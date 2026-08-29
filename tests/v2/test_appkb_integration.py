"""Fake-only integration tests for App-KB session and prompt wiring."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from phone_agent.adb import AppLabelEntry, get_app_labels
from phone_agent.device_factory import DeviceFactory
from phone_agent.v2.agent import ThinPhoneAgent
from phone_agent.v2.appkb import AppKnowledgeStore
from phone_agent.v2.session import PhoneSession


class _LabelDevice:
    def __init__(self, labels=None, error: Exception | None = None) -> None:
        self.labels = list(labels or [])
        self.error = error
        self.calls = 0

    def get_app_labels(self, device_id=None):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return list(self.labels)


def _config(tmp_path, *, enabled: bool = True):
    return SimpleNamespace(
        app_kb_enabled=enabled,
        memory_dir=str(tmp_path / "memory"),
        device_id="serial-1",
    )


def _global_entry(term: str, label: str, package: str) -> dict:
    timestamp = datetime(2026, 1, 1, tzinfo=timezone.utc).isoformat()
    return {
        "term": term,
        "label": label,
        "package": package,
        "kind": "alias",
        "scope": "global",
        "confidence": 0.8,
        "success_count": 0,
        "first_seen": timestamp,
        "last_seen": timestamp,
        "stale": False,
    }


def test_session_app_kb_wiring_is_disabled_without_filesystem_write(tmp_path):
    device = _LabelDevice([AppLabelEntry("com.example.one", "One")])
    session = PhoneSession(_config(tmp_path, enabled=False), device_factory=device)

    assert session.sync_app_knowledge() is False
    assert session.app_list_for_prompt(10) == ""
    assert session.app_store is None
    assert session.app_knowledge is None
    assert device.calls == 0
    assert not (tmp_path / "memory").exists()


def test_adb_label_exports_and_device_factory_plain_forward():
    assert callable(get_app_labels)
    factory = DeviceFactory()
    expected = [AppLabelEntry("com.example.one", "One")]
    factory._module = SimpleNamespace(get_app_labels=lambda device_id: expected)

    assert factory.get_app_labels("serial-1") == expected


def test_sync_app_knowledge_fails_open_when_labels_unavailable(tmp_path):
    config = _config(tmp_path)
    store = AppKnowledgeStore(config.memory_dir)
    store.upsert(_global_entry("global-alias", "Global App", "com.example.global"))
    session = PhoneSession(
        config,
        device_factory=_LabelDevice(error=RuntimeError("device absent")),
    )

    assert session.sync_app_knowledge() is False
    assert session.app_store is not None
    assert session.app_knowledge is not None
    assert session.app_list_for_prompt(10) == "Global App"


def test_sync_resolves_serial_when_device_id_unset(tmp_path):
    """Default single-device config (no serial) must still feed the App-KB:
    the serial is resolved from the device itself and used as the scope."""

    class _SerialDevice(_LabelDevice):
        def get_serial_number(self, device_id=None):
            return "auto-serial"

    config = _config(tmp_path)
    config.device_id = None
    device = _SerialDevice([AppLabelEntry("com.example.one", "One")])
    session = PhoneSession(config, device_factory=device)

    assert session.sync_app_knowledge() is True
    store = session.app_store
    assert store is not None
    device_entries = store.entries(scope="device:auto-serial")
    assert [entry["label"] for entry in device_entries] == ["One"]
    assert session.app_list_for_prompt(10) == "One"
    assert session.app_knowledge is not None
    assert session.app_knowledge.lookup("One") == "com.example.one"


def test_sync_and_prompt_list_are_device_first_and_bounded(tmp_path):
    device = _LabelDevice(
        [
            AppLabelEntry("com.example.b", "Device B"),
            AppLabelEntry("com.example.a", "Device A"),
        ]
    )
    session = PhoneSession(_config(tmp_path), device_factory=device)
    assert session.sync_app_knowledge() is True
    assert session.app_store is not None
    session.app_store.upsert(
        _global_entry("global-alias", "Global App", "com.example.global")
    )

    rendered = session.app_list_for_prompt(2)
    assert rendered.startswith("Device A，Device B")
    assert "Global App" not in rendered
    assert "…等 3 个，可用 launch_app 尝试其它名称" in rendered


def test_agent_prompt_injection_contains_bounded_app_list():
    calls = {"sync": 0, "max_n": None}

    def sync():
        calls["sync"] += 1

    def render(max_n):
        calls["max_n"] = max_n
        return "微信，淘宝，…等 3 个，可用 launch_app 尝试其它名称"

    agent = ThinPhoneAgent.__new__(ThinPhoneAgent)
    agent.config = SimpleNamespace(app_kb_enabled=True, app_list_max=2)
    agent.session = SimpleNamespace(
        sync_app_knowledge=sync,
        app_list_for_prompt=render,
        observe=lambda: SimpleNamespace(
            screenshot_b64="",
            current_app="launcher",
            screen_seq=1,
            marks=[],
        ),
    )
    agent._base_system_prompt = "BASE"
    agent._system_prompt = "BASE"

    agent._prepare_app_knowledge()

    assert calls == {"sync": 1, "max_n": 2}
    assert "# 本机可启动应用（launch_app 请用这些名字）" in agent._system_prompt
    assert "微信，淘宝" in agent._system_prompt
    assert "…等 3 个" in agent._system_prompt
    messages = agent._initial_messages("打开应用")
    assert messages[0].content == agent._system_prompt


def test_agent_app_kb_disabled_skips_sync_and_prompt_injection():
    def unexpected_call(*args, **kwargs):
        raise AssertionError("App-KB hook must not run when disabled")

    agent = ThinPhoneAgent.__new__(ThinPhoneAgent)
    agent.config = SimpleNamespace(app_kb_enabled=False, app_list_max=2)
    agent.session = SimpleNamespace(
        sync_app_knowledge=unexpected_call,
        app_list_for_prompt=unexpected_call,
    )
    agent._base_system_prompt = "BASE"
    agent._system_prompt = "STALE"

    agent._prepare_app_knowledge()

    assert agent._system_prompt == "BASE"
