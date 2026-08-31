"""Journal des panes que le plugin a créés, indexé par onglet.

Ce journal est la seule autorité sur *ce que le plugin possède*. Il ne dit
jamais l'état d'un service — celui-ci est toujours réobservé — mais il dit
quels panes lui appartiennent, ce qui l'empêche d'agir sur ceux des autres.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field

STATE_FILE = "tabs.json"


@dataclass
class ServiceRecord:
    """Le pane d'un service, et si son arrêt a été demandé."""

    pane_id: str
    stop_requested: bool = False


@dataclass
class TabRecord:
    """Ce que le plugin possède dans un onglet."""

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
    """Lit le journal. Un fichier illisible est traité comme vide et signalé.

    Perdre la trace de panes existants est bénin ; agir sur les mauvais ne
    l'est pas. Le doute se résout donc toujours vers l'oubli.
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
    """Écrit le journal par fichier temporaire puis `os.replace`.

    Le remplacement est atomique : une interruption laisse l'ancien fichier
    intact plutôt qu'un JSON tronqué, qu'une lecture ultérieure prendrait pour
    une absence de panes suivis.
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


def prune_state(
    state: dict[str, TabRecord], live_tab_ids: set[str]
) -> dict[str, TabRecord]:
    """Écarte les onglets qui n'existent plus."""
    return {tab_id: record for tab_id, record in state.items() if tab_id in live_tab_ids}
