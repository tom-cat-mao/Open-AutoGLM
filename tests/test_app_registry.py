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


def test_resolver_static_alias_hit_is_first_stage() -> None:
    resolver = LaunchTargetResolver(DEFAULT_APP_REGISTRY, DEFAULT_LAUNCH_POLICY)
    inventory = InstalledAppInventory(
        frozenset({APP_PACKAGES["Chrome"], "com.example.other"}), device_id="serial"
    )

    target = resolver.resolve(
        "chrome",
        inventory=inventory,
        candidates=["com.example.other"],
    )

    assert target.status == "resolved"
    assert target.package_name == APP_PACKAGES["Chrome"]


def test_resolver_learning_cache_hit_is_second_stage() -> None:
    from phone_agent.graph.runtime_app_learning import RuntimeAppLearningContext

    resolver = LaunchTargetResolver(DEFAULT_APP_REGISTRY, DEFAULT_LAUNCH_POLICY)
    learning = RuntimeAppLearningContext()
    learning.record("某新应用", "com.example.learned")
    inventory = InstalledAppInventory(
        frozenset({"com.example.learned"}), device_id="serial"
    )

    target = resolver.resolve(
        "某新应用",
        inventory=inventory,
        candidates=["com.example.other"],
        learning=learning,
    )

    assert target.status == "resolved"
    assert target.package_name == "com.example.learned"
    assert target.identity is None


def test_resolver_candidates_unique_hit_is_third_stage() -> None:
    resolver = LaunchTargetResolver(DEFAULT_APP_REGISTRY, DEFAULT_LAUNCH_POLICY)
    inventory = InstalledAppInventory(
        frozenset({"com.example.unique.app"}), device_id="serial"
    )

    target = resolver.resolve(
        "某新应用",
        inventory=inventory,
        candidates=["com.example.unique.app"],
    )

    assert target.status == "resolved"
    assert target.package_name == "com.example.unique.app"


def test_resolver_candidates_match_is_case_insensitive_substring() -> None:
    resolver = LaunchTargetResolver(DEFAULT_APP_REGISTRY, DEFAULT_LAUNCH_POLICY)
    inventory = InstalledAppInventory(
        frozenset({"com.Tongcheng.Android"}), device_id="serial"
    )

    target = resolver.resolve(
        "同程旅行新客户端",
        inventory=inventory,
        candidates=["tongcheng"],
    )

    assert target.status == "resolved"
    assert target.package_name == "com.Tongcheng.Android"


def test_resolver_candidates_multiple_hits_are_ambiguous() -> None:
    resolver = LaunchTargetResolver(DEFAULT_APP_REGISTRY, DEFAULT_LAUNCH_POLICY)
    inventory = InstalledAppInventory(
        frozenset({"com.example.alpha.app", "com.example.beta.app"}), device_id="serial"
    )

    target = resolver.resolve(
        "某新应用",
        inventory=inventory,
        candidates=["example", "beta"],
    )

    assert target.status == "ambiguous"
    assert set(target.candidates) == {"com.example.alpha.app", "com.example.beta.app"}


def test_resolver_candidates_zero_hit_is_unknown() -> None:
    resolver = LaunchTargetResolver(DEFAULT_APP_REGISTRY, DEFAULT_LAUNCH_POLICY)
    inventory = InstalledAppInventory(
        frozenset({"com.example.installed"}), device_id="serial"
    )

    target = resolver.resolve(
        "某新应用",
        inventory=inventory,
        candidates=["com.example.missing.app"],
    )

    assert target.status == "unknown"
    assert target.package_name is None


