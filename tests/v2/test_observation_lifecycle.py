"""U1 observation lifecycle: atomic single producer + batch-badge freshness gate.

Covers the U1 contract (design-locked) against the **real** ``PhoneSession``
driven by a fake DeviceFactory (no real device / MLX / network):

    - atomic ``observe()`` — one consistent frame (foreground-before → screenshot
      → accessibility dump reusing that shot → foreground-after);
    - single producer — ``refresh_marks`` reuses the passed screenshot (no 2nd
      capture); one screenshot per observe;
    - batch badges — external mark ids carry an ``@e<epoch>`` suffix that never
      repeats across observations; badge ids are minted onto ``MarkCandidate``;
    - freshness gate — a badged id from a superseded batch fails closed in
      ``resolve_mark`` (StaleMarkError); the tap tool then never executes;
    - instability retry-once then fail — a mid-capture foreground change retries
      once; a second instability raises and clears the whole batch;
    - observation failure invalidates the batch (marks cleared, epoch frozen);
    - ``locate`` mints the resolved mark into the current batch and the locate
      tool returns the *same* frame (no extra observe, no epoch bump).
"""

from __future__ import annotations

import pytest

from phone_agent.grounding.provider import MarkCandidate, MarkProviderResult
from phone_agent.v2.session import (
    PhoneSession,
    ScreenshotError,
    StaleMarkError,
    mint_badge,
    parse_badge,
)


# --------------------------------------------------------------------------
# Fakes: a screenshot, a foreground observation, and a scriptable device.
# --------------------------------------------------------------------------
class FakeShot:
    def __init__(self, payload: str, *, valid: bool = True) -> None:
        self.base64_data = payload
        self.width = 1080
        self.height = 2400
        self.mime_type = "image/png"
        self.is_valid = valid
        self.failure_code = None if valid else "screenshot_unavailable"


class FakeForeground:
    def __init__(self, component: str, package: str = "com.example.app") -> None:
        self.component_name = component
        self.package_name = package
        self.display_name = package


_SETTINGS_XML = (
    "<hierarchy>"
    '<node text="WLAN" class="android.widget.TextView" clickable="true" '
    'enabled="true" bounds="[0,100][1080,300]" />'
    '<node text="蓝牙" class="android.widget.TextView" clickable="true" '
    'enabled="true" bounds="[0,300][1080,500]" />'
    "</hierarchy>"
)


class FakeConfig:
    device_id = None
    accessibility_max_marks = 80
    accessibility_timeout = 3.0
    grounding_provider = "accessibility"
    locateanything_max_size = 960
    locateanything_model = None


class FakeDeviceFactory:
    """Scriptable device: screenshots, foreground, and a UiAutomator dump.

    ``foreground_script`` is a list of component names returned by successive
    ``get_foreground_app`` calls (observe() samples twice per attempt). A single
    string means the foreground is stable. ``screenshot_valid`` toggles a screenshot
    failure for the batch-invalidation test.
    """

    def __init__(
        self,
        *,
        foreground: str | list[str] = "com.example.app/.Main",
        xml: str = _SETTINGS_XML,
        screenshot_valid: bool = True,
    ) -> None:
        self._fg = foreground
        self._fg_i = 0
        self._xml = xml
        self._screenshot_valid = screenshot_valid
        self.screenshot_calls = 0
        self.dump_calls = 0

    def get_screenshot(self, device_id=None, timeout=10):
        self.screenshot_calls += 1
        return FakeShot(f"shot{self.screenshot_calls}", valid=self._screenshot_valid)

    def dump_uiautomator_xml(self, device_id=None, timeout=None):
        self.dump_calls += 1
        return self._xml

    def get_foreground_app(self, device_id=None):
        if isinstance(self._fg, list):
            comp = self._fg[min(self._fg_i, len(self._fg) - 1)]
            self._fg_i += 1
        else:
            comp = self._fg
        return FakeForeground(comp)


def _session(**kwargs) -> PhoneSession:
    return PhoneSession(FakeConfig(), device_factory=FakeDeviceFactory(**kwargs))


# --------------------------------------------------------------------------
# Badge primitives.
# --------------------------------------------------------------------------
def test_badge_roundtrip():
    badged = mint_badge("ax_3", 7)
    assert badged == "ax_3@e7"
    base, epoch = parse_badge(badged)
    assert base == "ax_3"
    assert epoch == 7


def test_parse_badge_unbadged_returns_none():
    base, epoch = parse_badge("ax_3")
    assert base == "ax_3"
    assert epoch is None


