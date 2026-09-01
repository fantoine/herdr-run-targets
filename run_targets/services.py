"""État des services et décision d'action.

Tout ce fichier est pur : il ne parle ni à Herdr ni au terminal, seulement à
des valeurs. C'est ce qui rend la table d'idempotence vérifiable case par case.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Sequence

from .config import Target
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

# Attente d'un arrêt avant de relancer dans le même pane. Un serveur de
# développement qui gère SIGINT met bien plus que le temps de livraison de la
# touche à rendre la main ; taper la commande entre-temps la ferait avaler par
# l'entrée standard du processus mourant, et le service resterait arrêté.
STOP_POLL_SECONDS = 0.1
STOP_POLL_ATTEMPTS = 30

# Indirection pour que les tests n'attendent jamais réellement.
_sleep = time.sleep


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


def restart_blocked_message(name: str) -> str:
    """Le message d'un redémarrage abandonné faute d'arrêt.

    Ne rien dire laisserait croire à un redémarrage réussi, alors que le
    service tourne encore avec son ancien code.
    """
    return f"{name}: still running after stop, restart skipped"


def skip_message(name: str, action: str, state: str) -> str:
    """Le message d'une action sans effet.

    Une action ignorée en silence est indistinguable d'une touche non prise.
    """
    return f"{name}: already {state}, {action} skipped"


@dataclass
class ServiceView:
    """Une target et ce qu'on observe d'elle en ce moment."""

    target: Target
    state: str
    pane_id: str | None


def observe(
    tab: TabRecord, targets: Sequence[Target], client, tab_id: str
) -> list[ServiceView]:
    """Recalcule l'état de chaque target depuis Herdr.

    L'état n'est jamais lu depuis le journal seul : celui-ci dit quels panes
    appartiennent au plugin, l'observation dit ce qui s'y passe.
    """
    live = set(client.panes_in_tab(tab_id))
    views: list[ServiceView] = []
    for target in targets:
        record = tab.services.get(target.name)
        pane_alive = record is not None and record.pane_id in live
        foreground = False
        if pane_alive:
            foreground = client.has_foreground_command(client.process_info(record.pane_id))
        views.append(
            ServiceView(
                target=target,
                state=derive_state(record, pane_alive, foreground),
                pane_id=record.pane_id if record is not None else None,
            )
        )
    return views


def _start_in_pane(tab: TabRecord, view: ServiceView, pane_id: str, client) -> None:
    # Le pane est enregistré avant le lancement : si `pane_run` échoue, le service
    # reste associé à son pane plutôt que de laisser un pane vivant qu'aucune
    # entrée du journal ne réclame — un « start » suivant le réutiliserait, là où
    # un pane orphelin ferait splitter un pane de plus à chaque tentative.
    tab.services[view.target.name] = ServiceRecord(pane_id=pane_id, stop_requested=False)
    client.pane_run(pane_id, view.target.command)


def _create_and_start(
    tab: TabRecord, view: ServiceView, repo_root: str, client, tab_id: str
) -> None:
    live = set(client.panes_in_tab(tab_id))
    destination = next_split_target(tab, live)
    if destination is None:
        raise RuntimeError("no pane of ours to split from")
    pane_id, direction = destination
    cwd = os.path.join(repo_root, view.target.cwd) if view.target.cwd else repo_root
    ratio = 0.25 if direction == "right" else None
    new_pane = client.pane_split(
        pane_id, direction, ratio=ratio, cwd=cwd, env=view.target.env or None
    )
    tab.last_service_pane_id = new_pane
    # Le pane porte le nom de sa target : dans une colonne de services, un titre
    # de terminal générique ne dit rien de ce qui tourne là. Le renommage
    # précède le lancement, sinon la commande poserait son propre titre.
    # Un échec ici ne doit pas empêcher le service de démarrer.
    try:
        client.pane_rename(new_pane, view.target.name)
    except RuntimeError:
        pass
    _start_in_pane(tab, view, new_pane, client)


def _wait_until_stopped(pane_id: str, client) -> bool:
    """Sonde le premier plan du pane jusqu'à ce qu'il se libère.

    Rend vrai dès que plus rien n'occupe le pane, faux si le budget d'attente
    expire. Le budget est borné : un service qui refuse de mourir ne doit pas
    figer le tableau de bord.
    """
    for _ in range(STOP_POLL_ATTEMPTS):
        if not client.has_foreground_command(client.process_info(pane_id)):
            return True
        _sleep(STOP_POLL_SECONDS)
    return not client.has_foreground_command(client.process_info(pane_id))


def _forget(tab: TabRecord, name: str) -> None:
    """Retire un service du journal, en gardant `last_service_pane_id` cohérent."""
    record = tab.services.pop(name, None)
    if record is not None and tab.last_service_pane_id == record.pane_id:
        remaining = [service.pane_id for service in tab.services.values()]
        tab.last_service_pane_id = remaining[-1] if remaining else None


def apply_action(
    action: str,
    views: Sequence[ServiceView],
    tab: TabRecord,
    repo_root: str,
    client,
    tab_id: str,
) -> list[str]:
    """Applique une action à une sélection, et rend les messages à afficher.

    Une target en échec n'interrompt pas les suivantes : un lot à moitié lancé
    vaut mieux qu'un lot abandonné à la première erreur.
    """
    messages: list[str] = []
    for view in views:
        operation = plan_action(action, view.state)
        name = view.target.name
        try:
            if operation == OP_SKIP:
                messages.append(skip_message(name, action, view.state))
            elif operation == OP_CREATE:
                _create_and_start(tab, view, repo_root, client, tab_id)
            elif operation == OP_START:
                _start_in_pane(tab, view, view.pane_id, client)
            elif operation == OP_STOP:
                client.pane_send_keys(view.pane_id, "ctrl+c")
                record = tab.services.get(name)
                if record is not None:
                    record.stop_requested = True
            elif operation == OP_RESTART:
                client.pane_send_keys(view.pane_id, "ctrl+c")
                if _wait_until_stopped(view.pane_id, client):
                    _start_in_pane(tab, view, view.pane_id, client)
                else:
                    messages.append(restart_blocked_message(name))
            elif operation == OP_CLOSE:
                client.pane_close(view.pane_id)
                _forget(tab, name)
            elif operation == OP_FORGET:
                _forget(tab, name)
        except RuntimeError as error:
            messages.append(f"{name}: {error}")
    return messages
