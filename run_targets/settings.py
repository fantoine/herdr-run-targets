"""The plugin's own settings, read from its Herdr config directory."""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass

import tomllib

SETTINGS_FILE = "config.toml"

FOCUS_STAY = "stay"
FOCUS_FIRST = "first"
FOCUS_LAST = "last"
FOCUS_MODES = (FOCUS_STAY, FOCUS_FIRST, FOCUS_LAST)

PLACEMENT_OVERLAY = "overlay"
PLACEMENT_POPUP = "popup"
PLACEMENTS = (PLACEMENT_OVERLAY, PLACEMENT_POPUP)

# Herdr takes a popup dimension as terminal cells (`24`) or a percentage of the
# window (`"60%"`), and falls back to a half-size popup when one is omitted.
DIMENSION = re.compile(r"^[0-9]+%?$")


@dataclass(frozen=True)
class Settings:
    """What the user can tune. Every default leaves the plugin as it ships."""

    label_prefix: str = ""
    label_suffix: str = ""
    focus_mode: str = FOCUS_STAY
    placement: str = PLACEMENT_OVERLAY
    popup_width: str | None = None
    popup_height: str | None = None


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

    raw_placement = dashboard.get("placement")
    placement = _text(raw_placement)
    if raw_placement is not None and placement not in PLACEMENTS:
        warnings.append(
            f"{source}: unknown placement {raw_placement!r}; using {PLACEMENT_OVERLAY}"
        )
        placement = None

    dimensions: dict[str, str | None] = {}
    for key in ("popup_width", "popup_height"):
        raw = dashboard.get(key)
        if raw is None:
            dimensions[key] = None
            continue
        # A number in the TOML is as valid as a string of cells; anything else
        # is dropped rather than passed on to fail the open.
        value = str(raw) if isinstance(raw, int) and not isinstance(raw, bool) else _text(raw)
        if value is None or not DIMENSION.match(value):
            warnings.append(f"{source}: unsupported {key} {raw!r}; using the default")
            dimensions[key] = None
        else:
            dimensions[key] = value

    return (
        Settings(
            label_prefix=prefix or "",
            label_suffix=suffix or "",
            focus_mode=mode or FOCUS_STAY,
            placement=placement or PLACEMENT_OVERLAY,
            popup_width=dimensions["popup_width"],
            popup_height=dimensions["popup_height"],
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
