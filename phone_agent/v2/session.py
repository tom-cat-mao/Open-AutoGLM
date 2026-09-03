"""v2 PhoneSession: device-side run state, screenshots, marks, and locate.

One :class:`PhoneSession` holds the mutable device-side state for a single run.
Tools reach the device and the current-screen marks exclusively through it. All
device I/O goes through :class:`~phone_agent.device_factory.DeviceFactory`
(P0: no direct ADB), and marks come from the grounding provider stack.

See ``AGENTS.md`` §6 for the binding contract.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
import time
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

    def __init__(
        self,
        message: str,
        *,
        failure_code: str | None = None,
        failure_message: str | None = None,
    ) -> None:
        super().__init__(message)
        self.failure_code = failure_code
        self.failure_message = failure_message


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
MAX_ACTION_SETTLE_MS = 5000


def clamp_action_settle_ms(settle_ms: int) -> tuple[int, bool]:
    """Clamp a per-tool observation settle override to [0, 5000] ms."""

    requested = int(settle_ms)
    effective = max(0, min(MAX_ACTION_SETTLE_MS, requested))
    return effective, effective != requested


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


# WP-G2cB (B2): accessibility marks-level failure codes that mean the dump was
# *unstable* (not "there are genuinely no marks"). observe() retries the atomic
# window once on these — the same tier as a mid-capture foreground change — then,
# if still failing, commits an annotated zero-mark observation (the screenshot is
# valid, so this is NOT the batch-invalidating observation failure that a bad
# screenshot / persistent foreground instability is). ``dump_empty`` /
# ``no_interactive_marks`` are legitimate empty screens and never retry.
_UNSTABLE_MARK_CODES = frozenset(
    {"timeout", "provider_error", "accessibility_xml_parse_error"}
)


@dataclass
class MarksSample:
    """Result of one marks extraction attempt (B2 internal signature).

    The tool-layer contract is unchanged — this object never leaves the session.
    ``failure_code``/``failure_message`` carry the provider's diagnosis so
    ``observe()`` can retry transient failures and annotate a final zero-mark
    observation; ``parse_summary`` is the trace-safe parser diagnostic bundle
    (window source, candidate counts, actionability tally).
    """

    marks: list[MarkCandidate]
    failure_code: str | None = None
    failure_message: str | None = None
    parse_summary: dict[str, Any] | None = None
    # WP-G2cB (B3): trace-safe window sidecar (``MarkProviderResult.screen_structures``)
    # so the digest header can surface ``active``/``focus`` — those live on the
    # window record, not on ``MarkCandidate``. Display only.
    windows: list[dict[str, Any]] | None = None


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
    # WP-G2cB (B2): accessibility marks-level failure diagnosis for this frame.
    # ``None`` on a clean dump. When set, the screenshot was valid but the marks
    # dump failed/was-empty; the tool layer annotates the OBS header
    # (``marks (0) [accessibility:<code>]``) and surfaces ``parse_summary`` so a
    # dump failure is never silently rendered as "this screen has no controls".
    marks_failure_code: str | None = None
    marks_failure_message: str | None = None
    parse_summary: dict[str, Any] | None = None
    # WP-G2cB (B3): trace-safe per-window sidecar (source_confidence / active /
    # focused / layer / type) so the model-facing digest can surface window
    # ``active``/``focus`` flags and the ``windowed/v1 source=`` badge. Display
    # only — addressing/execution never read it.
    windows: list[dict[str, Any]] | None = None


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
        # WP-I3 run-local launch correction evidence. Unknown launch attempts
        # record only package names that were actually rendered in that failure
        # receipt; a later verified package launch may consume exact matches.
        # This state is reset at every ThinPhoneAgent.run() boundary and is never
        # itself persisted.
        self.implicit_alias_run_id: str = ""
        self._failed_launches: list[dict[str, Any]] = []
        self._implicit_alias_written_terms: set[str] = set()
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
        # Observe-only experience mirrors. Tools append only after device-confirmed
        # launches; finish records whether its independent verifier actually ran.
        self.launched_apps: list[str] = []
        self.finish_verifier: str = "skipped"
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
        # WP-G2cB (B1): monotonic locate sequence. Every locate-minted mark id
        # carries this counter so two locates *in the same batch* can never
        # collide on the provider id (both providers tend to return ``la_1`` /
        # ``loc_1``). Without it, a second same-epoch locate would overwrite the
        # first entry in ``self.marks`` and a later tap on the first id would
        # silently actuate the second target. Monotonic for the whole run (never
        # reset by observe) so a stashed id is never reused after re-mint.
        self._locate_seq: int = 0
        # Frame the last locate() ran its visual model on (U1 same-frame return):
        # the locate tool renders text + this screenshot instead of re-observing.
        self._last_locate_shot: "Screenshot | None" = None
        self._last_locate_app: str = "unknown"
        self._last_locate_metadata: dict[str, Any] = {}
        # last-known dimensions for coordinate conversion
        self._last_width: int = 0
        self._last_height: int = 0

    # -- app knowledge ---------------------------------------------------

    def record_launched_app(self, package: str) -> None:
        """Remember one device-confirmed launch for the run outcome sidecar."""

        value = str(package or "").strip()
        if value:
            self.launched_apps.append(value)
    @staticmethod
    def _normalize_launch_term(value: str) -> str:
        """Strip all whitespace from a launch term without guessing aliases."""

        return "".join(str(value or "").split())

    def reset_implicit_alias_state(self, run_id: str | None = None) -> None:
        """Start an empty implicit-alias evidence ledger for one run."""

        self.implicit_alias_run_id = str(run_id or "").strip()
        self._failed_launches.clear()
        self._implicit_alias_written_terms.clear()

    def record_failed_launch(
        self, failed_term: str, candidates: list[str]
    ) -> None:
        """Remember one unknown launch and its receipt-visible packages."""

        term = self._normalize_launch_term(failed_term)
        normalized_candidates: list[str] = []
        seen: set[str] = set()
        for candidate in candidates:
            package = self._normalize_launch_term(candidate)
            if not package or package in seen:
                continue
            seen.add(package)
            normalized_candidates.append(package)
        self._failed_launches.append(
            {
                "failed_term": term,
                "candidates": normalized_candidates,
                "ts": time.time(),
            }
        )

    def implicit_alias_terms_for(self, package: str) -> list[str]:
        """Return unique failed terms backed by this exact listed package."""

        normalized_package = self._normalize_launch_term(package)
        if not normalized_package:
            return []

        matches: list[str] = []
        seen: set[str] = set()
        for failure in self._failed_launches:
            term = self._normalize_launch_term(failure.get("failed_term", ""))
            if (
                not term
                or term == normalized_package
                or term in seen
                or term in self._implicit_alias_written_terms
            ):
                continue
            candidates = failure.get("candidates") or []
            if not any(
                self._normalize_launch_term(candidate) == normalized_package
                for candidate in candidates
            ):
                continue
            seen.add(term)
            matches.append(term)
        return matches

    def mark_implicit_alias_written(self, term: str) -> None:
        """Deduplicate a successfully persisted failed term within this run."""

        normalized = self._normalize_launch_term(term)
        if normalized:
            self._implicit_alias_written_terms.add(normalized)

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

        try:
            shot = self.device_factory.get_screenshot(
                self.config.device_id,
                black_screen_detect=getattr(
                    self.config, "black_screen_detect", True
                ),
            )
        except TypeError as exc:
            # Compatibility for external/test DeviceFactory doubles that still
            # expose the pre-WP-O two-argument screenshot surface. Production's
            # DeviceFactory forwards the explicit V2Config switch.
            if "black_screen_detect" not in str(exc):
                raise
            shot = self.device_factory.get_screenshot(self.config.device_id)
        if not getattr(shot, "is_valid", False):
            code = getattr(shot, "failure_code", None) or "screenshot_unavailable"
            raise ScreenshotError(
                f"screenshot invalid: {code}",
                failure_code=code,
                failure_message=getattr(shot, "failure_message", None),
            )
        self._last_width = int(shot.width)
        self._last_height = int(shot.height)
        return shot

    def refresh_marks(self, shot: "Screenshot | None" = None) -> list[MarkCandidate]:
        """Produce accessibility marks for a screenshot; never raises.

        Backward-compatible thin wrapper over :meth:`refresh_marks_sample` that
        returns only the mark list (external/legacy callers and tests). U1
        single-producer discipline is unchanged: ``observe()`` passes the
        screenshot it already captured so marks are extracted against *that*
        frame — no second screenshot is taken.
        """

        return self.refresh_marks_sample(shot).marks

    def refresh_marks_sample(
        self, shot: "Screenshot | None" = None, *, screen_hash: str | None = None
    ) -> MarksSample:
        """Extract accessibility marks + the provider's failure diagnosis (B2).

        Returns a :class:`MarksSample` carrying the marks, the provider's
        ``failure_code``/``message`` (``timeout`` / ``accessibility_dump_empty``
        / ``accessibility_xml_parse_error`` / ``accessibility_no_interactive_marks``
        / ``provider_error``) and the trace-safe ``parse_summary``. This is an
        *internal* signature — the tool layer still only ever sees the rendered
        digest. Failure (bad screenshot, provider error, empty dump) yields an
        empty mark list with the code set; the caller decides how to degrade.

        ``screen_hash`` lets ``observe()`` pass the sha256 it already computed so
        the frame is hashed exactly once per observation (B5).
        """

        if shot is None:
            try:
                shot = self.screenshot()
            except ScreenshotError as exc:
                return MarksSample(
                    marks=[],
                    failure_code=getattr(exc, "failure_code", None)
                    or "screenshot_unavailable",
                    failure_message=getattr(exc, "failure_message", None),
                )
        try:
            binding = self._screen_binding(shot, screen_hash=screen_hash)
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
        except Exception as exc:  # noqa: BLE001 - provider failures degrade to []
            return MarksSample(
                marks=[], failure_code="provider_error", failure_message=type(exc).__name__
            )
        parse_summary = self._extract_parse_summary(result)
        windows = self._extract_windows_sidecar(result)
        if not result.success:
            return MarksSample(
                marks=[],
                failure_code=getattr(result, "failure_code", None),
                failure_message=getattr(result, "message", None),
                parse_summary=parse_summary,
                windows=windows,
            )
        return MarksSample(
            marks=list(result.marks),
            parse_summary=parse_summary,
            windows=windows,
        )

    @staticmethod
    def _extract_parse_summary(result: Any) -> dict[str, Any] | None:
        """Pull the trace-safe ``parse_summary`` out of a provider result."""

        metadata = getattr(result, "metadata", None)
        if isinstance(metadata, dict):
            summary = metadata.get("parse_summary")
            if isinstance(summary, dict):
                return dict(summary)
        return None

    @staticmethod
    def _extract_windows_sidecar(result: Any) -> list[dict[str, Any]] | None:
        """Pull the trace-safe per-window sidecar out of a provider result."""

        structures = getattr(result, "screen_structures", None)
        if isinstance(structures, list) and structures:
            return [dict(item) for item in structures if isinstance(item, dict)]
        return None

    def observe(self, settle_ms: int | None = None) -> Observation:
        """Atomic single-producer observation (U1).

        Before every sampling attempt, observation settles for
        ``config.observe_settle_ms`` by default. An explicit ``settle_ms``
        replaces that default and is clamped to [0, 5000] ms. The locate-only
        sampling path intentionally remains immediate. The atomic window stays:
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

        WP-G2cB (B2): a marks dump whose failure is *transient* (``timeout`` /
        parser error / provider error / a ``marks_windowed=on`` unsupported-dump
        error) is treated as observation instability — the atomic window retries
        once, the same tier as a mid-capture foreground change. If the marks dump
        still fails but the *screenshot* is valid, the observation still commits
        (annotated with the failure code + ``parse_summary``): a marks-only
        failure is not the batch-invalidating observation failure that a bad
        screenshot or persistent foreground instability is. A genuinely empty
        screen (``accessibility_dump_empty`` / ``no_interactive_marks``) does not
        retry.
        """

        if settle_ms is None:
            effective_settle_ms = int(
                getattr(self.config, "observe_settle_ms", 300) or 0
            )
        else:
            effective_settle_ms, _was_clamped = clamp_action_settle_ms(settle_ms)

        last_error: Exception | None = None
        last_sample: MarksSample | None = None
        last_shot: "Screenshot | None" = None
        last_after: "ForegroundAppObservation | None" = None
        last_hash: str = ""
        for attempt in range(2):
            if effective_settle_ms > 0:
                time.sleep(effective_settle_ms / 1000.0)
            try:
                before = self._foreground_observation()
                shot = self.screenshot()
                screen_hash = self._hash_screenshot(shot)
                sample = self.refresh_marks_sample(shot, screen_hash=screen_hash)
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
            last_sample = sample
            last_shot = shot
            last_after = after
            last_hash = screen_hash
            # A transient marks-dump failure is observation instability: retry
            # once (like a foreground change). A stable/empty screen or the
            # second attempt commits with whatever the dump produced.
            if (
                sample.failure_code in _UNSTABLE_MARK_CODES
                and attempt == 0
            ):
                last_error = ScreenshotError(
                    f"marks dump unstable: {sample.failure_code}"
                )
                continue
            # The ``after`` sample is closest to the committed frame — use it for
            # the display label so the label matches the bracket that verified
            # stability (no extra device round-trip outside the window).
            return self._commit_observation(
                shot, sample, foreground=after, screen_hash=screen_hash
            )

        # Screenshot itself never succeeded: fail closed, drop the whole batch.
        if last_shot is None or last_sample is None:
            self._invalidate_batch()
            raise last_error or ScreenshotError("observation failed")

        # Screenshot is valid but the marks dump kept failing across both
        # attempts. The frame is real, so commit it annotated rather than losing
        # the observation — a dump failure must never masquerade as "no controls".
        return self._commit_observation(
            last_shot, last_sample, foreground=last_after, screen_hash=last_hash
        )

    def _commit_observation(
        self,
        shot: "Screenshot",
        sample: MarksSample,
        *,
        foreground: "ForegroundAppObservation | None" = None,
        screen_hash: str | None = None,
    ) -> Observation:
        """Bump the batch, mint badged marks, and build the Observation.

        ``sample`` carries the marks plus the B2 marks-level failure diagnosis;
        ``screen_hash`` is the sha256 already computed for this frame (B5 — the
        payload is hashed exactly once per observation, shared with the screen
        binding).
        """

        current_app = self._label_of(foreground)
        self.screen_seq += 1
        self.epoch += 1
        minted = self._mint_marks(sample.marks)
        self.marks = {mark.mark_id: mark for mark in minted}
        # B4: a fresh observation supersedes the stashed locate frame — the
        # located screenshot belonged to the prior batch, so drop it rather than
        # letting the locate tool return a same-frame image from a dead batch.
        self._last_locate_shot = None
        self._last_locate_app = "unknown"
        # screen_hash: short sha256 of the screenshot payload, audit/binding only.
        digest = screen_hash if screen_hash is not None else self._hash_screenshot(shot)
        return Observation(
            screenshot_b64=shot.base64_data,
            width=int(shot.width),
            height=int(shot.height),
            current_app=current_app,
            marks=minted,
            screen_seq=self.screen_seq,
            epoch=self.epoch,
            screen_hash=digest,
            mime_type=getattr(shot, "mime_type", None) or "image/png",
            marks_failure_code=sample.failure_code,
            marks_failure_message=sample.failure_message,
            parse_summary=sample.parse_summary,
            windows=sample.windows,
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

        return [self._mint_one(mark) for mark in marks]

    def _mint_one(self, mark: MarkCandidate, *, locate_seq: int | None = None) -> MarkCandidate:
        """Badge one mark into the current batch (single minting path — B5).

        ``locate_seq`` disambiguates locate-minted ids within one batch (B1):
        two locates before the next ``observe()`` both tend to carry the same
        provider id (``la_1``/``loc_1``); the monotonic ``#<seq>`` infix keeps
        the external ids distinct so the second never overwrites the first in
        ``self.marks``. The ``@e<epoch>`` suffix is preserved so ``parse_badge``
        / ``resolve_mark`` freshness extraction is unchanged.
        """

        provider_id = mark.mark_id
        if locate_seq is not None:
            provider_id = f"{provider_id}#{locate_seq}"
        badged_id = mint_badge(provider_id, self.epoch)
        return replace(mark, mark_id=badged_id, epoch=self.epoch)

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

        WP-G2cB (B4): locate runs its visual model on a **fresh screenshot**, so a
        confident hit **opens a new observation batch** — ``epoch`` increments,
        every prior-batch mark is invalidated (``self.marks`` cleared), and only
        the located mark is minted into the new batch. This restores the U1
        "one frame = one batch" invariant: before B4 locate stamped a new-frame
        hit with the *previous* batch's epoch, so a stale ``ax_*`` id from the
        earlier observation would still resolve against a screen locate had
        already moved past. Any ``scope_*`` ids are resolved against the current
        batch **before** the bump, so scoping still fails closed on a stale id.

        The single screenshot the visual model ran on is stashed
        (``_last_locate_shot`` / ``_last_locate_app``) so the locate tool can
        return that **same frame** without a second capture. Zero or multiple
        confident candidates raise :class:`LocateAmbiguousError`; nothing is
        registered, no batch is opened, and nothing executes.
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
        # B4: a confident locate opens a NEW batch (new frame => new authority).
        # Bump the epoch and drop every prior-batch mark, then mint the located
        # hit alone into the fresh batch. The monotonic locate seq (B1) still
        # disambiguates repeat locates, and record the display geometry off this
        # frame so swipe/scroll math stays correct.
        self._last_width = int(shot.width)
        self._last_height = int(shot.height)
        self.screen_seq += 1
        self.epoch += 1
        self._locate_seq += 1
        self.marks = {}
        minted = self._mint_one(raw, locate_seq=self._locate_seq)
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
    def format_marks_digest(
        marks: list[MarkCandidate],
        max_items: int = 40,
        *,
        window_source: str | None = None,
        windows: list[dict[str, Any]] | None = None,
    ) -> str:
        """Render the marks digest (WP-G2a windowed-aware, pure display layer).

        The ``[OBS] app=X screen#N`` / ``marks (K):`` header is added by the
        caller and is untouched — this returns only the digest body. The mark id
        badge, coordinates and ``max_items`` cut are unchanged; windowing only
        regroups and annotates the *same* marks.

        * Non-windowed marks (no ``window_id`` — locate marks, test doubles) or a
          single **weak** (heuristic) window keep the historic flat layout
          ``mark_id | role | text | center``, with an optional trailing
          ``| path=…`` when a semantic container path exists.
        * Multiple windows (or a single window carrying real ``--windows``
          metadata) render grouped: a leading ``windowed/v1 source=<src>`` badge
          line (WP-G2cB B3 — ``source=`` only when ``window_source`` is known),
          then one window header line (``Wk type pkg layer=… [active] [focus]
          [covered_by=…]``) followed by its marks indented, each ending with
          ``| op=<actionability> | path=<container_path>``. ``active``/``focus``
          come from the ``windows`` sidecar (they live on the window record, not
          on ``MarkCandidate``); when the sidecar is absent the flags are simply
          omitted. This is display only — nothing here gates execution.
        """

        shown = list(marks[:max_items])
        window_ids = [m.window_id for m in shown if m.window_id]
        distinct = list(dict.fromkeys(window_ids))
        has_strong = any(
            m.window_layer is not None or m.window_type is not None for m in shown
        )
        grouped = bool(distinct) and (len(distinct) > 1 or has_strong)

        if grouped:
            win_flags = PhoneSession._window_flag_lookup(windows)
            windowed_body = PhoneSession._format_windowed_digest(shown, win_flags)
            # B3: prepend the ``windowed/v1 source=<src>`` badge line **only** when
            # the window source is known (the production observe path always sets
            # it). A bare direct call (tests / callers without a parse summary)
            # keeps the pre-B3 head-first layout so the WP-G2a render contract is
            # unchanged; the diagnosis skill only ever parses the production output
            # which carries the badge.
            if window_source:
                body = f"windowed/v1 source={window_source}\n{windowed_body}"
            else:
                body = windowed_body
        else:
            body = PhoneSession._format_flat_digest(shown)

        if len(marks) > max_items:
            body = f"{body}\n... (+{len(marks) - max_items} more)" if body else (
                f"... (+{len(marks) - max_items} more)"
            )
        return body

    @staticmethod
    def _window_flag_lookup(
        windows: list[dict[str, Any]] | None,
    ) -> dict[str, tuple[bool, bool]]:
        """Map ``window_id -> (active, focused)`` from the trace-safe sidecar."""

        lookup: dict[str, tuple[bool, bool]] = {}
        for entry in windows or []:
            if not isinstance(entry, dict):
                continue
            wid = entry.get("window_id")
            if wid:
                lookup[str(wid)] = (
                    bool(entry.get("active")),
                    bool(entry.get("focused")),
                )
        return lookup

    @staticmethod
    def _format_flat_digest(marks: list[MarkCandidate]) -> str:
        """Historic one-line-per-mark layout (+ optional trailing container path)."""

        lines: list[str] = []
        for mark in marks:
            role = (mark.role or "?")[:24]
            if is_container_like(mark):
                role = f"[容器]{role}"
            text = (mark.text_summary or "").replace("\n", " ").strip()
            if len(text) > 32:
                text = text[:32]
            center = tuple(mark.center) if mark.center else ()
            line = f"{mark.mark_id} | {role} | {text} | {center}"
            path = PhoneSession._render_container_path(mark)
            if path:
                line += f" | path={path}"
            lines.append(line)
        return "\n".join(lines)

    @staticmethod
    def _format_windowed_digest(
        marks: list[MarkCandidate],
        win_flags: dict[str, tuple[bool, bool]] | None = None,
    ) -> str:
        """Group marks by window (layer desc), one header + indented mark lines."""

        order: list[str] = []
        buckets: dict[str, list[MarkCandidate]] = {}
        for mark in marks:
            wid = mark.window_id or "W?"
            if wid not in buckets:
                buckets[wid] = []
                order.append(wid)
            buckets[wid].append(mark)

        def layer_of(wid: str) -> int:
            for mark in buckets[wid]:
                if mark.window_layer is not None:
                    return mark.window_layer
            return -1

        # Higher layer first; ties keep document (window-id) order.
        doc_rank = {wid: i for i, wid in enumerate(order)}
        order.sort(key=lambda wid: (-layer_of(wid), doc_rank[wid]))

        lines: list[str] = []
        for wid in order:
            bucket = buckets[wid]
            lines.append(PhoneSession._window_header(wid, bucket, win_flags))
            for mark in bucket:
                lines.append(PhoneSession._windowed_mark_line(mark))
        return "\n".join(lines)

    @staticmethod
    def _window_header(
        window_id: str,
        bucket: list[MarkCandidate],
        win_flags: dict[str, tuple[bool, bool]] | None = None,
    ) -> str:
        """One window header line derived from its marks' shared metadata."""

        sample = bucket[0]
        parts = [window_id]
        if sample.window_type:
            parts.append(str(sample.window_type))
        if sample.package:
            parts.append(str(sample.package))
        if sample.window_layer is not None:
            parts.append(f"layer={sample.window_layer}")
        if sample.window_title:
            parts.append(f"title={sample.window_title}")
        # B3: bare active/focus tokens from the window sidecar (only when true).
        active, focused = (win_flags or {}).get(window_id, (False, False))
        if active:
            parts.append("active")
        if focused:
            parts.append("focus")
        covered_by = PhoneSession._covered_by(bucket)
        if covered_by:
            parts.append(f"covered_by={covered_by}")
        return " ".join(parts)

    @staticmethod
    def _windowed_mark_line(mark: MarkCandidate) -> str:
        """Indented ``mark_id | role | text | center | op=… | path=…`` line."""

        role = (mark.role or "?")[:24]
        if is_container_like(mark):
            role = f"[容器]{role}"
        text = (mark.text_summary or "").replace("\n", " ").strip()
        if len(text) > 32:
            text = text[:32]
        center = tuple(mark.center) if mark.center else ()
        line = f"  {mark.mark_id} | {role} | {text} | {center}"
        if mark.actionability:
            line += f" | op={mark.actionability}"
        path = PhoneSession._render_container_path(mark)
        if path:
            line += f" | path={path}"
        return line

    @staticmethod
    def _render_container_path(mark: MarkCandidate) -> str:
        """Join the (already <=3) semantic container path outer->inner."""

        path = getattr(mark, "container_path", ()) or ()
        return ">".join(str(kind) for kind in path if kind)

    @staticmethod
    def _covered_by(bucket: list[MarkCandidate]) -> str | None:
        """Return the window id covering this window, from mark reasons."""

        for mark in bucket:
            for reason in getattr(mark, "actionability_reasons", ()) or ():
                text = str(reason)
                if text.startswith("covered_by:"):
                    return text.split(":", 1)[1]
                if text.startswith("maybe_covered_by:"):
                    return text.split(":", 1)[1]
        return None


    # -- internals --------------------------------------------------------

    def _dump_tree(self, timeout: float | None = None) -> str:
        windowed = getattr(self.config, "marks_windowed", "auto")
        try:
            return self.device_factory.dump_uiautomator_xml(
                self.config.device_id, timeout=timeout, windowed=windowed
            )
        except TypeError as exc:
            # Duck-typed / test device factories may still expose the pre-WP-G2a
            # two-argument surface. Fall back to the legacy single-root dump.
            if "windowed" not in str(exc):
                raise
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

    @staticmethod
    def _hash_screenshot(shot: "Screenshot") -> str:
        """Short sha256 of a screenshot payload (B5: computed once per frame)."""

        return hashlib.sha256(
            (getattr(shot, "base64_data", "") or "").encode("utf-8")
        ).hexdigest()[:16]

    def _screen_binding(
        self, shot: "Screenshot", *, screen_hash: str | None = None
    ) -> ScreenBinding:
        raw_hash = screen_hash if screen_hash is not None else self._hash_screenshot(shot)
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
