"""Typed app identity, installation inventory, and launch policy boundaries."""

from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata
from typing import Any, Iterable, Literal, Mapping

ResolutionStatus = Literal["resolved", "unknown", "ambiguous"]
LaunchResolutionStatus = Literal[
    "resolved",
    "unknown",
    "ambiguous",
    "not_installed",
    "denied",
]

SYSTEM_HOME_PACKAGES = frozenset(
    {
        "com.android.launcher",
        "com.android.launcher2",
        "com.android.launcher3",
        "com.google.android.apps.nexuslauncher",
        "com.miui.home",
        "com.sec.android.app.launcher",
    }
)


def normalize_app_term(value: str) -> str:
    """Normalize a user-facing app term without erasing meaningful punctuation."""

    normalized = unicodedata.normalize("NFKC", str(value or "")).strip()
    return " ".join(normalized.split()).casefold()


@dataclass(frozen=True)
class AppIdentity:
    """Stable app identity independent of launch authorization and installation."""

    canonical_id: str
    packages: frozenset[str]
    display_names: Mapping[str, str]
    aliases: frozenset[str]
    observation_only: bool = False

    @property
    def display_name(self) -> str:
        """Return the default localized display name."""

        return self.display_names.get("default", self.canonical_id)

    @property
    def primary_package(self) -> str:
        """Return the deterministic primary package for launch compatibility."""

        return sorted(self.packages)[0]


@dataclass(frozen=True)
class AppResolution:
    """Result of resolving a package or natural-language app term."""

    status: ResolutionStatus
    term: str
    identity: AppIdentity | None = None
    candidates: tuple[AppIdentity, ...] = ()


@dataclass(frozen=True)
class ForegroundAppObservation:
    """Observed foreground package/activity with optional registry identity."""

    package_name: str
    activity_name: str | None
    component_name: str
    known: bool
    canonical_id: str | None
    display_name: str
    is_system_home: bool = False

    def to_dict(self) -> dict[str, str | bool | None]:
        """Return the trace-safe foreground fact projection."""

        return {
            "package_name": self.package_name,
            "activity_name": self.activity_name,
            "component_name": self.component_name,
            "known": self.known,
            "canonical_id": self.canonical_id,
            "display_name": self.display_name,
            "is_system_home": self.is_system_home,
        }


