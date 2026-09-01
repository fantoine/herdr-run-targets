#!/usr/bin/env python3
"""Action `toggle` : ouvre, rouvre ou ferme le tableau de bord."""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from run_targets import TAB_OWNED_ENV, herdr
from run_targets.state import TabRecord, load_state, save_state

PLUGIN_ID = "fantoine.run-targets"
ENTRYPOINT = "dashboard"


def live_service_pane(record: TabRecord, live_panes: dict[str, dict]) -> str | None:
    """Un pane de service encore vivant de l'onglet, pour servir de point de split."""
    for service in record.services.values():
        if service.pane_id in live_panes:
            return service.pane_id
    return None


def tab_workspace_id(record: TabRecord, live_panes: dict[str, dict]) -> str | None:
    """Le workspace d'un onglet suivi, déduit de ses panes encore vivants.

    Le déduire plutôt que le stocker évite d'ajouter un champ au journal et
    garde lisibles les journaux écrits par une version antérieure.
    """
    candidates = [record.control_pane_id]
    candidates += [service.pane_id for service in record.services.values()]
    for pane_id in candidates:
        pane = live_panes.get(pane_id) if pane_id else None
        if isinstance(pane, dict) and isinstance(pane.get("workspace_id"), str):
            return pane["workspace_id"]
    return None


def current_workspace_id() -> str | None:
    """Le workspace depuis lequel l'action est invoquée.

    Herdr injecte `HERDR_WORKSPACE_ID` dans chaque commande de plugin ; le
    contexte de l'action porte la même information et sert de repli.
    """
    from_env = os.environ.get("HERDR_WORKSPACE_ID")
    if from_env:
        return from_env
    try:
        payload = json.loads(os.environ.get("HERDR_PLUGIN_CONTEXT_JSON") or "{}")
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    workspace_id = payload.get("workspace_id")
    return workspace_id if isinstance(workspace_id, str) and workspace_id else None


def decide_toggle(
    state: dict[str, TabRecord],
    live_panes: dict[str, dict],
    workspace_id: str | None = None,
) -> tuple[str, str | None]:
    """Ce que `toggle` doit faire, au vu du journal et des panes vivants.

    L'ordre compte : fermer si le tableau de bord est là, sinon le rouvrir dans
    l'onglet qui héberge encore des services, sinon seulement créer. Sans le
    cas intermédiaire, fermer le tableau de bord laisserait un onglet orphelin
    qu'aucun raccourci ne saurait retrouver.

    Fermer un onglet sans le moindre service vivant ferme l'onglet entier, pas
    seulement le pane : sinon il resterait un onglet que ni le cas « rouvrir »
    ni le cas « fermer » ne reconnaîtrait, et chaque bascule suivante en
    créerait un de plus. Ce que la bascule a créé, elle le retire.
    """

    def ours(record: TabRecord) -> bool:
        # Le journal est global : sans ce filtre, la bascule agit sur le premier
        # onglet suivi venu, y compris dans un autre worktree — elle a déjà
        # fermé le tableau de bord d'un workspace où l'utilisateur travaillait.
        # Workspace inconnu : on retombe sur l'ancien comportement plutôt que
        # de ne rien faire du tout.
        if workspace_id is None:
            return True
        found = tab_workspace_id(record, live_panes)
        return found is None or found == workspace_id

    for tab_id, record in state.items():
        if not ours(record):
            continue
        if record.control_pane_id and record.control_pane_id in live_panes:
            if live_service_pane(record, live_panes) is None:
                return "close_tab", tab_id
            return "close", record.control_pane_id
    for tab_id, record in state.items():
        if not ours(record):
            continue
        if live_service_pane(record, live_panes) is not None:
            return "reopen", tab_id
    return "create", None


