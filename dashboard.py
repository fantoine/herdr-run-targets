#!/usr/bin/env python3
"""Point d'entrée du pane : prépare le contexte puis lance le TUI."""

from __future__ import annotations

import curses
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from run_targets.config import resolve_repo_root
from run_targets.state import TabRecord, load_state, prune_state, save_state
from run_targets.tui import Dashboard, run_dashboard
from run_targets import herdr


def main() -> int:
    tab_id = os.environ.get("HERDR_TAB_ID")
    pane_id = os.environ.get("HERDR_PANE_ID")
    if not tab_id or not pane_id:
        sys.stderr.write("No Herdr tab or pane in the environment; nothing to show.\n")
        return 1

    repo_root = resolve_repo_root(os.getcwd())
    if repo_root is None:
        sys.stderr.write(f"{os.getcwd()} is not inside a git repository.\n")
        return 1

    # Le pane de contrôle s'enregistre lui-même : c'est lui, et non l'action,
    # qui connaît son propre identifiant.
    state = load_state()
    try:
        live_tabs = {pane.get("tab_id") for pane in herdr.list_panes()}
        state = prune_state(state, {tab for tab in live_tabs if isinstance(tab, str)})
    except RuntimeError as error:
        # Élaguer est une optimisation, pas une condition d'ouverture : un appel
        # Herdr en échec ne doit pas empêcher le tableau de bord de s'afficher.
        sys.stderr.write(f"Could not prune the plugin state: {error}\n")
    record = state.setdefault(tab_id, TabRecord())
    record.control_pane_id = pane_id
    save_state(state)

    dashboard = Dashboard(tab_id=tab_id, repo_root=repo_root, warnings=[])
    curses.wrapper(run_dashboard, dashboard)

    # En sortie, le pane disparaît : on ne se déclare plus comme pane de contrôle.
    state = load_state()
    record = state.get(tab_id)
    if record is not None and record.control_pane_id == pane_id:
        record.control_pane_id = None
        save_state(state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
