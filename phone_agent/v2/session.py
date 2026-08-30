"""v2 PhoneSession: device-side run state, screenshots, marks, and locate.

One :class:`PhoneSession` holds the mutable device-side state for a single run.
Tools reach the device and the current-screen marks exclusively through it. All
device I/O goes through :class:`~phone_agent.device_factory.DeviceFactory`
(P0: no direct ADB), and marks come from the grounding provider stack.

See ``docs/refactor-thin-loop-v2.md`` §6 for the binding contract.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from phone_agent.device_factory import DeviceFactory, get_device_factory
from phone_agent.grounding.accessibility import AccessibilityTreeProvider
from phone_agent.grounding.factory import build_locate_provider
from phone_agent.grounding.provider import (
    MarkCandidate,
    MarkProviderHint,
    ScreenBinding,
)
from phone_agent.v2.coords import convert_relative_to_absolute
from phone_agent.v2.locate_scope import (
    ScopeCrop,
    build_scope_crop,
    interval_region_1000,
    is_container_like,
)

if TYPE_CHECKING:
    from phone_agent.adb.screenshot import Screenshot
    from phone_agent.config.app_registry import ForegroundAppObservation
    from phone_agent.grounding.provider import MarkProvider
    from phone_agent.v2.config import V2Config
    from phone_agent.v2.appkb import AppKnowledge, AppKnowledgeStore
    from phone_agent.v2.taskdoc import TaskDoc
    from phone_agent.v2.usage import UsageLedger


class ScreenshotError(RuntimeError):
    """Raised when the device screenshot is unavailable / invalid."""


class StaleMarkError(RuntimeError):
    """Raised when a requested mark_id is not in the current-screen marks."""


class LocateAmbiguousError(RuntimeError):
    """Raised when visual locate returns zero or multiple confident candidates."""

    def __init__(
        self,
        message: str,
        *,
        candidates: list[MarkCandidate] | None = None,
        failure_code: str = "no_candidate",
    ) -> None:
        super().__init__(message)
        self.candidates = candidates or []
        self.failure_code = failure_code


# U1 batch-badge separator: external mark ids are ``<provider_id>@e<epoch>``
# (e.g. ``ax_1@e12``). The provider-internal id (``ax_1``) is kept only as
# provenance; every id the model ever sees carries the batch suffix so a stale
# reference is structurally detectable in ``resolve_mark``.
_BADGE_SEP = "@e"


def mint_badge(provider_id: str, epoch: int) -> str:
    """Return the external badged mark id for a provider id in a given batch."""

    return f"{provider_id}{_BADGE_SEP}{int(epoch)}"


def parse_badge(mark_id: str) -> tuple[str, int | None]:
    """Split an external badged id into ``(provider_id, epoch)``.

    An id without the ``@e`` suffix (or a non-integer suffix) yields an epoch of
    ``None`` so callers can treat it as unbadged / structurally stale.
    """

    base, sep, suffix = str(mark_id or "").rpartition(_BADGE_SEP)
    if not sep:
        return mark_id, None
    try:
        return base, int(suffix)
    except ValueError:
        return mark_id, None


@dataclass
class Observation:
    """Node-local observation snapshot; never serialized to trace/checkpoint."""

    screenshot_b64: str
    width: int
    height: int
    current_app: str
    marks: list[MarkCandidate]
    screen_seq: int
    # U1 observation batch this frame was produced in (session.epoch after the
    # successful atomic capture). Every mark in ``marks`` carries the same epoch.
    epoch: int = 0
    # Short sha256 of the screenshot base64 payload, kept on the Observation for
    # screen binding / audit only (U3 removed the seen_states stagnation set; A4
    # removed image dedup).
    screen_hash: str = ""
    # Screenshot mime type (``image/png`` | ``image/jpeg``) for the data: URL.
    mime_type: str = "image/png"


class PhoneSession:
    """Device-side state for one run. Tools access device + marks through it."""

    def __init__(
        self,
        config: "V2Config",
        device_factory: DeviceFactory | None = None,
    ) -> None:
        self.config = config
        self.device_factory = device_factory or get_device_factory()
        # Shared per-run model-cost ledger. ThinPhoneAgent installs the concrete
        # ledger after constructing the session; the slot always exists for side
        # calls and duck-typed integrations to probe safely.
        self.usage_ledger: "UsageLedger | None" = None
        # App-KB is an optional enhancement. Keep stable public slots but defer
        # imports and filesystem creation until sync or prompt lookup needs it.
        self.app_store: "AppKnowledgeStore | None" = None
        self.app_knowledge: "AppKnowledge | None" = None
        # Cached device serial resolved via adb when config.device_id is unset
        # (the single-device default); None means "not resolved yet" (retried).
        self._kb_serial: str | None = None
        self.marks: dict[str, MarkCandidate] = {}
        self.screen_seq: int = 0
        # U1 observation batch counter ("work-badge epoch"). Bumped once per
        # successful ``observe()``; every mark minted in that batch carries the
        # counter in its external id (``ax_1@e12``) and in ``MarkCandidate.epoch``.
        # A fresh observation invalidates every prior badge; an observation
        # *failure* clears ``marks`` entirely (no stale authority survives).
        self.epoch: int = 0
        self.finished: bool = False
        self.finish_summary: str | None = None
        self.takeover_reason: str | None = None
        # finish two-step review (S2 §1.2): the first finish() call emits a world
        # mirror (review packet) and sets finish_reviewed=True at finish_review_seq;
        # confirm=True only lands finished when the review is still valid
        # (screen_seq == finish_review_seq — any intervening observe invalidates it).
        # finish_dispute_count tallies verifier rejections (reserved for next relay).
        self.last_tool_ok: bool | None = None
        self.finish_reviewed: bool = False
        self.finish_review_seq: int = -1
        self.finish_dispute_count: int = 0
        # Hard-contradiction labels captured by the most recent review packet
        # (review.py). The finish verifier trigger (S2 §4.1.2) reads this to
        # detect "hard contradiction + model still confirms".
        self.finish_hard_doubts: list[str] = []
        # TaskDoc (task board) state: doc is harness-seeded at run start (agent.py).
        # The model is its sole writer via update_task_doc; the pinned render +
        # transcript-derived flow line live in middleware/taskdoc.py (U3 removed the
        # seen_states/nudged stagnation machinery — no session state backs it now).
        self.task_doc: "TaskDoc | None" = None
        # lazy visual locate provider (singleton per session)
        self._locate_provider: "MarkProvider | None" = None
        self._locate_provider_built: bool = False
        # Frame the last locate() ran its visual model on (U1 same-frame return):
        # the locate tool renders text + this screenshot instead of re-observing.
        self._last_locate_shot: "Screenshot | None" = None
        self._last_locate_app: str = "unknown"
        self._last_locate_metadata: dict[str, Any] = {}
        # last-known dimensions for coordinate conversion
        self._last_width: int = 0
        self._last_height: int = 0

    # -- app knowledge ---------------------------------------------------

    def _kb_device_id(self) -> str | None:
        """Device namespace for App-KB: configured serial, else the live one.

        ``config.device_id`` wins; when unset (single-device default) the serial
        is resolved once via the device layer and cached. Resolution failures
        return None (retried next call) so a transient adb hiccup disables
        nothing permanently.
        """

        serial = getattr(self.config, "device_id", None) or self._kb_serial
        if serial:
            return serial
        getter = getattr(self.device_factory, "get_serial_number", None)
        if callable(getter):
            try:
                serial = getter(None)
            except Exception:  # noqa: BLE001 - best-effort serial resolution
                serial = None
        if serial:
            self._kb_serial = serial
        return serial

    def _ensure_app_knowledge(
        self,
    ) -> tuple["AppKnowledgeStore | None", "AppKnowledge | None"]:
        """Lazily open the local App-KB; setup failures degrade to off."""

        if not getattr(self.config, "app_kb_enabled", True):
            return None, None
        if self.app_store is not None and self.app_knowledge is not None:
            return self.app_store, self.app_knowledge
        try:
            from phone_agent.v2.appkb import AppKnowledge, AppKnowledgeStore

            store = AppKnowledgeStore(
                str(getattr(self.config, "memory_dir", "memory"))
            )
            knowledge = AppKnowledge(
                store, device_id=self._kb_device_id()
            )
        except Exception:  # noqa: BLE001 - memory is an optional enhancement
            self.app_store = None
            self.app_knowledge = None
            return None, None
        self.app_store = store
        self.app_knowledge = knowledge
        return store, knowledge

    def sync_app_knowledge(self) -> bool:
        """Refresh device-scoped App-KB facts from launchable app labels.

        Device access, an absent explicit serial, or local-store errors all fail
        open: the run continues and persisted global knowledge remains usable.
        """

        if not getattr(self.config, "app_kb_enabled", True):
            return False
        try:
            store, _knowledge = self._ensure_app_knowledge()
            if store is None:
                return False
            labels = self.device_factory.get_app_labels(self.config.device_id)
            serial = self._kb_device_id()
            if not labels or not serial:
                return False
            store.sync_device(
                serial,
                [(entry.package, entry.label) for entry in labels],
            )
            return True
        except Exception:  # noqa: BLE001 - App-KB must never block run start
            return False

    def app_list_for_prompt(self, max_n: int) -> str:
        """Return bounded canonical labels, device-scope before global."""

        if not getattr(self.config, "app_kb_enabled", True):
            return ""
        try:
            limit = int(max_n)
        except (TypeError, ValueError):
            return ""
        if limit <= 0:
            return ""
        store, _knowledge = self._ensure_app_knowledge()
        if store is None:
            return ""
        try:
            device_id = self._kb_device_id()
            device_entries = (
                store.entries(scope=f"device:{device_id}") if device_id else []
            )
            global_entries = store.entries(scope="global")
        except Exception:  # noqa: BLE001 - prompt enrichment is fail-open
            return ""

        def rank(entry: dict[str, Any]) -> tuple[int, str, str]:
            return (
                -int(entry.get("success_count", 0)),
                str(entry.get("label", "")).casefold(),
                str(entry.get("package", "")),
            )

        labels: list[str] = []
        seen: set[str] = set()
        ordered = [
            *sorted(device_entries, key=rank),
            *sorted(global_entries, key=rank),
        ]
        for entry in ordered:
            label = str(entry.get("label", "")).strip()
            key = label.casefold()
            if not label or key in seen:
                continue
            seen.add(key)
            labels.append(label)

        if not labels:
            return ""
        rendered = "，".join(labels[:limit])
        if len(labels) > limit:
            rendered += f"，…等 {len(labels)} 个，可用 launch_app 尝试其它名称"
        return rendered

    # -- device state -----------------------------------------------------

    def screenshot(self) -> "Screenshot":
        """Capture a device screenshot; raise ScreenshotError if invalid."""

        shot = self.device_factory.get_screenshot(self.config.device_id)
        if not getattr(shot, "is_valid", False):
            code = getattr(shot, "failure_code", None) or "screenshot_unavailable"
            raise ScreenshotError(f"screenshot invalid: {code}")
        self._last_width = int(shot.width)
        self._last_height = int(shot.height)
        return shot

    def refresh_marks(self, shot: "Screenshot | None" = None) -> list[MarkCandidate]:
        """Produce accessibility marks for a screenshot; never raises.

        U1 single-producer discipline: ``observe()`` passes the screenshot it
        already captured so marks are extracted against *that* frame — no second
        screenshot is taken. When ``shot`` is ``None`` (a bare external call) one
        is captured for backward compatibility. Failure (bad screenshot, provider
        error, empty dump) returns ``[]``; the caller decides how to degrade.
        """

        if shot is None:
            try:
                shot = self.screenshot()
            except ScreenshotError:
                return []
        try:
            binding = self._screen_binding(shot)
            provider = AccessibilityTreeProvider(
                dump_tree=self._dump_tree,
                max_marks=self.config.accessibility_max_marks,
            )
            result = provider.provide_marks(
                shot,
                binding,
                hints=None,
                timeout=self.config.accessibility_timeout,
            )
        except Exception:
            return []
        if not result.success:
            return []
        return list(result.marks)

    def observe(self) -> Observation:
        """Atomic single-producer observation (U1).

        One consistent frame is assembled in a single sampling window:
        foreground-before → screenshot → accessibility dump (marks, reusing that
        one screenshot) → foreground-after. If the foreground component changed
        between the before/after brackets the frame is inconsistent (the screen
        moved mid-capture): the whole window is retried **once**. A second
        instability is an observation failure — :class:`ScreenshotError` is raised
        and the entire mark batch is invalidated (``marks`` cleared, no epoch
        bump) so no stale addressing authority survives a failed observation.

        On success the batch counter (``epoch``) increments and every mark is
        minted with the batch badge (``ax_1@e<epoch>``); the provider-internal id
        stays as the pre-badge prefix (provenance only).
        """

        last_error: Exception | None = None
        for _attempt in range(2):
            try:
                before = self._foreground_observation()
                shot = self.screenshot()
                marks = self.refresh_marks(shot)
                after = self._foreground_observation()
            except ScreenshotError as exc:
                last_error = exc
                continue
            before_c = self._component_of(before)
            after_c = self._component_of(after)
            if before_c is not None and after_c is not None and before_c != after_c:
                # Screen moved mid-capture: marks/screenshot may disagree. Retry.
                last_error = ScreenshotError(
                    f"observation unstable: foreground {before_c!r} -> {after_c!r}"
                )
                continue
            # The ``after`` sample is closest to the committed frame — use it for
            # the display label so the label matches the bracket that verified
            # stability (no extra device round-trip outside the window).
            return self._commit_observation(shot, marks, foreground=after)

        # Two unstable/invalid attempts: fail closed and drop the whole batch.
        self._invalidate_batch()
        raise last_error or ScreenshotError("observation failed")

    def _commit_observation(
        self,
        shot: "Screenshot",
        marks: list[MarkCandidate],
        *,
        foreground: "ForegroundAppObservation | None" = None,
    ) -> Observation:
        """Bump the batch, mint badged marks, and build the Observation."""

        current_app = self._label_of(foreground)
        self.screen_seq += 1
        self.epoch += 1
        minted = self._mint_marks(marks)
        self.marks = {mark.mark_id: mark for mark in minted}
        # screen_hash: short sha256 of the screenshot payload, audit/binding only.
        screen_hash = hashlib.sha256(
            (shot.base64_data or "").encode("utf-8")
        ).hexdigest()[:16]
        return Observation(
            screenshot_b64=shot.base64_data,
            width=int(shot.width),
            height=int(shot.height),
            current_app=current_app,
            marks=minted,
            screen_seq=self.screen_seq,
            epoch=self.epoch,
            screen_hash=screen_hash,
            mime_type=getattr(shot, "mime_type", None) or "image/png",
        )

    def _invalidate_batch(self) -> None:
        """Drop every current-batch mark (no stale authority after a failure)."""

        self.marks = {}

    # -- mark resolution --------------------------------------------------

    def _mint_marks(self, marks: list[MarkCandidate]) -> list[MarkCandidate]:
        """Stamp raw provider marks with the current batch badge.

        Each mark's external id becomes ``<provider_id>@e<epoch>`` and its
        ``epoch`` field is set to ``self.epoch``. The provider-internal id is
        preserved via ``source``-independent provenance (the pre-badge id is the
        prefix before ``@e``). Returns the re-stamped list; ``self.marks`` is the
        caller's responsibility to rebuild.
        """

        minted: list[MarkCandidate] = []
        for mark in marks:
            badged_id = mint_badge(mark.mark_id, self.epoch)
            minted.append(replace(mark, mark_id=badged_id, epoch=self.epoch))
        return minted

    def resolve_mark(self, mark_id: str) -> MarkCandidate:
        """Return the current-batch mark for ``mark_id`` or raise StaleMarkError.

        Freshness gate (U1): a badged id from a superseded batch (its ``@e``
        epoch != the session's current ``epoch``) is rejected *before* the marks
        lookup, so a stale reference never resolves even if a same-provider-id
        mark happens to exist in the new batch.
        """

        _base, badge_epoch = parse_badge(mark_id)
        if badge_epoch is not None and badge_epoch != self.epoch:
            raise StaleMarkError(
                f"mark {mark_id!r} is from batch e{badge_epoch}, current batch is "
                f"e{self.epoch}; re-observe (read_screen) and use a fresh mark id"
            )
        mark = self.marks.get(mark_id)
        if mark is None:
            raise StaleMarkError(
                f"mark {mark_id!r} is not on the current screen; re-observe first"
            )
        return mark

    # -- screen geometry (tools read these for swipe/scroll math) ----------

    @property
    def screen_width(self) -> int:
        """Last observed screen width in px (0 before the first screenshot)."""

        return self._last_width

    @property
    def screen_height(self) -> int:
        """Last observed screen height in px (0 before the first screenshot)."""

        return self._last_height

    def relative_to_abs(self, rx: int, ry: int) -> tuple[int, int]:
        """Convert a 0-1000 relative point to absolute pixels on the real screen.

        Uses the last observed dimensions; captures a screenshot first when no
        dimensions are known yet (same fallback as ``mark_center_abs``).
        """

        width = self._last_width
        height = self._last_height
        if width <= 0 or height <= 0:
            shot = self.screenshot()
            width, height = int(shot.width), int(shot.height)
        return convert_relative_to_absolute([rx, ry], width, height)

    def mark_center_abs(self, mark: MarkCandidate) -> tuple[int, int]:
        """Convert a mark's 0-1000 center to absolute device pixels."""

        width = self._last_width
        height = self._last_height
        if width <= 0 or height <= 0:
            shot = self.screenshot()
            width, height = int(shot.width), int(shot.height)
        return convert_relative_to_absolute(list(mark.center), width, height)

    def locate(
        self,
        description: str,
        *,
        visible_text_hint: str | None = None,
        intent: str | None = None,
        scope_mark_id: str | None = None,
        scope_start_mark_id: str | None = None,
        scope_end_mark_id: str | None = None,
    ) -> MarkCandidate:
        """Visual deep-locate ``description`` -> one confident mark (fail-closed).

        The resolved mark is **minted into the current batch** (badged with the
        session's current ``epoch``) and registered into ``self.marks`` — no epoch
        bump, so it joins the batch produced by the last ``observe()`` rather than
        starting a new one. The single screenshot the visual model ran on is
        stashed (``_last_locate_shot`` / ``_last_locate_app``) so the locate tool
        can return that **same frame** without a second capture. Zero or multiple
        confident candidates raise :class:`LocateAmbiguousError`; nothing is
        registered and nothing executes.
        """

        self._last_locate_metadata = {}
        if scope_end_mark_id and not scope_start_mark_id:
            raise ValueError("scope_end_mark_id requires scope_start_mark_id")
        if scope_mark_id and (scope_start_mark_id or scope_end_mark_id):
            raise ValueError("scope_mark_id cannot be combined with interval scope")

        scope_mark = self.resolve_mark(scope_mark_id) if scope_mark_id else None
        scope_start = (
            self.resolve_mark(scope_start_mark_id) if scope_start_mark_id else None
        )
        scope_end = self.resolve_mark(scope_end_mark_id) if scope_end_mark_id else None

        provider = self._get_locate_provider()
        if provider is None:
            raise LocateAmbiguousError(
                "visual locate provider is unavailable",
                failure_code="provider_unavailable",
            )
        shot = self.screenshot()
        binding = self._screen_binding(shot)
        scope_crop: ScopeCrop | None = None
        provider_shot = shot
        if scope_mark is not None:
            region = scope_mark.bbox
        elif scope_start is not None:
            region = interval_region_1000(scope_start, scope_end)
        else:
            region = None
        if region is not None:
            scope_crop = build_scope_crop(
                shot,
                session=self,
                region_bbox_1000=region,
                padding_ratio=self.config.scope_padding_ratio,
            )
            provider_shot = scope_crop.crop

        hint = MarkProviderHint(
            text=description,
            source="tool",
            intent=intent,
            action="locate",
            visible_text_hint=visible_text_hint,
        )
        result = provider.provide_marks(
            provider_shot,
            binding,
            hints=[hint],
            timeout=self.config.accessibility_timeout,
            max_size=self.config.locate_max_size,
        )
        metadata = dict(getattr(result, "metadata", {}) or {})
        input_size = metadata.get("provider_input_size_px")
        if not input_size:
            input_w, input_h = int(provider_shot.width), int(provider_shot.height)
            tier = int(self.config.locate_max_size)
            if tier > 0 and max(input_w, input_h) > tier:
                scale = tier / max(input_w, input_h)
                input_w = max(1, round(input_w * scale))
                input_h = max(1, round(input_h * scale))
            input_size = [input_w, input_h]
        self._last_locate_metadata = {
            "provider_input_size_px": input_size,
            "full_frame_size_px": [int(shot.width), int(shot.height)],
            "scope_bbox_1000": list(scope_crop.bbox_1000) if scope_crop else None,
            "scope_mark_id": scope_mark_id,
            "scope_start_mark_id": scope_start_mark_id,
            "scope_end_mark_id": scope_end_mark_id,
        }
        executable = [mark for mark in result.marks if getattr(mark, "valid", True)]
        if not result.success or len(executable) == 0:
            failure_code = (
                "ambiguous"
                if getattr(result, "failure_code", None)
                == "grounding_ambiguous"
                else "no_candidate"
            )
            raise LocateAmbiguousError(
                f"no confident match for {description!r}",
                candidates=list(result.candidates),
                failure_code=failure_code,
            )
        if len(executable) > 1:
            raise LocateAmbiguousError(
                f"ambiguous match for {description!r}: {len(executable)} candidates",
                candidates=executable,
                failure_code="ambiguous",
            )
        raw = executable[0]
        if scope_crop is not None:
            full_bbox = scope_crop.map_box_to_full(raw.bbox)
            full_center = scope_crop.map_point_to_full(raw.center)
            raw = replace(
                raw,
                bbox=[int(round(value)) for value in full_bbox],
                center=[int(round(value)) for value in full_center],
            )
        minted = replace(
            raw, mark_id=mint_badge(raw.mark_id, self.epoch), epoch=self.epoch
        )
        self.marks[minted.mark_id] = minted
        # Stash the locate frame so the tool returns the same screenshot without
        # a fresh observe (single-producer: one screenshot per locate call).
        self._last_locate_shot = shot
        self._last_locate_app = self._foreground_label()
        return minted

    def last_locate_metadata(self) -> dict[str, Any]:
        """Return trace-safe metadata for the most recent locate query."""

        return dict(self._last_locate_metadata)

    def last_locate_frame(self) -> dict | None:
        """Return the stashed locate screenshot as a same-frame render payload.

        ``{"b64", "mime", "screen_seq", "app"}`` for the frame the most recent
        ``locate()`` ran its visual model on, or ``None`` if no locate has run
        (so the tool degrades to text-only rather than fabricating an image).
        """

        shot = self._last_locate_shot
        if shot is None or not getattr(shot, "base64_data", None):
            return None
        return {
            "b64": shot.base64_data,
            "mime": getattr(shot, "mime_type", None) or "image/png",
            "screen_seq": self.screen_seq,
            "app": self._last_locate_app,
        }

    # -- marks digest -----------------------------------------------------

    @staticmethod
    def format_marks_digest(marks: list[MarkCandidate], max_items: int = 40) -> str:
        """One line per mark: ``mark_id | role | text(<=32) | center``."""

        lines: list[str] = []
        for mark in marks[:max_items]:
            role = (mark.role or "?")[:24]
            if is_container_like(mark):
                role = f"[容器]{role}"
            text = (mark.text_summary or "").replace("\n", " ").strip()
            if len(text) > 32:
                text = text[:32]
            center = tuple(mark.center) if mark.center else ()
            lines.append(f"{mark.mark_id} | {role} | {text} | {center}")
        if len(marks) > max_items:
            lines.append(f"... (+{len(marks) - max_items} more)")
        return "\n".join(lines)

    # -- internals --------------------------------------------------------

    def _dump_tree(self, timeout: float | None = None) -> str:
        return self.device_factory.dump_uiautomator_xml(
            self.config.device_id, timeout=timeout
        )

    def _foreground_observation(self) -> "ForegroundAppObservation | None":
        """Sample the foreground once; ``None`` when it cannot be read.

        The atomic ``observe()`` window samples this twice (before/after the
        screenshot) so one device call yields both the stability component and
        the display label — no extra round-trip outside the window.
        """

        try:
            return self.device_factory.get_foreground_app(self.config.device_id)
        except Exception:
            return None

    @staticmethod
    def _component_of(foreground: "ForegroundAppObservation | None") -> str | None:
        """Component name used as the atomic-capture stability key.

        ``None`` (unsampled foreground) means "assume stable" so a device that
        never reports a foreground does not force an endless retry loop.
        """

        if foreground is None:
            return None
        component = getattr(foreground, "component_name", None)
        if component:
            return str(component)
        package = getattr(foreground, "package_name", None)
        return str(package) if package else None

    @staticmethod
    def _label_of(foreground: "ForegroundAppObservation | None") -> str:
        """Human display label for the observation (falls back to ``unknown``)."""

        if foreground is None:
            return "unknown"
        return (
            getattr(foreground, "display_name", None)
            or getattr(foreground, "package_name", None)
            or "unknown"
        )

    def _foreground_label(self) -> str:
        """Sample the foreground and return its display label (standalone call)."""

        return self._label_of(self._foreground_observation())

    def _screen_binding(self, shot: "Screenshot") -> ScreenBinding:
        raw_hash = hashlib.sha256(
            (shot.base64_data or "").encode("utf-8")
        ).hexdigest()[:16]
        return ScreenBinding(
            screen_id=f"screen_{self.screen_seq}",
            raw_screenshot_hash=raw_hash,
            width=int(shot.width),
            height=int(shot.height),
            current_app=None,
            observation_epoch=self.epoch,
        )

    def _get_locate_provider(self) -> "MarkProvider | None":
        if not self._locate_provider_built:
            cfg = {
                "grounding_provider_name": self.config.grounding_provider,
                "locateanything_max_size": self.config.locateanything_max_size,
                "locateanything_context_max_chars": (
                    self.config.locateanything_context_max_chars
                ),
            }
            if self.config.locateanything_model:
                cfg["grounding_model_path"] = self.config.locateanything_model
            self._locate_provider = build_locate_provider(cfg)
            self._locate_provider_built = True
        return self._locate_provider