def workspace_cwd_from_context() -> str | None:
    """Le répertoire de travail du workspace, lu dans le contexte de l'action.

    Herdr injecte `HERDR_PLUGIN_CONTEXT_JSON` ; c'est la seule source du
    répertoire du projet quand aucun pane de service n'existe encore pour en
    hériter. Variable absente, JSON invalide, payload non objet ou
    `workspace_cwd` absent ou non textuel dégradent tous vers None plutôt que
    de faire échouer l'action. La dégradation n'est pas anodine : sans `--cwd`,
    le pane hérite du répertoire du plugin — lui-même un dépôt git — et le
    tableau de bord annonce « aucune target » pour le mauvais dépôt. C'est
    pourquoi l'en-tête nomme le dépôt résolu : faute visible plutôt que
    réponse fausse et silencieuse.
    """
    try:
        payload = json.loads(os.environ.get("HERDR_PLUGIN_CONTEXT_JSON") or "{}")
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    cwd = payload.get("workspace_cwd")
    return cwd if isinstance(cwd, str) and cwd else None


def main() -> int:
    try:
        live_panes = {
            pane["pane_id"]: pane
            for pane in herdr.list_panes()
            if isinstance(pane.get("pane_id"), str)
        }
    except RuntimeError as error:
        sys.stderr.write(f"Could not list panes: {error}\n")
        return 1

    state = load_state()
    workspace_id = current_workspace_id()
    decision, argument = decide_toggle(state, live_panes, workspace_id)

    try:
        if decision == "close":
            herdr.pane_close(argument)
            print(f"Closed the dashboard pane {argument}.")
        elif decision == "close_tab":
            # Le garde-fou structurel du plugin vaut aussi pour les onglets :
            # on ne ferme que ce que le journal réclame comme sien.
            if argument not in state:
                sys.stderr.write(f"{argument} is not a run-targets tab.\n")
                return 1
            herdr.tab_close(argument)
            del state[argument]
            save_state(state)
            print(f"Closed the run-targets tab {argument}.")
        elif decision == "reopen":
            # Le tableau de bord se réinstalle dans l'onglet existant : il est
            # ouvert comme un split d'un pane de service encore vivant, cible
            # explicite requise par `--placement split` pour savoir où scinder.
            record = state[argument]
            open_args = [
                "plugin", "pane", "open",
                "--plugin", PLUGIN_ID,
                "--entrypoint", ENTRYPOINT,
                "--placement", "split",
            ]
            target_pane = live_service_pane(record, live_panes)
            if target_pane is not None:
                open_args += ["--target-pane", target_pane]
                # Herdr lance les commandes de plugin dans le répertoire du
                # plugin, pas celui du dépôt : sans `--cwd`, le tableau de bord
                # rouvert résoudrait sa propre racine au lieu de celle du
                # dépôt observé par le pane de service qu'il rejoint.
                pane_cwd = live_panes.get(target_pane, {}).get("cwd")
                if isinstance(pane_cwd, str) and pane_cwd:
                    open_args += ["--cwd", pane_cwd]
            herdr.herdr_result(open_args)
            print(f"Reopened the dashboard in {argument}.")
        else:
            open_args = [
                "plugin", "pane", "open",
                "--plugin", PLUGIN_ID,
                "--entrypoint", ENTRYPOINT,
                "--placement", "tab",
                "--env", f"{TAB_OWNED_ENV}=1",
            ]
            # Sans cela l'onglet naît dans le workspace focalisé, qui n'est pas
            # forcément celui d'où l'on invoque — incohérent avec la recherche,
            # désormais restreinte au workspace courant.
            # L'id n'est passé que si le workspace vit encore : fermer le
            # dernier onglet d'un workspace ferme le workspace lui-même, donc
            # l'id courant peut désigner quelque chose de disparu. Herdr
            # répondrait `workspace_not_found` et la bascule échouerait au lieu
            # de retomber sur le workspace focalisé.
            live_workspaces = {
                pane.get("workspace_id")
                for pane in live_panes.values()
                if isinstance(pane, dict)
            }
            if workspace_id is not None and workspace_id in live_workspaces:
                open_args += ["--workspace", workspace_id]
            # Pas de pane de service pour hériter d'un répertoire : c'est le
            # contexte de l'action, injecté par Herdr, qui porte celui du
            # workspace de l'utilisateur.
            workspace_cwd = workspace_cwd_from_context()
            if workspace_cwd is not None:
                open_args += ["--cwd", workspace_cwd]
            herdr.herdr_result(open_args)
            print("Opened a run-targets tab.")
    except RuntimeError as error:
        sys.stderr.write(f"{error}\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