def test_session_exposes_real_screen_geometry_for_swipe_scroll():
    """Bugfix regression: the swipe/scroll tools read ``screen_width`` /
    ``screen_height`` / ``relative_to_abs`` off the session. These must reflect
    the real screenshot dimensions — before this fix the real session lacked
    the interface and the tools silently fell back to a hardcoded 1080x2400.
    """

    session = _session()
    session.observe()  # FakeShot is 1080x2400
    assert session.screen_width == 1080
    assert session.screen_height == 2400
    assert session.relative_to_abs(500, 500) == (540, 1200)
    assert session.relative_to_abs(0, 1000) == (0, 2400)


# --------------------------------------------------------------------------
# Atomicity + single producer.
# --------------------------------------------------------------------------
def test_observe_is_single_producer_one_screenshot_one_dump():
    session = _session()
    obs = session.observe()
    # Exactly one screenshot and one dump per observe (refresh_marks reused the
    # shot instead of taking a second one).
    assert session.device_factory.screenshot_calls == 1
    assert session.device_factory.dump_calls == 1
    # The frame is internally consistent: the digest marks come from the same
    # screenshot payload the observation carries.
    assert obs.screenshot_b64 == "shot1"
    assert len(obs.marks) == 2


def test_observe_bumps_epoch_and_mints_badges():
    session = _session()
    assert session.epoch == 0
    obs = session.observe()
    assert session.epoch == 1
    assert obs.epoch == 1
    # Every external mark id carries the batch badge; the provider id is the prefix.
    for mark in obs.marks:
        base, epoch = parse_badge(mark.mark_id)
        assert epoch == 1
        assert base.startswith("ax_")
        assert mark.epoch == 1
    # session.marks is keyed by the badged id.
    assert all("@e1" in mid for mid in session.marks)


def test_refresh_marks_reuses_passed_shot_no_second_capture():
    session = _session()
    shot = session.screenshot()
    assert session.device_factory.screenshot_calls == 1
    session.refresh_marks(shot)
    # refresh_marks(shot) did NOT capture again.
    assert session.device_factory.screenshot_calls == 1
    assert session.device_factory.dump_calls == 1


# --------------------------------------------------------------------------
# Batch badges never reused across observations.
# --------------------------------------------------------------------------
def test_new_observation_invalidates_prior_batch_badges():
    session = _session()
    first = session.observe()
    old_id = first.marks[0].mark_id
    assert session.resolve_mark(old_id).mark_id == old_id  # live in batch 1

    session.observe()  # batch 2
    assert session.epoch == 2
    # The old badged id is now stale — even though the provider id "ax_1" recurs.
    with pytest.raises(StaleMarkError):
        session.resolve_mark(old_id)
    # A same-position mark exists in the new batch under a NEW badge (@e2).
    new_ids = list(session.marks)
    assert all("@e2" in mid for mid in new_ids)
    assert old_id not in session.marks


# --------------------------------------------------------------------------
# Freshness gate: stale badge fails closed, tool never executes.
# --------------------------------------------------------------------------
def test_resolve_mark_rejects_stale_batch():
    session = _session()
    session.observe()  # batch 1
    live_id = next(iter(session.marks))
    base, _ = parse_badge(live_id)
    session.observe()  # batch 2 -> live_id from batch 1 is stale
    with pytest.raises(StaleMarkError) as exc:
        session.resolve_mark(mint_badge(base, 1))
    assert "batch e1" in str(exc.value)


def test_stale_mark_blocks_tap_tool_no_device_action():
    from phone_agent.v2.tools.actuation import build_actuation_tools

    session = _session()
    first = session.observe()
    stale_id = first.marks[0].mark_id
    session.observe()  # invalidate batch 1

    tools = {t.name: t for t in build_actuation_tools(session, FakeConfig())}
    before_shots = session.device_factory.screenshot_calls
    out = tools["tap"].invoke({"target_mark_id": stale_id})
    # Fail-closed: error string, no tap, no extra observation triggered.
    assert isinstance(out, str)
    assert "stale mark" in out
    assert session.device_factory.screenshot_calls == before_shots


# --------------------------------------------------------------------------
# Instability: retry once, then fail and invalidate the batch.
# --------------------------------------------------------------------------
def test_observe_retries_once_on_foreground_change():
    # attempt1: before=A, after=B (unstable) -> retry; attempt2: before=B, after=B.
    session = PhoneSession(
        FakeConfig(),
        device_factory=FakeDeviceFactory(
            foreground=["A/.X", "B/.Y", "B/.Y", "B/.Y"]
        ),
    )
    obs = session.observe()
    assert obs.epoch == 1
    # Two attempts -> two screenshots taken; the committed one is the 2nd.
    assert session.device_factory.screenshot_calls == 2
    assert obs.screenshot_b64 == "shot2"


