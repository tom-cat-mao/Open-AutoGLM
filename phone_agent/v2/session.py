"""v2 PhoneSession: device-side run state, screenshots, marks, and locate.

One :class:`PhoneSession` holds the mutable device-side state for a single run.
Tools reach the device and the current-screen marks exclusively through it. All
device I/O goes through :class:`~phone_agent.device_factory.DeviceFactory`
(P0: no direct ADB), and marks come from the grounding provider stack.

See ``docs/refactor-thin-loop-v2.md`` §6 for the binding contract.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

from phone_agent.device_factory import DeviceFactory, get_device_factory
from phone_agent.grounding.accessibility import AccessibilityTreeProvider
from phone_agent.grounding.factory import build_locate_provider
from phone_agent.grounding.provider import (
    MarkCandidate,
    MarkProviderHint,
    ScreenBinding,
)
from phone_agent.v2.coords import convert_relative_to_absolute

if TYPE_CHECKING:
    from phone_agent.adb.screenshot import Screenshot
    from phone_agent.grounding.provider import MarkProvider
    from phone_agent.v2.config import V2Config
    from phone_agent.v2.taskdoc import TaskDoc


class ScreenshotError(RuntimeError):
    """Raised when the device screenshot is unavailable / invalid."""


class StaleMarkError(RuntimeError):
    """Raised when a requested mark_id is not in the current-screen marks."""


class LocateAmbiguousError(RuntimeError):
    """Raised when visual locate returns zero or multiple confident candidates."""

    def __init__(self, message: str, *, candidates: list[MarkCandidate] | None = None) -> None:
        super().__init__(message)
        self.candidates = candidates or []


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
    # Short sha256 of the screenshot base64 payload. Recorded on the observation
    # for stagnation detection (``seen_states``) and screen binding; no longer
    # drives image dedup (A4 removed same-screen image suppression).
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
        # TaskDoc (task board) state: doc is harness-seeded at run start (agent.py);
        # seen_states tracks (current_app, screen_hash) tuples for stagnation
        # detection; nudged fires the stagnation hint at most once per run.
        self.task_doc: "TaskDoc | None" = None
        self.seen_states: set[tuple[str, str]] = set()
        self.nudged: bool = False
        # lazy visual locate provider (singleton per session)
        self._locate_provider: "MarkProvider | None" = None
        self._locate_provider_built: bool = False
        # Frame the last locate() ran its visual model on (U1 same-frame return):
        # the locate tool renders text + this screenshot instead of re-observing.
        self._last_locate_shot: "Screenshot | None" = None
        self._last_locate_app: str = "unknown"
        # last-known dimensions for coordinate conversion
        self._last_width: int = 0
        self._last_height: int = 0

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
            return list(result.marks)
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
                before = self._foreground_component()
                shot = self.screenshot()
                marks = self.refresh_marks(shot)
                after = self._foreground_component()
            except ScreenshotError as exc:
                last_error = exc
                continue
            if before is not None and after is not None and before != after:
                # Screen moved mid-capture: marks/screenshot may disagree. Retry.
                last_error = ScreenshotError(
                    f"observation unstable: foreground {before!r} -> {after!r}"
                )
                continue
            return self._commit_observation(shot, marks)

        # Two unstable/invalid attempts: fail closed and drop the whole batch.
        self._invalidate_batch()
        raise last_error or ScreenshotError("observation failed")

    def _commit_observation(
        self, shot: "Screenshot", marks: list[MarkCandidate]
    ) -> Observation:
        """Bump the batch, mint badged marks, and build the Observation."""

        current_app = self._foreground_label()
        self.screen_seq += 1
        self.epoch += 1
        minted = self._mint_marks(marks)
        self.marks = {mark.mark_id: mark for mark in minted}
        # Record (current_app, screen_hash) for TaskDoc stagnation detection.
        # screen_hash is a short sha256 of the screenshot base64 payload.
        screen_hash = hashlib.sha256(
            (shot.base64_data or "").encode("utf-8")
        ).hexdigest()[:16]
        self.seen_states.add((current_app, screen_hash))
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

    def mark_center_abs(self, mark: MarkCandidate) -> tuple[int, int]:
        """Convert a mark's 0-1000 center to absolute device pixels."""

        width = self._last_width
        height = self._last_height
        if width <= 0 or height <= 0:
            shot = self.screenshot()
            width, height = int(shot.width), int(shot.height)
        return convert_relative_to_absolute(list(mark.center), width, height)

    def locate(self, description: str) -> MarkCandidate:
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

        provider = self._get_locate_provider()
        if provider is None:
            raise LocateAmbiguousError("visual locate provider is unavailable")
        shot = self.screenshot()
        binding = self._screen_binding(shot)
        hint = MarkProviderHint(text=description, source="tool", action="locate")
        result = provider.provide_marks(
            shot,
            binding,
            hints=[hint],
            timeout=self.config.accessibility_timeout,
        )
        executable = [mark for mark in result.marks if getattr(mark, "valid", True)]
        if not result.success or len(executable) == 0:
            raise LocateAmbiguousError(
                f"no confident match for {description!r}",
                candidates=list(result.candidates),
            )
        if len(executable) > 1:
            raise LocateAmbiguousError(
                f"ambiguous match for {description!r}: {len(executable)} candidates",
                candidates=executable,
            )
        raw = executable[0]
        minted = replace(
            raw, mark_id=mint_badge(raw.mark_id, self.epoch), epoch=self.epoch
        )
        self.marks[minted.mark_id] = minted
        # Stash the locate frame so the tool returns the same screenshot without
        # a fresh observe (single-producer: one screenshot per locate call).
        self._last_locate_shot = shot
        self._last_locate_app = self._foreground_label()
        return minted

    # -- marks digest -----------------------------------------------------

    @staticmethod
    def format_marks_digest(marks: list[MarkCandidate], max_items: int = 40) -> str:
        """One line per mark: ``mark_id | role | text(<=32) | center``."""

        lines: list[str] = []
        for mark in marks[:max_items]:
            role = (mark.role or "?")[:24]
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

    def _foreground_label(self) -> str:
        try:
            foreground = self.device_factory.get_foreground_app(self.config.device_id)
        except Exception:
            return "unknown"
        return foreground.display_name or foreground.package_name or "unknown"

    def _foreground_component(self) -> str | None:
        """Foreground component name for the atomic-capture stability bracket.

        Returns the resolved ``package/activity`` component (or a coarser package
        fallback) so ``observe()`` can detect a screen change *between* the
        pre-screenshot and post-screenshot samples. ``None`` means the foreground
        could not be sampled — the bracket then degrades to "assume stable"
        rather than forcing a retry loop on a device that never reports one.
        """

        try:
            foreground = self.device_factory.get_foreground_app(self.config.device_id)
        except Exception:
            return None
        component = getattr(foreground, "component_name", None)
        if component:
            return str(component)
        package = getattr(foreground, "package_name", None)
        return str(package) if package else None

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
        )

    def _get_locate_provider(self) -> "MarkProvider | None":
        if not self._locate_provider_built:
            cfg = {
                "grounding_provider_name": self.config.grounding_provider,
                "locateanything_max_size": self.config.locateanything_max_size,
            }
            if self.config.locateanything_model:
                cfg["grounding_model_path"] = self.config.locateanything_model
            self._locate_provider = build_locate_provider(cfg)
            self._locate_provider_built = True
        return self._locate_provider
