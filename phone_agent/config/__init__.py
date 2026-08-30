"""Configuration module for Phone Agent."""

from phone_agent.config.app_registry import (
    AppIdentity,
    AppRegistry,
    AppResolution,
    ForegroundAppObservation,
    InstalledAppInventory,
    LaunchPolicy,
    LaunchTargetResolution,
    LaunchTargetResolver,
)
from phone_agent.config.apps import (
    APP_PACKAGES,
    DEFAULT_APP_REGISTRY,
    DEFAULT_LAUNCH_POLICY,
    DEFAULT_LAUNCH_TARGET_RESOLVER,
)
from phone_agent.config.i18n import get_message, get_messages
from phone_agent.config.timing import (
    TIMING_CONFIG,
    ActionTimingConfig,
    ConnectionTimingConfig,
    DeviceTimingConfig,
    TimingConfig,
    get_timing_config,
    update_timing_config,
)

__all__ = [
    "APP_PACKAGES",
    "AppIdentity",
    "AppRegistry",
    "AppResolution",
    "ForegroundAppObservation",
    "InstalledAppInventory",
    "LaunchPolicy",
    "LaunchTargetResolution",
    "LaunchTargetResolver",
    "DEFAULT_APP_REGISTRY",
    "DEFAULT_LAUNCH_POLICY",
    "DEFAULT_LAUNCH_TARGET_RESOLVER",
    "get_messages",
    "get_message",
    "TIMING_CONFIG",
    "TimingConfig",
    "ActionTimingConfig",
    "DeviceTimingConfig",
    "ConnectionTimingConfig",
    "get_timing_config",
    "update_timing_config",
]
