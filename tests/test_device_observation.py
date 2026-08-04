"""Tests for stable composite screenshot and foreground-app sampling."""

from dataclasses import dataclass

import pytest

from phone_agent.config.apps import DEFAULT_APP_REGISTRY
from phone_agent.device_factory import DeviceFactory, ObservationCaptureError
from phone_agent.graph.observation import build_observation


@dataclass
class FakeScreenshot:
    base64_data: str = "screen"
    width: int = 100
    height: int = 200
    is_valid: bool = True


class SequencedDeviceModule:
    def __init__(self, components: list[str]) -> None:
        self._components = iter(components)
        self.screenshot_count = 0

    def get_foreground_app(self, device_id=None):
        return DEFAULT_APP_REGISTRY.foreground_observation(next(self._components))

    def get_screenshot(self, device_id=None, timeout=10):
        self.screenshot_count += 1
        return FakeScreenshot(base64_data=f"screen-{self.screenshot_count}")


def _factory(module: SequencedDeviceModule) -> DeviceFactory:
    factory = DeviceFactory()
    factory._module = module
    return factory


def test_composite_capture_retries_when_foreground_changes_during_screenshot() -> None:
    module = SequencedDeviceModule(
        [
            "com.android.settings/.Settings",
            "com.android.chrome/.Main",
            "com.android.chrome/.Main",
            "com.android.chrome/.Main",
        ]
    )

    captured = _factory(module).capture_observation(max_attempts=2)

    assert captured.foreground.package_name == "com.android.chrome"
    assert captured.attempts == 2
    assert captured.observation_epoch == 1
    assert module.screenshot_count == 2


def test_composite_capture_fails_closed_when_foreground_never_stabilizes() -> None:
    module = SequencedDeviceModule(
        [
            "com.android.settings/.One",
            "com.android.settings/.Two",
            "com.android.settings/.Three",
            "com.android.settings/.Four",
        ]
    )

    with pytest.raises(ObservationCaptureError) as raised:
        _factory(module).capture_observation(max_attempts=2)

    assert raised.value.code == "observation_unstable"
    assert raised.value.attempts == 2


def test_composite_capture_assigns_monotonic_epochs() -> None:
    module = SequencedDeviceModule(
        [
            "com.android.settings/.Settings",
            "com.android.settings/.Settings",
            "com.android.settings/.Settings",
            "com.android.settings/.Settings",
        ]
    )
    factory = _factory(module)

    first = factory.capture_observation()
    second = factory.capture_observation()

    assert (first.observation_epoch, second.observation_epoch) == (1, 2)


def test_observation_propagates_foreground_facts_and_epoch() -> None:
    foreground = DEFAULT_APP_REGISTRY.foreground_observation(
        "com.example.unknown/.MainActivity"
    )

    observation = build_observation(
        screenshot=FakeScreenshot(),
        current_app=foreground.display_name,
        foreground=foreground,
        observation_epoch=7,
    )

    assert observation.snapshot.observation_epoch == 7
    assert observation.mark_registry.observation_epoch == 7
    assert observation.snapshot.foreground_package == "com.example.unknown"
    assert observation.snapshot.foreground_activity == ".MainActivity"
    assert observation.snapshot.foreground_known is False


def _screenshot() -> FakeScreenshot:
    return FakeScreenshot()


def _base_marks() -> list[dict]:
    return [{"mark_id": "m1", "bbox": [100, 200, 300, 400]}]


def _locate_mark_dict(screen_id: str, mark_id: str = "locate_1") -> dict:
    return {
        "mark_id": mark_id,
        "screen_id": screen_id,
        "bbox": [200, 300, 400, 500],
        "center": [300, 400],
        "source": "locate",
        "confidence": 1.0,
    }