def test_observe_fails_and_invalidates_batch_on_persistent_instability():
    # Every attempt flips foreground -> never stable -> observation failure.
    session = PhoneSession(
        FakeConfig(),
        device_factory=FakeDeviceFactory(
            foreground=["A/.X", "B/.Y", "C/.Z", "D/.W"]
        ),
    )
    # Seed a prior batch so we can prove failure clears it.
    session.marks = {"ax_1@e0": MarkCandidate("ax_1@e0", [0, 0, 1, 1], [0, 0], epoch=0)}
    with pytest.raises(ScreenshotError):
        session.observe()
    # Batch invalidated: no stale marks survive a failed observation.
    assert session.marks == {}


def test_observe_screenshot_failure_invalidates_batch():
    session = PhoneSession(
        FakeConfig(), device_factory=FakeDeviceFactory(screenshot_valid=False)
    )
    session.marks = {"ax_1@e0": MarkCandidate("ax_1@e0", [0, 0, 1, 1], [0, 0], epoch=0)}
    with pytest.raises(ScreenshotError):
        session.observe()
    assert session.marks == {}


# --------------------------------------------------------------------------
# Locate: mints into the current batch, same-frame return, no extra observe.
# --------------------------------------------------------------------------
class _StubLocateProvider:
    """Returns exactly one confident mark for any hint."""

    name = "stub-locate"
    version = "test"

    def provide_marks(self, screenshot, screen_binding, hints=None, timeout=None):
        mark = MarkCandidate(
            mark_id="loc_1",
            bbox=[10, 10, 90, 90],
            center=[50, 50],
            role="ImageView",
            text_summary="隐藏目标",
            source=self.name,
        )
        return MarkProviderResult(
            success=True, provider=self.name, marks=[mark], candidates=[mark]
        )


def test_locate_mints_into_current_batch():
    session = _session()
    session.observe()  # batch 1
    session._locate_provider = _StubLocateProvider()
    session._locate_provider_built = True

    mark = session.locate("隐藏目标")
    # Minted into the CURRENT batch (epoch 1), no epoch bump.
    assert session.epoch == 1
    assert mark.mark_id == mint_badge("loc_1", 1)
    assert mark.epoch == 1
    # Registered and resolvable in the current batch.
    assert session.resolve_mark(mark.mark_id).mark_id == mark.mark_id


def test_locate_tool_returns_same_frame_no_extra_observe():
    from phone_agent.v2.tools.perception import build_perception_tools

    session = _session()
    session.observe()  # batch 1
    session._locate_provider = _StubLocateProvider()
    session._locate_provider_built = True
    shots_before = session.device_factory.screenshot_calls
    dumps_before = session.device_factory.dump_calls

    tools = {t.name: t for t in build_perception_tools(session, FakeConfig())}
    out = tools["locate"].invoke({"description": "隐藏目标"})

    # Same-frame: locate captured exactly one screenshot (for its own visual
    # model) and did NOT run a fresh observe (no extra accessibility dump).
    assert session.device_factory.screenshot_calls == shots_before + 1
    assert session.device_factory.dump_calls == dumps_before
    # The tool ships that located frame back (text + image), not a text-only stub.
    assert isinstance(out, list)
    texts = [b for b in out if b.get("type") == "text"]
    images = [b for b in out if b.get("type") in {"image_url", "image"}]
    assert texts and "已定位并注册为 mark loc_1@e1" in texts[0]["text"]
    assert len(images) == 1
    assert images[0]["image_url"]["url"].startswith("data:image/png;base64,")


def test_locate_epoch_survives_resolve_after_locate():
    # After a locate, a subsequent observe bumps the batch and the located mark
    # goes stale like any other batch-1 mark (freshness gate is uniform).
    session = _session()
    session.observe()  # batch 1
    session._locate_provider = _StubLocateProvider()
    session._locate_provider_built = True
    mark = session.locate("隐藏目标")
    session.observe()  # batch 2
    with pytest.raises(StaleMarkError):
        session.resolve_mark(mark.mark_id)


# --------------------------------------------------------------------------
# resolve_mark backward-compat: unbadged id still fails when absent.
# --------------------------------------------------------------------------
def test_resolve_unbadged_missing_id_is_stale():
    session = _session()
    session.observe()
    with pytest.raises(StaleMarkError):
        session.resolve_mark("ax_1")  # no badge -> not in current marks