class AppRegistry:
    """Resolve app terms and packages without insertion-order guesses."""

    def __init__(self, identities: Iterable[AppIdentity]) -> None:
        identity_list = tuple(identities)
        canonical_index: dict[str, AppIdentity] = {}
        package_index: dict[str, AppIdentity] = {}
        alias_index: dict[str, list[AppIdentity]] = {}

        for identity in identity_list:
            canonical_key = normalize_app_term(identity.canonical_id)
            if not canonical_key or canonical_key in canonical_index:
                raise ValueError(
                    f"duplicate_or_empty_canonical_id:{identity.canonical_id}"
                )
            if not identity.packages:
                raise ValueError(f"missing_package:{identity.canonical_id}")
            canonical_index[canonical_key] = identity
            for package in identity.packages:
                if package in package_index:
                    raise ValueError(f"package_has_multiple_identities:{package}")
                package_index[package] = identity
            terms = set(identity.aliases)
            terms.add(identity.canonical_id)
            terms.update(identity.display_names.values())
            terms.update(identity.packages)
            for term in terms:
                normalized = normalize_app_term(term)
                if normalized:
                    alias_index.setdefault(normalized, []).append(identity)

        self._identities = identity_list
        self._canonical_index = canonical_index
        self._package_index = package_index
        self._alias_index = {
            term: tuple(
                sorted(
                    {item.canonical_id: item for item in matches}.values(),
                    key=lambda item: item.canonical_id,
                )
            )
            for term, matches in alias_index.items()
        }

    @property
    def identities(self) -> tuple[AppIdentity, ...]:
        """Return all registered identities in deterministic source order."""

        return self._identities

    def resolve_term(self, term: str) -> AppResolution:
        """Resolve an exact user-facing term to one identity or ambiguity."""

        normalized = normalize_app_term(term)
        matches = self._alias_index.get(normalized, ())
        if len(matches) == 1:
            return AppResolution(status="resolved", term=term, identity=matches[0])
        if len(matches) > 1:
            return AppResolution(status="ambiguous", term=term, candidates=matches)
        return AppResolution(status="unknown", term=term)

    def resolve_package(self, package_name: str) -> AppResolution:
        """Resolve an observed package while preserving unknown package text."""

        identity = self._package_index.get(str(package_name or "").strip())
        if identity is None:
            return AppResolution(status="unknown", term=package_name)
        return AppResolution(status="resolved", term=package_name, identity=identity)

    def foreground_observation(self, component_name: str) -> ForegroundAppObservation:
        """Resolve one observed Android package/activity component."""

        component = str(component_name or "").strip()
        package, separator, activity = component.partition("/")
        if not separator or not package:
            return ForegroundAppObservation(
                package_name=package,
                activity_name=None,
                component_name=component,
                known=False,
                canonical_id=None,
                display_name=package or "Unknown foreground app",
            )
        resolution = self.resolve_package(package)
        if resolution.status != "resolved" or resolution.identity is None:
            return ForegroundAppObservation(
                package_name=package,
                activity_name=activity or None,
                component_name=component,
                known=False,
                canonical_id=None,
                display_name=package,
            )
        identity = resolution.identity
        return ForegroundAppObservation(
            package_name=package,
            activity_name=activity or None,
            component_name=component,
            known=True,
            canonical_id=identity.canonical_id,
            display_name=identity.display_name,
            is_system_home=identity.canonical_id == "system_home",
        )

    def resolve_text(self, text: str) -> AppResolution:
        """Resolve the longest declared alias present in natural-language text."""

        normalized_text = normalize_app_term(text)
        matches: list[tuple[int, str, AppIdentity]] = []
        for alias, identities in self._alias_index.items():
            if len(alias) < 2 or not _alias_occurs(alias, normalized_text):
                continue
            for identity in identities:
                matches.append((len(alias), alias, identity))
        if not matches:
            return AppResolution(status="unknown", term=text)
        longest = max(item[0] for item in matches)
        best = {item[2].canonical_id: item[2] for item in matches if item[0] == longest}
        if len(best) == 1:
            identity = next(iter(best.values()))
            return AppResolution(status="resolved", term=text, identity=identity)
        return AppResolution(
            status="ambiguous",
            term=text,
            candidates=tuple(sorted(best.values(), key=lambda item: item.canonical_id)),
        )

    def aliases_for(self, identity: AppIdentity) -> tuple[str, ...]:
        """Return normalized aliases owned only by the given identity."""

        return tuple(
            alias
            for alias, identities in self._alias_index.items()
            if len(identities) == 1 and identities[0] == identity
        )

    @classmethod
    def from_legacy_maps(
        cls,
        app_packages: Mapping[str, str],
        app_aliases: Mapping[str, str],
        canonical_display: Mapping[str, str],
    ) -> "AppRegistry":
        """Build typed identities from the current compatibility data source."""

        names_by_package: dict[str, list[str]] = {}
        for name, package in app_packages.items():
            names_by_package.setdefault(package, []).append(name)

        identities: list[AppIdentity] = []
        for package, names in names_by_package.items():
            preferred = next(
                (
                    name
                    for name in canonical_display
                    if app_packages.get(name) == package
                ),
                names[0],
            )
            display = canonical_display.get(preferred, preferred)
            aliases = set(names)
            aliases.add(display)
            for alias, target in app_aliases.items():
                if app_packages.get(target) == package:
                    aliases.add(alias)
            identities.append(
                AppIdentity(
                    canonical_id=_canonical_id(preferred),
                    packages=frozenset({package}),
                    display_names={"default": display},
                    aliases=frozenset(aliases),
                )
            )

        identities.append(
            AppIdentity(
                canonical_id="system_home",
                packages=SYSTEM_HOME_PACKAGES,
                display_names={"default": "System Home"},
                aliases=frozenset(),
                observation_only=True,
            )
        )
        return cls(identities)


@dataclass(frozen=True)
class InstalledAppInventory:
    """Package inventory observed on one device."""

    packages: frozenset[str]
    device_id: str | None = None

    def contains(self, package_name: str) -> bool:
        """Return whether a package is installed on this device."""

        return package_name in self.packages


@dataclass(frozen=True)
class LaunchPolicy:
    """Launch authority: static allowlist union device installation.

    The device is the fact source: a package installed on the device is
    launchable. The static allowlist remains as a seed so launch never depends
    on a stale static table. ``observation_only`` identities stay denied.
    """

    allowed_packages: frozenset[str]

    def allows_package(
        self,
        package_name: str,
        *,
        inventory: InstalledAppInventory | None = None,
    ) -> bool:
        """Return whether a package is launch-authorized."""

        package = str(package_name or "").strip()
        if not package:
            return False
        if package in self.allowed_packages:
            return True
        if inventory is not None and inventory.contains(package):
            return True
        return False

    def is_allowed(
        self,
        identity: AppIdentity,
        *,
        inventory: InstalledAppInventory | None = None,
    ) -> bool:
        """Return whether at least one package is launch-authorized."""

        if identity.observation_only:
            return False
        if identity.packages & self.allowed_packages:
            return True
        if inventory is not None and bool(identity.packages & inventory.packages):
            return True
        return False


@dataclass(frozen=True)
class LaunchTargetResolution:
    """Launch resolution across identity, policy, and optional inventory."""

    status: LaunchResolutionStatus
    term: str
    identity: AppIdentity | None = None
    package_name: str | None = None
    candidates: tuple[AppIdentity | str, ...] = ()


