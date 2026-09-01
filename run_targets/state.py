"""Journal of the panes the plugin created, keyed by tab.

This journal is the only authority on *what the plugin owns*. It never states a
service's state -- that is always re-observed -- but it does say which panes
belong to it, which is what keeps it from acting on anyone else's.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field

STATE_FILE = "tabs.json"


@dataclass
class ServiceRecord:
    """A service's pane, and whether its stop was requested."""

    pane_id: str
    stop_requested: bool = False


@dataclass
class TabRecord:
    """What the plugin owns inside one tab."""

    control_pane_id: str | None = None
    last_service_pane_id: str | None = None
    services: dict[str, ServiceRecord] = field(default_factory=dict)


def state_dir() -> str:
    return os.environ.get("HERDR_PLUGIN_STATE_DIR") or os.path.join(
        os.path.expanduser("~"), ".local", "state", "herdr-run-targets"
    )


def state_path() -> str:
    return os.path.join(state_dir(), STATE_FILE)


def _tab_from_json(raw: object) -> TabRecord | None:
    if not isinstance(raw, dict):
        return None
    services: dict[str, ServiceRecord] = {}
    raw_services = raw.get("services")
    if isinstance(raw_services, dict):
        for name, entry in raw_services.items():
            if not isinstance(name, str) or not isinstance(entry, dict):
                continue
            pane_id = entry.get("pane_id")
            if not isinstance(pane_id, str):
                continue
            services[name] = ServiceRecord(
                pane_id=pane_id, stop_requested=bool(entry.get("stop_requested"))
            )
    control = raw.get("control_pane_id")
    last = raw.get("last_service_pane_id")
    return TabRecord(
        control_pane_id=control if isinstance(control, str) else None,
        last_service_pane_id=last if isinstance(last, str) else None,
        services=services,
    )


def load_state() -> dict[str, TabRecord]:
    """Read the journal. An unreadable file is treated as empty, and reported.

    Losing track of existing panes is harmless; acting on the wrong ones is not.
    Doubt therefore always resolves towards forgetting.
    """
    try:
        with open(state_path(), "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as error:
        sys.stderr.write(f"Could not read the plugin state at {state_path()}: {error}\n")
        return {}

    if not isinstance(payload, dict):
        return {}
    state: dict[str, TabRecord] = {}
    for tab_id, raw in payload.items():
        record = _tab_from_json(raw)
        if isinstance(tab_id, str) and record is not None:
            state[tab_id] = record
    return state


def save_state(state: dict[str, TabRecord]) -> None:
    """Write the journal through a temporary file, then `os.replace`.

    The replacement is atomic: an interruption leaves the old file intact rather
    than truncated JSON, which a later read would take for an absence of tracked
    panes.
    """
    payload = {
        tab_id: {
            "control_pane_id": record.control_pane_id,
            "last_service_pane_id": record.last_service_pane_id,
            "services": {
                name: {"pane_id": service.pane_id, "stop_requested": service.stop_requested}
                for name, service in record.services.items()
            },
        }
        for tab_id, record in state.items()
    }
    path = state_path()
    temporary = path + ".tmp"
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
        os.replace(temporary, path)
    except OSError as error:
        sys.stderr.write(f"Could not write the plugin state at {path}: {error}\n")


def register_control_pane(tab_id: str, pane_id: str) -> None:
    """Record a control pane without erasing the other tabs.

    The journal is rewritten whole on every save. Two dashboards starting at the
    same instant would therefore both read the previous state, and the second
    would write over the first one's entry -- seen for real, a workspace entry
    lost and its toggle recognising nothing any more. Reloading right before
    writing narrows the window to a few microseconds; only a lock would close it
    entirely, which a plugin where two dashboards rarely start together does not
    justify.
    """
    state = load_state()
    record = state.setdefault(tab_id, TabRecord())
    record.control_pane_id = pane_id
    save_state(state)


def prune_state(
    state: dict[str, TabRecord], live_tab_ids: set[str]
) -> dict[str, TabRecord]:
    """Drop the tabs that no longer exist."""
    return {tab_id: record for tab_id, record in state.items() if tab_id in live_tab_ids}
