"""Journal of what the plugin owns in a workspace.

This journal is the only authority on *what the plugin owns*. It never states a
service's state -- that is always re-observed -- but it does say which tabs and
panes belong to it, which is what keeps it from acting on anyone else's.

Keyed by workspace, not by tab: the dashboard is a floating overlay, and Herdr
resolves an overlay's host tab from whatever pane is active when it opens, so its
tab id changes from one opening to the next. The workspace does not.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field

STATE_FILE = "tabs.json"


@dataclass
class ServiceRecord:
    """A service's tab and pane, and whether its stop was requested."""

    tab_id: str
    pane_id: str
    stop_requested: bool = False


@dataclass
class WorkspaceRecord:
    """What the plugin owns inside one workspace."""

    control_pane_id: str | None = None
    services: dict[str, ServiceRecord] = field(default_factory=dict)


def state_dir() -> str:
    return os.environ.get("HERDR_PLUGIN_STATE_DIR") or os.path.join(
        os.path.expanduser("~"), ".local", "state", "herdr-run-targets"
    )


def state_path() -> str:
    return os.path.join(state_dir(), STATE_FILE)


def _services_from_json(raw: object) -> dict[str, ServiceRecord] | None:
    """Read a workspace's services, or None when the shape is not ours.

    A service without a `tab_id` comes from the 0.1.0 journal, where services
    were panes in the dashboard's own tab. Closing a service now means closing
    its tab, so a record that cannot name one is not something to act on.
    """
    services: dict[str, ServiceRecord] = {}
    if raw is None:
        return services
    if not isinstance(raw, dict):
        return None
    for name, entry in raw.items():
        if not isinstance(name, str) or not isinstance(entry, dict):
            continue
        tab_id = entry.get("tab_id")
        pane_id = entry.get("pane_id")
        if not isinstance(tab_id, str) or not isinstance(pane_id, str):
            return None
        services[name] = ServiceRecord(
            tab_id=tab_id,
            pane_id=pane_id,
            stop_requested=bool(entry.get("stop_requested")),
        )
    return services


def _workspace_from_json(raw: object) -> WorkspaceRecord | None:
    if not isinstance(raw, dict):
        return None
    services = _services_from_json(raw.get("services"))
    if services is None:
        return None
    control = raw.get("control_pane_id")
    return WorkspaceRecord(
        control_pane_id=control if isinstance(control, str) else None,
        services=services,
    )


def load_state() -> dict[str, WorkspaceRecord]:
    """Read the journal. Anything unrecognised is treated as absent, and reported.

    Losing track of existing tabs is harmless; acting on the wrong ones is not.
    Doubt therefore always resolves towards forgetting -- which is also how a
    0.1.0 journal is handled: its keys are tab ids, which carry a colon where a
    workspace id does not, so those entries are dropped rather than migrated.
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
    state: dict[str, WorkspaceRecord] = {}
    for key, raw in payload.items():
        if not isinstance(key, str):
            continue
        if ":" in key:
            sys.stderr.write(
                f"Dropped {key!r} from the plugin state: keyed by tab, not workspace.\n"
            )
            continue
        record = _workspace_from_json(raw)
        if record is None:
            sys.stderr.write(
                f"Dropped {key!r} from the plugin state: unrecognised service records.\n"
            )
            continue
        state[key] = record
    return state


def save_state(state: dict[str, WorkspaceRecord]) -> None:
    """Write the journal through a temporary file, then `os.replace`.

    The replacement is atomic: an interruption leaves the old file intact rather
    than truncated JSON, which a later read would take for an absence of tracked
    tabs.
    """
    payload = {
        workspace_id: {
            "control_pane_id": record.control_pane_id,
            "services": {
                name: {
                    "tab_id": service.tab_id,
                    "pane_id": service.pane_id,
                    "stop_requested": service.stop_requested,
                }
                for name, service in record.services.items()
            },
        }
        for workspace_id, record in state.items()
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


def register_control_pane(workspace_id: str, pane_id: str) -> None:
    """Record a dashboard pane without erasing the other workspaces.

    The journal is rewritten whole on every save. Two dashboards starting at the
    same instant would therefore both read the previous state, and the second
    would write over the first one's entry -- seen for real, a workspace entry
    lost and its toggle recognising nothing any more. Reloading right before
    writing narrows the window to a few microseconds; only a lock would close it
    entirely, which a plugin where two dashboards rarely start together does not
    justify.
    """
    state = load_state()
    record = state.setdefault(workspace_id, WorkspaceRecord())
    record.control_pane_id = pane_id
    save_state(state)


def prune_state(
    state: dict[str, WorkspaceRecord], live_workspace_ids: set[str]
) -> dict[str, WorkspaceRecord]:
    """Drop the workspaces that no longer exist."""
    return {
        workspace_id: record
        for workspace_id, record in state.items()
        if workspace_id in live_workspace_ids
    }
