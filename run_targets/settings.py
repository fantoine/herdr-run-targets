"""The plugin's own settings, read from its Herdr config directory."""

from __future__ import annotations

import os
import sys
import tomllib
from dataclasses import dataclass

SETTINGS_FILE = "config.toml"

FOCUS_STAY = "stay"
FOCUS_FIRST = "first"
FOCUS_LAST = "last"
FOCUS_MODES = (FOCUS_STAY, FOCUS_FIRST, FOCUS_LAST)


@dataclass(frozen=True)
class Settings:
    """What the user can tune. Every default leaves the plugin as it ships."""

    label_prefix: str = ""
    label_suffix: str = ""
    focus_mode: str = FOCUS_STAY


def tab_label(name: str, settings: Settings) -> str:
    """The label a service's tab carries.

    Applied once, when the tab is created: changing the prefix later does not
    rename tabs that already exist.
    """
    return f"{settings.label_prefix}{name}{settings.label_suffix}"


def _text(value: object) -> str | None:
    return value if isinstance(value, str) else None


def parse_settings(text: str, source: str) -> tuple[Settings, list[str]]:
    """Parse the settings file.

    Every failure degrades to the default rather than stopping the dashboard: a
    typo in an optional file must not cost the user their services. Unknown keys
    pass silently, so a file written for a newer version stays readable.
    """
    try:
        document = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        return Settings(), [f"{source}: invalid TOML ({error})"]

    warnings: list[str] = []

    tabs = document.get("tabs")
    tabs = tabs if isinstance(tabs, dict) else {}
    prefix = _text(tabs.get("label_prefix"))
    suffix = _text(tabs.get("label_suffix"))
    if tabs.get("label_prefix") is not None and prefix is None:
        warnings.append(f"{source}: tabs.label_prefix must be a string; ignored")
    if tabs.get("label_suffix") is not None and suffix is None:
        warnings.append(f"{source}: tabs.label_suffix must be a string; ignored")

    dashboard = document.get("dashboard")
    dashboard = dashboard if isinstance(dashboard, dict) else {}
    raw_mode = dashboard.get("focus_mode")
    mode = _text(raw_mode)
    if raw_mode is not None and mode not in FOCUS_MODES:
        warnings.append(
            f"{source}: unknown focus_mode {raw_mode!r}; using {FOCUS_STAY}"
        )
        mode = None

    return (
        Settings(
            label_prefix=prefix or "",
            label_suffix=suffix or "",
            focus_mode=mode or FOCUS_STAY,
        ),
        warnings,
    )


def settings_path() -> str:
    """Where Herdr keeps this plugin's configuration."""
    directory = os.environ.get("HERDR_PLUGIN_CONFIG_DIR") or os.path.join(
        os.path.expanduser("~"), ".config", "herdr", "plugins", "config",
        "fantoine.run-targets",
    )
    return os.path.join(directory, SETTINGS_FILE)


def load_settings() -> tuple[Settings, list[str]]:
    """Read the settings file; its absence is the normal case, not a failure."""
    path = settings_path()
    try:
        with open(path, "r", encoding="utf-8") as handle:
            text = handle.read()
    except FileNotFoundError:
        return Settings(), []
    except OSError as error:
        sys.stderr.write(f"Could not read {path}: {error}\n")
        return Settings(), [f"{SETTINGS_FILE}: could not be read"]
    return parse_settings(text, SETTINGS_FILE)
