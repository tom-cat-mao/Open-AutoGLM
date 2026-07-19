"""Tests for typed app identity, inventory, and launch-policy boundaries."""

import pytest

from phone_agent.config.app_registry import (
    AppIdentity,
    AppRegistry,
    InstalledAppInventory,
    LaunchPolicy,
    LaunchTargetResolver,
)
from phone_agent.config.apps import (
    APP_PACKAGES,
    DEFAULT_APP_REGISTRY,
    DEFAULT_LAUNCH_POLICY,
    normalize_app_name,
)


def test_every_legacy_package_has_exactly_one_identity() -> None:
    resolved_packages = {}
    for package in set(APP_PACKAGES.values()):
        resolution = DEFAULT_APP_REGISTRY.resolve_package(package)
        assert resolution.status == "resolved"
        assert resolution.identity is not None
        resolved_packages[package] = resolution.identity.canonical_id

    assert len(resolved_packages) == len(set(APP_PACKAGES.values()))


def test_all_legacy_names_resolve_to_their_package_identity() -> None:
    for name, package in APP_PACKAGES.items():
        by_name = DEFAULT_APP_REGISTRY.resolve_term(name)
        by_package = DEFAULT_APP_REGISTRY.resolve_package(package)
        assert by_name.status == "resolved", name
        assert by_name.identity == by_package.identity


def test_declared_aliases_are_registry_data_not_compiler_vocabulary() -> None:
    assert (
        DEFAULT_APP_REGISTRY.resolve_text("去b站打开一个视频").identity.canonical_id
        == "bilibili"
    )
    assert (
        DEFAULT_APP_REGISTRY.resolve_text("打开小红书").identity.canonical_id
        == "小红书"
    )
    assert normalize_app_name("Android-System-Settings") == "Settings"


def test_unknown_package_is_preserved_as_unknown() -> None:
    resolution = DEFAULT_APP_REGISTRY.resolve_package("com.example.unknown")

    assert resolution.status == "unknown"
    assert resolution.term == "com.example.unknown"
    assert resolution.identity is None


def test_alias_collision_is_explicitly_ambiguous() -> None:
    identities = [
        AppIdentity(
            "one", frozenset({"pkg.one"}), {"default": "One"}, frozenset({"shared"})
        ),
        AppIdentity(
            "two", frozenset({"pkg.two"}), {"default": "Two"}, frozenset({"shared"})
        ),
    ]
    registry = AppRegistry(reversed(identities))

    resolution = registry.resolve_term("shared")

    assert resolution.status == "ambiguous"
    assert [item.canonical_id for item in resolution.candidates] == ["one", "two"]


def test_package_cannot_belong_to_multiple_identities() -> None:
    identities = [
        AppIdentity("one", frozenset({"pkg.shared"}), {"default": "One"}, frozenset()),
        AppIdentity("two", frozenset({"pkg.shared"}), {"default": "Two"}, frozenset()),
    ]

    try:
        AppRegistry(identities)
    except ValueError as exc:
        assert str(exc) == "package_has_multiple_identities:pkg.shared"
    else:
        raise AssertionError(
            "duplicate package identity must fail registry construction"
        )


def test_known_installed_and_launch_allowed_are_independent() -> None:
    chrome = DEFAULT_APP_REGISTRY.resolve_term("Chrome").identity
    assert chrome is not None
    inventory = InstalledAppInventory(frozenset(), device_id="serial")
    resolver = LaunchTargetResolver(DEFAULT_APP_REGISTRY, DEFAULT_LAUNCH_POLICY)

    target = resolver.resolve("Chrome", inventory=inventory)

    assert target.status == "not_installed"
    assert target.identity == chrome
    assert DEFAULT_LAUNCH_POLICY.is_allowed(chrome) is True


def test_observation_only_system_home_is_not_launch_authorized() -> None:
    home = DEFAULT_APP_REGISTRY.resolve_package("com.android.launcher3").identity
    assert home is not None
    resolver = LaunchTargetResolver(
        DEFAULT_APP_REGISTRY,
        LaunchPolicy(frozenset(APP_PACKAGES.values())),
    )

    assert home.display_name == "System Home"
    assert resolver.resolve("system_home").status == "denied"


def test_unknown_natural_language_app_is_not_guessed_into_launch_target() -> None:
    resolver = LaunchTargetResolver(DEFAULT_APP_REGISTRY, DEFAULT_LAUNCH_POLICY)

    target = resolver.resolve("an app that is not registered")

    assert target.status == "unknown"
    assert target.package_name is None


@pytest.mark.parametrize(
    ("task", "canonical_id"),
    [("打开bilibili", "bilibili"), ("打开Chrome", "chrome"), ("启动Gmail", "gmail")],
)
def test_mixed_script_alias_adjacency_resolves(task: str, canonical_id: str) -> None:
    resolution = DEFAULT_APP_REGISTRY.resolve_text(task)

    assert resolution.status == "resolved"
    assert resolution.identity is not None
    assert resolution.identity.canonical_id == canonical_id
