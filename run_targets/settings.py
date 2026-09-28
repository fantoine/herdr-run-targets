"""The plugin's settings: its Herdr config directory, then the repository.

Three layers, merged key by key: the plugin's `config.toml`, the repository's
`.herdr-run.toml`, then `.herdr-run.local.toml`. A file sets only what it names;
everything else comes from the layer below.
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass

import tomllib

from .config import LOCAL_CONFIG_FILE, TEAM_CONFIG_FILE

SETTINGS_FILE = "config.toml"

FOCUS_STAY = "stay"
FOCUS_FIRST = "first"
FOCUS_LAST = "last"
FOCUS_CLOSE = "close"
FOCUS_MODES = (FOCUS_STAY, FOCUS_FIRST, FOCUS_LAST, FOCUS_CLOSE)

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


# Read by `toggle` to open the pane, before any repository is known: a project
# file cannot move them, so it is told so rather than silently ignored.
PLUGIN_ONLY_KEYS = ("placement", "popup_width", "popup_height")


def parse_layer(
    text: str, source: str, *, project: bool = False
) -> tuple[dict[str, str], list[str]]:
    """Parse one settings file into the keys it actually sets.

    A missing or invalid key is absent from the result, so the layer below
    shows through. Every failure degrades rather than stopping the dashboard: a
    typo in an optional file must not cost the user their services. Unknown keys
    pass silently, so a file written for a newer version stays readable.

    `project` marks a repository file. Its broken TOML is left unreported --
    the target loader reads the same file and already says so -- and it cannot
    set the plugin-only keys.
    """
    try:
        document = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        return {}, [] if project else [f"{source}: invalid TOML ({error})"]

    layer: dict[str, str] = {}
    warnings: list[str] = []
    # A project value that fails falls back to the plugin config, not to the
    # shipped default, so the message cannot name the value that wins.
    fallback = "ignored" if project else f"using {FOCUS_STAY}"

    tabs = document.get("tabs")
    tabs = tabs if isinstance(tabs, dict) else {}
    for key in ("label_prefix", "label_suffix"):
        raw = tabs.get(key)
        if raw is None:
            continue
        if isinstance(raw, str):
            layer[key] = raw
        else:
            warnings.append(f"{source}: tabs.{key} must be a string; ignored")

    dashboard = document.get("dashboard")
    dashboard = dashboard if isinstance(dashboard, dict) else {}
    raw_mode = dashboard.get("focus_mode")
    if raw_mode is not None:
        if _text(raw_mode) in FOCUS_MODES:
            layer["focus_mode"] = raw_mode
        else:
            warnings.append(
                f"{source}: unknown focus_mode {raw_mode!r}; {fallback}"
            )

    if project:
        for key in PLUGIN_ONLY_KEYS:
            if dashboard.get(key) is not None:
                warnings.append(
                    f"{source}: dashboard.{key} is only read from the plugin's "
                    f"{SETTINGS_FILE}; ignored"
                )
        return layer, warnings

    raw_placement = dashboard.get("placement")
    if raw_placement is not None:
        if _text(raw_placement) in PLACEMENTS:
            layer["placement"] = raw_placement
        else:
            warnings.append(
                f"{source}: unknown placement {raw_placement!r}; using {PLACEMENT_OVERLAY}"
            )

    for key in ("popup_width", "popup_height"):
        raw = dashboard.get(key)
        if raw is None:
            continue
        # A number in the TOML is as valid as a string of cells; anything else
        # is dropped rather than passed on to fail the open.
        value = str(raw) if isinstance(raw, int) and not isinstance(raw, bool) else _text(raw)
        if value is None or not DIMENSION.match(value):
            warnings.append(f"{source}: unsupported {key} {raw!r}; using the default")
        else:
            layer[key] = value

    return layer, warnings


def settings_from(layer: dict[str, str]) -> Settings:
    """The settings a merged layer describes; what it leaves out is the default."""
    return Settings(**layer)


def parse_settings(text: str, source: str) -> tuple[Settings, list[str]]:
    """Parse the plugin's own settings file on its own."""
    layer, warnings = parse_layer(text, source)
    return settings_from(layer), warnings


def settings_path() -> str:
    """Where Herdr keeps this plugin's configuration."""
    directory = os.environ.get("HERDR_PLUGIN_CONFIG_DIR") or os.path.join(
        os.path.expanduser("~"), ".config", "herdr", "plugins", "config",
        "fantoine.run-targets",
    )
    return os.path.join(directory, SETTINGS_FILE)


def _read(path: str) -> str | None:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read()
    except FileNotFoundError:
        return None


def load_settings(repo_root: str | None = None) -> tuple[Settings, list[str]]:
    """Merge the plugin config with the repository's files, when there is one.

    Every file is optional; their absence is the normal case, not a failure.
    Without `repo_root` -- the toggle, which runs before a repository is known
    -- only the plugin config counts.
    """
    merged: dict[str, str] = {}
    warnings: list[str] = []

    path = settings_path()
    try:
        text = _read(path)
    except OSError as error:
        sys.stderr.write(f"Could not read {path}: {error}\n")
        text = None
        warnings.append(f"{SETTINGS_FILE}: could not be read")
    if text is not None:
        layer, layer_warnings = parse_layer(text, SETTINGS_FILE)
        merged.update(layer)
        warnings.extend(layer_warnings)

    if repo_root is not None:
        for filename in (TEAM_CONFIG_FILE, LOCAL_CONFIG_FILE):
            try:
                text = _read(os.path.join(repo_root, filename))
            except OSError:
                # The target loader reads the same file and reports it.
                continue
            if text is None:
                continue
            layer, layer_warnings = parse_layer(text, filename, project=True)
            merged.update(layer)
            warnings.extend(layer_warnings)

    return settings_from(merged), warnings