def test_build_observation_inherits_locate_marks_across_rebuild() -> None:
    """F-A: a locate_N registered by execute survives a plan-side rebuild on
    the same screen and stays groundable; versions stay consistent."""
    from phone_agent.actions.grounding import ground_intent_to_action
    from phone_agent.graph.marks import MarkRegistry

    first = build_observation(
        screenshot=_screenshot(),
        current_app="FakeApp",
        marks=_base_marks(),
    )
    previous_registry = first.mark_registry.with_extra_marks(
        [_locate_mark_dict(first.snapshot.screen_id)]
    )

    rebuilt = build_observation(
        screenshot=_screenshot(),
        current_app="FakeApp",
        marks=_base_marks(),
        previous_registry=previous_registry,
    )

    assert rebuilt.snapshot.screen_id == first.snapshot.screen_id
    assert "locate_1" in rebuilt.mark_registry.marks
    assert "m1" in rebuilt.mark_registry.marks
    locate_mark = rebuilt.mark_registry.marks["locate_1"]
    assert locate_mark.screen_id == rebuilt.snapshot.screen_id
    # mark_set_version is recomputed once (not mutated after structure binding).
    assert rebuilt.snapshot.mark_set_version == rebuilt.mark_registry.mark_set_version
    assert (
        rebuilt.object_registry.mark_set_version
        == rebuilt.mark_registry.mark_set_version
    )
    assert rebuilt.object_registry.mark_set_version == rebuilt.mark_registry.mark_set_version
    # The inherited locate mark is executable (grounds to a real ActionIR).
    grounded = ground_intent_to_action(
        {
            "_metadata": "intent",
            "action": "tap",
            "target_mark_id": "locate_1",
        },
        mark_registry=rebuilt.mark_registry,
        screen_id=rebuilt.snapshot.screen_id,
    )
    assert grounded["_metadata"] == "do"
    assert grounded["action"] == "Tap"
    assert grounded["element"] == [300.0, 400.0]


def test_build_observation_inheritance_accepts_dict_registry() -> None:
    """F-A: previous_registry may be the raw state dict (MarkRegistry.to_dict)."""
    first = build_observation(
        screenshot=_screenshot(),
        current_app="FakeApp",
        marks=_base_marks(),
    )
    previous_dict = first.mark_registry.with_extra_marks(
        [_locate_mark_dict(first.snapshot.screen_id)]
    ).to_dict()

    rebuilt = build_observation(
        screenshot=_screenshot(),
        current_app="FakeApp",
        marks=_base_marks(),
        previous_registry=previous_dict,
    )

    assert "locate_1" in rebuilt.mark_registry.marks
    assert (
        rebuilt.object_registry.mark_set_version
        == rebuilt.mark_registry.mark_set_version
    )


def test_build_observation_drops_foreign_screen_locate_marks() -> None:
    """F-A: locate marks bound to a different screen are dropped fail-closed."""
    first = build_observation(
        screenshot=_screenshot(),
        current_app="FakeApp",
        marks=_base_marks(),
    )
    foreign = build_observation(
        screenshot=FakeScreenshot(base64_data="other-screen", width=360, height=640),
        current_app="FakeApp",
        marks=_base_marks(),
    )
    previous_registry = foreign.mark_registry.with_extra_marks(
        [_locate_mark_dict(foreign.snapshot.screen_id)]
    )

    rebuilt = build_observation(
        screenshot=_screenshot(),
        current_app="FakeApp",
        marks=_base_marks(),
        previous_registry=previous_registry,
    )

    assert rebuilt.snapshot.screen_id == first.snapshot.screen_id
    assert "locate_1" not in rebuilt.mark_registry.marks
    assert "m1" in rebuilt.mark_registry.marks
    assert rebuilt.mark_registry.mark_set_version == first.mark_registry.mark_set_version


def test_build_observation_ignores_missing_previous_registry() -> None:
    """F-A: None previous_registry (first round) is a no-op."""
    plain = build_observation(
        screenshot=_screenshot(),
        current_app="FakeApp",
        marks=_base_marks(),
    )
    with_previous = build_observation(
        screenshot=_screenshot(),
        current_app="FakeApp",
        marks=_base_marks(),
        previous_registry=None,
    )
    assert with_previous.mark_registry.marks == plain.mark_registry.marks
    assert (
        with_previous.mark_registry.mark_set_version
        == plain.mark_registry.mark_set_version
    )
