"""État des services et décision d'action.

Tout ce fichier est pur : il ne parle ni à Herdr ni au terminal, seulement à
des valeurs. C'est ce qui rend la table d'idempotence vérifiable case par case.
"""

from __future__ import annotations

from typing import Sequence

from .state import ServiceRecord, TabRecord

RUNNING = "running"
STOPPED = "stopped"
EXITED = "exited"
IDLE = "idle"
GONE = "gone"

OP_START = "start"
OP_CREATE = "create"
OP_STOP = "stop"
OP_RESTART = "restart"
OP_CLOSE = "close"
OP_FORGET = "forget"
OP_SKIP = "skip"


def derive_state(
    record: ServiceRecord | None, pane_alive: bool, foreground: bool
) -> str:
    """L'état d'un service, croisant l'observation et ce que le plugin a demandé.

    `process-info` dit seulement quel processus occupe le premier plan, jamais
    pourquoi il est parti : un service qui se termine seul laisse exactement la
    même trace qu'un ctrl+C. Seul `stop_requested`, que le plugin pose lui-même,
    sépare `stopped` de `exited`.
    """
    if record is None:
        return IDLE
    if not pane_alive:
        return GONE
    if foreground:
        return RUNNING
    return STOPPED if record.stop_requested else EXITED


_PLAN: dict[str, dict[str, str]] = {
    "start": {
        RUNNING: OP_SKIP,
        STOPPED: OP_START,
        EXITED: OP_START,
        IDLE: OP_CREATE,
        GONE: OP_CREATE,
    },
    "stop": {
        RUNNING: OP_STOP,
        STOPPED: OP_SKIP,
        EXITED: OP_SKIP,
        IDLE: OP_SKIP,
        GONE: OP_SKIP,
    },
    # Redémarrer un service arrêté le démarre : l'état visé par un redémarrage
    # est « en marche », l'ignorer irait contre l'intention.
    "restart": {
        RUNNING: OP_RESTART,
        STOPPED: OP_START,
        EXITED: OP_START,
        IDLE: OP_CREATE,
        GONE: OP_CREATE,
    },
    "close": {
        RUNNING: OP_CLOSE,
        STOPPED: OP_CLOSE,
        EXITED: OP_CLOSE,
        IDLE: OP_SKIP,
        GONE: OP_FORGET,
    },
}


def plan_action(action: str, state: str) -> str:
    """L'opération à mener pour une action demandée sur un état donné."""
    return _PLAN.get(action, {}).get(state, OP_SKIP)


def next_split_target(
    tab: TabRecord, live_pane_ids: set[str]
) -> tuple[str, str] | None:
    """Le pane à splitter pour accueillir un nouveau service, et la direction.

    Ne considère que des panes du journal. Un onglet neuf contient déjà le pane
    docké de `herdr-sidebar` ; splitter « le dernier pane de l'onglet » l'a
    coupé en deux pendant la sonde de conception.
    """
    last = tab.last_service_pane_id
    if last is not None and last in live_pane_ids:
        return last, "down"
    control = tab.control_pane_id
    if control is not None and control in live_pane_ids:
        return control, "right"
    return None


def resolve_selection(
    names: Sequence[str], checked: set[str], cursor: str | None
) -> list[str]:
    """Les targets visées : les cochées, sinon celle sous le curseur.

    Convention des gestionnaires de fichiers en TUI : on ne coche que pour agir
    en lot, sinon on vise avec le curseur.
    """
    selected = [name for name in names if name in checked]
    if selected:
        return selected
    if cursor is not None and cursor in names:
        return [cursor]
    return []


def skip_message(name: str, action: str, state: str) -> str:
    """Le message d'une action sans effet.

    Une action ignorée en silence est indistinguable d'une touche non prise.
    """
    return f"{name}: already {state}, {action} skipped"
