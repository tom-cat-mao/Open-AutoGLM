"""Capability-owned assembly for the v2 thin loop.

The registry remains the public status/audit plane, but its lifecycle hooks now
mount every optional capability through five declared seams.  The concrete
context records each registration under the currently applying ``cap_id`` so a
later release can remove the whole contribution without guessing object names.

This is intentionally a static assembly layer.  Reconciliation is useful while
building agents and in tests/consoles, but it does not mutate a compiled agent's
tool table while a run is in progress.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
import re
from typing import Any, Protocol, runtime_checkable

CapabilityHook = Callable[..., None]
PromptProvider = Callable[..., Any]
RunHook = Callable[..., Any]
CliHandler = Callable[..., Any]
_CAPABILITY_ID = re.compile(r"^[a-z][a-z0-9_]*$")
_RUN_HOOK_WHEN = frozenset({"start", "end"})

# Preserve the pre-WP-C2 middleware ordering while allowing each capability to
# register independently.  Core harness middleware occupies the gaps through
# ``register_core_middleware(order=...)``.
_MIDDLEWARE_ORDER = {
    "taskdoc": 10,
    "compact": 20,
    "budget": 40,
    "safety": 90,
}
_RUN_HOOK_ORDER = {
    "start": {
        "taskdoc": 10,
        "app_kb": 20,
        "experience": 35,
        "recall": 40,
    },
    "end": {
        "recall": 40,
        "experience": 50,
        "dream": 90,
    },
}


@runtime_checkable
class CapabilityContext(Protocol):
    """The only five seams through which a capability may affect assembly."""

    def register_middleware(self, middleware: Any) -> None: ...

    def register_tool(self, tool: Any) -> None: ...

    def add_prompt_block(self, provider: PromptProvider) -> None: ...

    def add_run_hook(self, when: str, fn: RunHook) -> None: ...

    def add_cli_command(self, name: str, handler: CliHandler) -> None: ...


@dataclass(frozen=True)
class PromptBlock:
    """One provider result and where it belongs in the initial messages."""

    content: str
    placement: str = "system_message"

    def __post_init__(self) -> None:
        if self.placement not in {"system_suffix", "system_message"}:
            raise ValueError(f"invalid prompt block placement: {self.placement!r}")


@dataclass(frozen=True)
class CapabilitySpec:
    """Stable identity, configured mode, dependencies, and lifecycle hooks."""

    cap_id: str
    title: str
    mode: str
    deps: tuple[str, ...] = ()
    apply: CapabilityHook | None = None
    release: CapabilityHook | None = None

    def __post_init__(self) -> None:
        if not _CAPABILITY_ID.fullmatch(self.cap_id):
            raise ValueError(f"invalid capability id: {self.cap_id!r}")
        if not str(self.title).strip():
            raise ValueError("capability title must not be empty")
        if not str(self.mode).strip():
            raise ValueError("capability mode must not be empty")
        for dependency in self.deps:
            if not _CAPABILITY_ID.fullmatch(dependency):
                raise ValueError(f"invalid dependency id: {dependency!r}")
        if self.apply is not None and not callable(self.apply):
            raise TypeError("capability apply hook must be callable")
        if self.release is not None and not callable(self.release):
            raise TypeError("capability release hook must be callable")


class CapabilityRegistry:
    """Insertion-ordered registry with fail-visible dependency status."""

    def __init__(self) -> None:
        self._specs: dict[str, CapabilitySpec] = {}

    def register(self, spec: CapabilitySpec) -> None:
        """Register one stable id, rejecting accidental replacement."""

        if not isinstance(spec, CapabilitySpec):
            raise TypeError("spec must be a CapabilitySpec")
        if spec.cap_id in self._specs:
            raise ValueError(f"capability already registered: {spec.cap_id}")
        self._specs[spec.cap_id] = spec

    def specs(self) -> tuple[CapabilitySpec, ...]:
        """Return the immutable insertion-ordered composition."""

        return tuple(self._specs.values())

    def status(self) -> list[dict[str, Any]]:
        """Return deterministic status rows without applying effects.

        ``off`` takes precedence for the capability itself.  A non-off
        capability is ``pending`` when an immediate dependency is absent, off,
        or itself pending.  A ready ``shadow`` mode remains visible as shadow;
        every other ready non-off mode is active.
        """

        states: dict[str, str] = {}

        def resolve(cap_id: str, resolving: frozenset[str] = frozenset()) -> str:
            if cap_id in states:
                return states[cap_id]
            spec = self._specs.get(cap_id)
            if spec is None or cap_id in resolving:
                return "pending"
            mode = str(spec.mode).strip().lower()
            if mode == "off":
                states[cap_id] = "off"
                return "off"
            nested = resolving | {cap_id}
            missing = [
                dependency
                for dependency in spec.deps
                if resolve(dependency, nested) in {"off", "pending"}
            ]
            state = "pending" if missing else "shadow" if mode == "shadow" else "active"
            states[cap_id] = state
            return state

        rows: list[dict[str, Any]] = []
        for cap_id, spec in self._specs.items():
            state = resolve(cap_id)
            missing_deps = [
                dependency
                for dependency in spec.deps
                if resolve(dependency, frozenset({cap_id})) in {"off", "pending"}
            ]
            rows.append(
                {
                    "cap_id": cap_id,
                    "title": spec.title,
                    "mode": str(spec.mode).strip().lower(),
                    "state": state,
                    "missing_deps": missing_deps if state == "pending" else [],
                }
            )
        return rows


@dataclass(frozen=True)
class _Mount:
    owner: str
    value: Any
    sequence: int
    order: int
    replace_key: str | None = None


@dataclass(frozen=True)
class MiddlewareReplacement:
    """Mount ``middleware`` into an existing ordered core slot."""

    middleware: Any
    replace_key: str


class CapabilityAssemblyContext:
    """Capability-owned mount ledger for the five assembly seams."""

    def __init__(self, services: Mapping[str, Any] | None = None) -> None:
        self._services = dict(services or {})
        self._current_cap_id: str | None = None
        self._sequence = 0
        self._middleware: list[_Mount] = []
        self._tools: list[_Mount] = []
        self._prompt_blocks: list[_Mount] = []
        self._run_hooks: dict[str, list[_Mount]] = {"start": [], "end": []}
        self._cli_commands: list[_Mount] = []
        self._mounted: dict[str, tuple[str, CapabilitySpec]] = {}
        self._capability_order: dict[str, int] = {}

    @property
    def current_cap_id(self) -> str | None:
        return self._current_cap_id

    def service(self, name: str, default: Any = None) -> Any:
        """Return a harness-supplied dependency without making it a seam."""

        return self._services.get(name, default)

    def set_service(self, name: str, value: Any) -> None:
        self._services[name] = value

    @contextmanager
    def applying(self, cap_id: str):
        previous = self._current_cap_id
        self._current_cap_id = cap_id
        try:
            yield
        finally:
            self._current_cap_id = previous

    def _owner(self) -> str:
        if self._current_cap_id is None:
            raise RuntimeError("capability registration requires an active cap_id")
        return self._current_cap_id

    def _mount(
        self,
        target: list[_Mount],
        value: Any,
        order: int,
        *,
        replace_key: str | None = None,
    ) -> None:
        self._sequence += 1
        target.append(_Mount(self._owner(), value, self._sequence, order, replace_key))

    def register_middleware(self, middleware: Any) -> None:
        owner = self._owner()
        replace_key = None
        if isinstance(middleware, MiddlewareReplacement):
            replace_key = middleware.replace_key
            middleware = middleware.middleware
        self._mount(
            self._middleware,
            middleware,
            _MIDDLEWARE_ORDER.get(owner, 50),
            replace_key=replace_key,
        )

    def register_tool(self, tool: Any) -> None:
        self._mount(self._tools, tool, 100 + self._capability_order.get(self._owner(), 0))

    def add_prompt_block(self, provider: PromptProvider) -> None:
        if not callable(provider):
            raise TypeError("prompt block provider must be callable")
        self._mount(
            self._prompt_blocks,
            provider,
            100 + self._capability_order.get(self._owner(), 0),
        )

    def add_run_hook(self, when: str, fn: RunHook) -> None:
        if when not in _RUN_HOOK_WHEN:
            raise ValueError(f"invalid run hook phase: {when!r}")
        if not callable(fn):
            raise TypeError("run hook must be callable")
        owner = self._owner()
        self._mount(
            self._run_hooks[when],
            fn,
            _RUN_HOOK_ORDER[when].get(owner, 50),
        )

    def add_cli_command(self, name: str, handler: CliHandler) -> None:
        clean = str(name).strip()
        if not clean or not callable(handler):
            raise ValueError("CLI command requires a name and callable handler")
        self._mount(
            self._cli_commands,
            (clean, handler),
            100 + self._capability_order.get(self._owner(), 0),
        )

    # Core harness products use the same ordered collections, but cannot be
    # released by a capability because their owner is outside the cap_id space.
    def register_core_middleware(
        self, middleware: Any, *, order: int, replace_key: str | None = None
    ) -> None:
        self._sequence += 1
        self._middleware.append(
            _Mount("__core__", middleware, self._sequence, order, replace_key)
        )

    def register_core_tool(self, tool: Any, *, order: int) -> None:
        self._sequence += 1
        self._tools.append(_Mount("__core__", tool, self._sequence, order))

    def add_core_run_hook(self, when: str, fn: RunHook, *, order: int) -> None:
        if when not in _RUN_HOOK_WHEN:
            raise ValueError(f"invalid run hook phase: {when!r}")
        self._sequence += 1
        self._run_hooks[when].append(_Mount("__core__", fn, self._sequence, order))

    @staticmethod
    def _ordered(mounts: Sequence[_Mount]) -> list[_Mount]:
        return sorted(mounts, key=lambda item: (item.order, item.sequence))

    @property
    def middleware(self) -> list[Any]:
        result: list[Any] = []
        positions: dict[str, int] = {}
        for mount in self._ordered(self._middleware):
            key = mount.replace_key
            if key is not None and key in positions:
                result[positions[key]] = mount.value
            else:
                if key is not None:
                    positions[key] = len(result)
                result.append(mount.value)
        return result

    @property
    def tools(self) -> list[Any]:
        # A capability may replace a same-named baseline tool (finish verifier).
        # Release removes the upper mount and reveals the untouched baseline at
        # its original position.
        result: list[Any] = []
        positions: dict[str, int] = {}
        for mount in self._ordered(self._tools):
            tool = mount.value
            name = str(getattr(tool, "name", ""))
            if name and name in positions:
                result[positions[name]] = tool
            else:
                if name:
                    positions[name] = len(result)
                result.append(tool)
        return result

    @property
    def prompt_providers(self) -> list[PromptProvider]:
        return [mount.value for mount in self._ordered(self._prompt_blocks)]

    def run_hooks(self, when: str) -> list[RunHook]:
        if when not in _RUN_HOOK_WHEN:
            raise ValueError(f"invalid run hook phase: {when!r}")
        return [mount.value for mount in self._ordered(self._run_hooks[when])]

    @property
    def cli_commands(self) -> dict[str, CliHandler]:
        commands: dict[str, CliHandler] = {}
        for mount in self._ordered(self._cli_commands):
            name, handler = mount.value
            commands[name] = handler
        return commands

    def owned_values(self, cap_id: str, seam: str) -> list[Any]:
        """Expose owned products for agent compatibility attributes/tests."""

        collections = {
            "middleware": self._middleware,
            "tool": self._tools,
            "prompt": self._prompt_blocks,
            "run_start": self._run_hooks["start"],
            "run_end": self._run_hooks["end"],
            "cli": self._cli_commands,
        }
        if seam not in collections:
            raise ValueError(f"unknown capability seam: {seam}")
        return [mount.value for mount in collections[seam] if mount.owner == cap_id]

    def release_capability(self, cap_id: str) -> None:
        """Remove every registration owned by ``cap_id`` from all seams."""

        self._middleware = [item for item in self._middleware if item.owner != cap_id]
        self._tools = [item for item in self._tools if item.owner != cap_id]
        self._prompt_blocks = [
            item for item in self._prompt_blocks if item.owner != cap_id
        ]
        for when in _RUN_HOOK_WHEN:
            self._run_hooks[when] = [
                item for item in self._run_hooks[when] if item.owner != cap_id
            ]
        self._cli_commands = [
            item for item in self._cli_commands if item.owner != cap_id
        ]
        self._mounted.pop(cap_id, None)


def _register_factory(
    ctx: CapabilityAssemblyContext,
    service: str,
    seam: str,
    *,
    fail_open: bool = False,
) -> None:
    factory = ctx.service(service)
    if not callable(factory):
        return
    try:
        value = factory()
    except Exception:
        if fail_open:
            return
        raise
    if value is None:
        return
    getattr(ctx, seam)(value)


def _register_service_hook(
    ctx: CapabilityAssemblyContext, when: str, service: str
) -> None:
    hook = ctx.service(service)
    if callable(hook):
        ctx.add_run_hook(when, hook)


def _register_prompt(ctx: CapabilityAssemblyContext, service: str) -> None:
    provider = ctx.service(service)
    if callable(provider):
        ctx.add_prompt_block(provider)


def _register_cli(ctx: CapabilityAssemblyContext, names: Sequence[str]) -> None:
    handlers = ctx.service("cli_handlers", {})
    if not isinstance(handlers, Mapping):
        return
    for name in names:
        handler = handlers.get(name)
        if callable(handler):
            ctx.add_cli_command(name, handler)


def _apply_taskdoc(ctx: CapabilityAssemblyContext) -> None:
    _register_factory(
        ctx,
        "taskdoc_middleware_factory",
        "register_middleware",
        fail_open=True,
    )
    _register_factory(ctx, "taskdoc_tool_factory", "register_tool")
    _register_service_hook(ctx, "start", "taskdoc_run_start")


def _apply_safety(ctx: CapabilityAssemblyContext) -> None:
    _register_factory(ctx, "safety_middleware_factory", "register_middleware")


def _apply_budget(ctx: CapabilityAssemblyContext) -> None:
    _register_factory(ctx, "budget_middleware_factory", "register_middleware")


def _apply_compact(ctx: CapabilityAssemblyContext) -> None:
    _register_factory(
        ctx,
        "compact_middleware_factory",
        "register_middleware",
        fail_open=True,
    )


def _apply_finish_verify(ctx: CapabilityAssemblyContext) -> None:
    _register_factory(ctx, "finish_verify_tool_factory", "register_tool")


def _apply_deliverable(ctx: CapabilityAssemblyContext) -> None:
    factory = ctx.service("deliverable_tools_factory")
    if callable(factory):
        tools = factory()
        for tool in tools or ():
            ctx.register_tool(tool)
    _register_prompt(ctx, "deliverable_prompt_provider")


def _apply_app_kb(ctx: CapabilityAssemblyContext) -> None:
    _register_service_hook(ctx, "start", "app_kb_run_start")
    _register_prompt(ctx, "app_kb_prompt_provider")


def _apply_dream(ctx: CapabilityAssemblyContext) -> None:
    _register_service_hook(ctx, "end", "dream_run_end")
    _register_cli(ctx, ("dream",))


def _apply_experience(ctx: CapabilityAssemblyContext) -> None:
    _register_service_hook(ctx, "start", "experience_run_start")
    _register_service_hook(ctx, "end", "experience_run_end")


def _apply_recall(ctx: CapabilityAssemblyContext) -> None:
    _register_service_hook(ctx, "start", "recall_run_start")
    _register_service_hook(ctx, "end", "recall_run_end")
    _register_prompt(ctx, "recall_prompt_provider")
    _register_cli(
        ctx,
        (
            "rebuild_vec",
            "distill",
            "review_lessons",
            "approve_lesson",
            "revoke_lesson",
            "supersede_lesson",
        ),
    )


def _owned_apply(cap_id: str, hook: CapabilityHook) -> CapabilityHook:
    """Bind a public lifecycle hook to its stable ownership identity."""

    def apply(ctx: CapabilityAssemblyContext) -> None:
        with ctx.applying(cap_id):
            hook(ctx)

    return apply


def _owned_release(cap_id: str) -> CapabilityHook:
    def release(ctx: CapabilityAssemblyContext) -> None:
        ctx.release_capability(cap_id)

    return release


def assemble_capabilities(
    registry: CapabilityRegistry, ctx: CapabilityAssemblyContext
) -> CapabilityAssemblyContext:
    """Reconcile ``ctx`` to active/shadow specs using a cap-id/mode diff.

    Removed and changed-mode capabilities release first; changed/new specs then
    apply in registry order.  ``pending`` and ``off`` specs never apply.
    """

    if not isinstance(registry, CapabilityRegistry):
        raise TypeError("registry must be a CapabilityRegistry")
    if not isinstance(ctx, CapabilityAssemblyContext):
        raise TypeError("ctx must be a CapabilityAssemblyContext")

    specs = {spec.cap_id: spec for spec in registry.specs()}
    rows = {str(row["cap_id"]): row for row in registry.status()}
    desired = {
        cap_id: (str(row["mode"]), specs[cap_id])
        for cap_id, row in rows.items()
        if row["state"] in {"active", "shadow"}
    }
    ctx._capability_order = {
        spec.cap_id: index for index, spec in enumerate(registry.specs())
    }

    for cap_id, (old_mode, old_spec) in reversed(tuple(ctx._mounted.items())):
        next_item = desired.get(cap_id)
        if next_item is not None and next_item[0] == old_mode:
            continue
        try:
            with ctx.applying(cap_id):
                if old_spec.release is not None:
                    old_spec.release(ctx)
        finally:
            # Enforce zero residue even if a custom release hook is incomplete.
            ctx.release_capability(cap_id)
            ctx._mounted.pop(cap_id, None)

    for spec in registry.specs():
        item = desired.get(spec.cap_id)
        if item is None or spec.cap_id in ctx._mounted:
            continue
        mode, active_spec = item
        try:
            with ctx.applying(spec.cap_id):
                if active_spec.apply is not None:
                    active_spec.apply(ctx)
        except Exception:
            ctx.release_capability(spec.cap_id)
            raise
        ctx._mounted[spec.cap_id] = (mode, active_spec)
    return ctx


def build_capability_registry(config: Any) -> CapabilityRegistry:
    """Build the ten-capability composition shared by agent and runner."""

    registry = CapabilityRegistry()
    for spec in (
        CapabilitySpec(
            "taskdoc",
            "TaskDoc",
            "on" if getattr(config, "taskdoc_enabled", True) else "off",
            apply=_owned_apply("taskdoc", _apply_taskdoc),
            release=_owned_release("taskdoc"),
        ),
        CapabilitySpec(
            "safety",
            "Safety",
            getattr(config, "safety_mode", "wary"),
            apply=_owned_apply("safety", _apply_safety),
            release=_owned_release("safety"),
        ),
        CapabilitySpec(
            "budget",
            "Token budget",
            "on",
            apply=_owned_apply("budget", _apply_budget),
            release=_owned_release("budget"),
        ),
        CapabilitySpec(
            "compact",
            "Auto compact",
            "on" if getattr(config, "compact_enabled", True) else "off",
            apply=_owned_apply("compact", _apply_compact),
            release=_owned_release("compact"),
        ),
        CapabilitySpec(
            "finish_verify",
            "Finish verifier",
            getattr(config, "finish_verify", "auto"),
            apply=_owned_apply("finish_verify", _apply_finish_verify),
            release=_owned_release("finish_verify"),
        ),
        CapabilitySpec(
            "deliverable",
            "HTML deliverable",
            "on" if getattr(config, "deliverable_enabled", True) else "off",
            apply=_owned_apply("deliverable", _apply_deliverable),
            release=_owned_release("deliverable"),
        ),
        CapabilitySpec(
            "app_kb",
            "App knowledge",
            "on" if getattr(config, "app_kb_enabled", True) else "off",
            apply=_owned_apply("app_kb", _apply_app_kb),
            release=_owned_release("app_kb"),
        ),
        CapabilitySpec(
            "dream",
            "Memory maintenance",
            getattr(config, "dream_mode", "manual"),
            deps=("app_kb",),
            apply=_owned_apply("dream", _apply_dream),
            release=_owned_release("dream"),
        ),
        CapabilitySpec(
            "experience",
            "Experience plane",
            "on" if getattr(config, "experience_enabled", True) else "off",
            apply=_owned_apply("experience", _apply_experience),
            release=_owned_release("experience"),
        ),
        CapabilitySpec(
            "recall",
            "Memory recall",
            getattr(config, "memory_rag", "shadow"),
            deps=("experience",),
            apply=_owned_apply("recall", _apply_recall),
            release=_owned_release("recall"),
        ),
    ):
        registry.register(spec)
    return registry


__all__ = [
    "CapabilityAssemblyContext",
    "CapabilityContext",
    "CapabilityRegistry",
    "CapabilitySpec",
    "MiddlewareReplacement",
    "PromptBlock",
    "assemble_capabilities",
    "build_capability_registry",
]
