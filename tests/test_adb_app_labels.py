from __future__ import annotations

import subprocess

from phone_agent.adb import device


class FakeCompletedProcess:
    def __init__(self, stdout: str = "", stderr: str = "", returncode: int = 0) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def test_get_app_labels_parses_launcher_packages_and_labels(monkeypatch) -> None:
    monkeypatch.setattr(device, "_APP_LABEL_CACHE", {})
    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        calls.append(command)
        if "query-activities" in command:
            return FakeCompletedProcess(
                stdout=(
                    "com.example.alpha/.MainActivity\n"
                    "com.example.beta/com.example.beta.LauncherActivity\n"
                    "com.example.empty/.Launcher\n"
                )
            )
        if "get-application-label" in command[-1]:
            return FakeCompletedProcess(
                stdout=(
                    "com.example.alpha\t  Alpha App  \n"
                    "com.example.beta\t Beta App\n"
                    "com.example.empty\t   \n"
                )
            )
        raise AssertionError(f"Unexpected ADB command: {command}")

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert device.get_app_labels("serial") == [
        device.AppLabelEntry(package="com.example.alpha", label="Alpha App"),
        device.AppLabelEntry(package="com.example.beta", label="Beta App"),
    ]
    assert calls[0] == [
        "adb",
        "-s",
        "serial",
        "shell",
        "cmd",
        "package",
        "query-activities",
        "--brief",
        "-a",
        "android.intent.action.MAIN",
        "-c",
        "android.intent.category.LAUNCHER",
    ]
    assert sum("get-application-label" in command[-1] for command in calls) == 1


def test_get_app_labels_falls_back_to_third_party_packages(monkeypatch) -> None:
    monkeypatch.setattr(device, "_APP_LABEL_CACHE", {})
    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        calls.append(command)
        if "query-activities" in command:
            return FakeCompletedProcess(stderr="unsupported", returncode=1)
        if command[-4:] == ["pm", "list", "packages", "-3"]:
            return FakeCompletedProcess(
                stdout="package:com.example.one\npackage:com.example.two\n"
            )
        if "get-application-label" in command[-1]:
            return FakeCompletedProcess(
                stdout="com.example.one\tOne\ncom.example.two\tTwo\n"
            )
        raise AssertionError(f"Unexpected ADB command: {command}")

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert device.get_app_labels() == [
        device.AppLabelEntry(package="com.example.one", label="One"),
        device.AppLabelEntry(package="com.example.two", label="Two"),
    ]
    assert any(
        command[-4:] == ["pm", "list", "packages", "-3"]
        for command in calls
    )


def test_get_app_labels_adb_failure_returns_empty(monkeypatch) -> None:
    monkeypatch.setattr(device, "_APP_LABEL_CACHE", {})

    def fake_run(command, **kwargs):
        return FakeCompletedProcess(stderr="device offline", returncode=1)

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert device.get_app_labels() == []


def test_get_app_labels_label_command_stripped_degrades_to_packages(monkeypatch) -> None:
    """OEM-stripped ``pm get-application-label`` (e.g. ColorOS): still return the
    launchable install facts with the package name as the label."""

    monkeypatch.setattr(device, "_APP_LABEL_CACHE", {})

    def fake_run(command, **kwargs):
        if "query-activities" in command:
            return FakeCompletedProcess(
                stdout="com.example.one/.MainActivity\ncom.example.two/.MainActivity\n"
            )
        if "get-application-label" in command[-1]:
            return FakeCompletedProcess(stderr="Unknown command", returncode=1)
        raise AssertionError(f"Unexpected ADB command: {command}")

    monkeypatch.setattr(subprocess, "run", fake_run)

    entries = device.get_app_labels()
    assert entries == [
        device.AppLabelEntry(package="com.example.one", label="com.example.one"),
        device.AppLabelEntry(package="com.example.two", label="com.example.two"),
    ]


def test_get_app_labels_cache_avoids_second_label_lookup(monkeypatch) -> None:
    monkeypatch.setattr(device, "_APP_LABEL_CACHE", {})
    launcher_calls = 0
    label_calls = 0

    def fake_run(command, **kwargs):
        nonlocal launcher_calls, label_calls
        if "query-activities" in command:
            launcher_calls += 1
            components = (
                "com.example.one/.MainActivity\ncom.example.two/.MainActivity\n"
                if launcher_calls == 1
                else "com.example.two/.MainActivity\ncom.example.one/.MainActivity\n"
            )
            return FakeCompletedProcess(stdout=components)
        if "get-application-label" in command[-1]:
            label_calls += 1
            return FakeCompletedProcess(
                stdout="com.example.one\tOne\ncom.example.two\tTwo\n"
            )
        raise AssertionError(f"Unexpected ADB command: {command}")

    monkeypatch.setattr(subprocess, "run", fake_run)

    first = device.get_app_labels("serial")
    second = device.get_app_labels("serial")

    assert first == second
    assert launcher_calls == 2
    assert label_calls == 1