def test_resolver_short_needles_never_match() -> None:
    """F9: "a" / "com" are legal needles that would uniquely match a system
    package on a sparse device — sub-4-char normalized needles are skipped, so
    they can never falsely resolve a launch."""
    resolver = LaunchTargetResolver(DEFAULT_APP_REGISTRY, DEFAULT_LAUNCH_POLICY)
    inventory = InstalledAppInventory(
        frozenset({"com.android.settings", "com.example.installed"}),
        device_id="serial",
    )

    for needle in ("a", "com", "安"):
        target = resolver.resolve(
            "某新应用",
            inventory=inventory,
            candidates=[needle],
        )
        assert target.status == "unknown", needle
        assert target.package_name is None, needle

    # the same inventory still resolves a real >= 4-char fragment
    target = resolver.resolve(
        "某新应用",
        inventory=inventory,
        candidates=["settings"],
    )
    assert target.status == "resolved"
    assert target.package_name == "com.android.settings"


def test_resolver_candidates_without_inventory_fail_closed_unknown() -> None:
    resolver = LaunchTargetResolver(DEFAULT_APP_REGISTRY, DEFAULT_LAUNCH_POLICY)

    target = resolver.resolve(
        "某新应用",
        inventory=None,
        candidates=["com.example.installed"],
    )

    assert target.status == "unknown"


def test_policy_installed_package_is_launchable_without_static_allowlist() -> None:
    identity = AppIdentity(
        "ext_app",
        frozenset({"com.example.ext"}),
        {"default": "ExtApp"},
        frozenset({"某外部应用"}),
    )
    registry = AppRegistry([identity])
    policy = LaunchPolicy(frozenset())
    resolver = LaunchTargetResolver(registry, policy)
    inventory = InstalledAppInventory(frozenset({"com.example.ext"}), device_id="serial")

    target = resolver.resolve("某外部应用", inventory=inventory)

    assert target.status == "resolved"
    assert target.package_name == "com.example.ext"
    assert policy.is_allowed(identity, inventory=inventory) is True


def test_policy_uninstalled_package_is_denied() -> None:
    identity = AppIdentity(
        "ext_app",
        frozenset({"com.example.ext"}),
        {"default": "ExtApp"},
        frozenset({"某外部应用"}),
    )
    registry = AppRegistry([identity])
    policy = LaunchPolicy(frozenset())
    resolver = LaunchTargetResolver(registry, policy)
    inventory = InstalledAppInventory(frozenset(), device_id="serial")

    assert policy.is_allowed(identity, inventory=inventory) is False
    assert resolver.resolve("某外部应用", inventory=inventory).status == "denied"


def test_policy_observation_only_identity_always_denied() -> None:
    identity = AppIdentity(
        "system_home",
        frozenset({"com.android.launcher3"}),
        {"default": "System Home"},
        frozenset(),
        observation_only=True,
    )
    registry = AppRegistry([identity])
    policy = LaunchPolicy(frozenset({"com.android.launcher3"}))
    resolver = LaunchTargetResolver(registry, policy)
    inventory = InstalledAppInventory(
        frozenset({"com.android.launcher3"}), device_id="serial"
    )

    assert policy.is_allowed(identity, inventory=inventory) is False
    assert resolver.resolve("system_home", inventory=inventory).status == "denied"
    assert resolver.resolve("com.android.launcher3", inventory=inventory).status == "denied"


def test_learning_cache_resolves_same_term_after_launch() -> None:
    from phone_agent.graph.runtime_app_learning import RuntimeAppLearningContext

    resolver = LaunchTargetResolver(DEFAULT_APP_REGISTRY, DEFAULT_LAUNCH_POLICY)
    learning = RuntimeAppLearningContext()
    inventory = InstalledAppInventory(
        frozenset({"com.example.learned"}), device_id="serial"
    )

    before = resolver.resolve(
        "某新应用", inventory=inventory, candidates=["com.example.learned"]
    )
    assert before.status == "resolved"
    assert before.package_name == "com.example.learned"
    learning.record("某新应用", before.package_name)

    after = resolver.resolve("某新应用", inventory=inventory, learning=learning)

    assert after.status == "resolved"
    assert after.package_name == "com.example.learned"