class LaunchTargetResolver:
    """Resolve launch targets without conflating known, installed, and allowed.

    Resolution chain (all stages share the same status state machine):
    a. static registry alias hit (the static table is an alias seed);
    b. per-run learned mapping hit (RuntimeAppLearningContext);
    c. device inventory path: caller-supplied ``candidates`` are matched
       case-insensitively as substrings against the installed inventory
       (unique -> resolved, multiple -> ambiguous, none -> unknown).
    """

    def __init__(self, registry: AppRegistry, policy: LaunchPolicy) -> None:
        self._registry = registry
        self._policy = policy

    def resolve(
        self,
        term: str,
        *,
        inventory: InstalledAppInventory | None = None,
        candidates: Iterable[str] | None = None,
        learning: Any | None = None,
    ) -> LaunchTargetResolution:
        """Resolve one launch request using explicit policy and inventory facts."""

        resolution = self._registry.resolve_term(term)
        if resolution.status == "ambiguous":
            return LaunchTargetResolution(
                status="ambiguous",
                term=term,
                candidates=resolution.candidates,
            )
        if resolution.status == "resolved" and resolution.identity is not None:
            return self._resolve_identity(
                term, resolution.identity, inventory=inventory
            )
        # b. per-run learned mapping (device already confirmed launchability).
        if learning is not None:
            learned_package = learning.lookup(term)
            if learned_package is not None:
                return self._resolve_known_package(
                    term, learned_package, inventory=inventory
                )
        # c. device inventory candidates: substring match, fail-closed.
        if candidates and inventory is not None:
            matches = tuple(
                sorted(_match_candidates_to_inventory(candidates, inventory))
            )
            if len(matches) == 1:
                return self._resolve_known_package(
                    term, matches[0], inventory=inventory
                )
            if len(matches) > 1:
                return LaunchTargetResolution(
                    status="ambiguous", term=term, candidates=matches
                )
        return LaunchTargetResolution(status="unknown", term=term)

    def _resolve_identity(
        self,
        term: str,
        identity: AppIdentity,
        *,
        inventory: InstalledAppInventory | None,
    ) -> LaunchTargetResolution:
        """Resolve a statically known identity under policy and install facts."""

        if not self._policy.is_allowed(identity, inventory=inventory):
            return LaunchTargetResolution(status="denied", term=term, identity=identity)
        if inventory is not None:
            package = next(
                (
                    item
                    for item in sorted(identity.packages)
                    if inventory.contains(item)
                    and self._policy.allows_package(item, inventory=inventory)
                ),
                None,
            )
            if package is not None:
                return LaunchTargetResolution(
                    status="resolved",
                    term=term,
                    identity=identity,
                    package_name=package,
                )
            fallback = next(
                (
                    item
                    for item in sorted(identity.packages)
                    if self._policy.allows_package(item, inventory=None)
                ),
                None,
            )
            if fallback is None:
                return LaunchTargetResolution(
                    status="denied", term=term, identity=identity
                )
            return LaunchTargetResolution(
                status="not_installed",
                term=term,
                identity=identity,
                package_name=fallback,
            )
        package = next(
            (
                item
                for item in sorted(identity.packages)
                if self._policy.allows_package(item, inventory=None)
            ),
            None,
        )
        if package is None:
            return LaunchTargetResolution(status="denied", term=term, identity=identity)
        return LaunchTargetResolution(
            status="resolved",
            term=term,
            identity=identity,
            package_name=package,
        )

    def _resolve_known_package(
        self,
        term: str,
        package_name: str,
        *,
        inventory: InstalledAppInventory | None,
    ) -> LaunchTargetResolution:
        """Resolve a package from learning/candidates under policy facts."""

        if not self._policy.allows_package(package_name, inventory=inventory):
            return LaunchTargetResolution(status="denied", term=term)
        package_identity = self._registry.resolve_package(package_name).identity
        if package_identity is not None and package_identity.observation_only:
            return LaunchTargetResolution(
                status="denied", term=term, package_name=package_name
            )
        return LaunchTargetResolution(
            status="resolved", term=term, package_name=package_name
        )


def _match_candidates_to_inventory(
    candidates: Iterable[str], inventory: InstalledAppInventory
) -> frozenset[str]:
    """Case-insensitive substring match of candidate terms against packages."""

    matches: set[str] = set()
    normalized_packages = {
        normalize_app_term(package): package for package in inventory.packages
    }
    for candidate in candidates:
        needle = normalize_app_term(str(candidate or ""))
        if not needle:
            continue
        for normalized_package, package in normalized_packages.items():
            if needle in normalized_package:
                matches.add(package)
    return frozenset(matches)


def _canonical_id(name: str) -> str:
    normalized = normalize_app_term(name)
    return re.sub(r"\s+", "_", normalized)


def _alias_occurs(alias: str, text: str) -> bool:
    if any("\u4e00" <= char <= "\u9fff" for char in alias):
        return alias in text
    return (
        re.search(rf"(?<![A-Za-z0-9_]){re.escape(alias)}(?![A-Za-z0-9_])", text)
        is not None
    )
