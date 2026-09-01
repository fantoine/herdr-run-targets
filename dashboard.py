#!/usr/bin/env python3
"""Pane entry point: prepare the context, then start the TUI."""

from __future__ import annotations

import curses
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from run_targets.config import resolve_repo_root
from run_targets.state import (
    TabRecord,
    load_state,
    prune_state,
    register_control_pane,
    save_state,
)
from run_targets.tui import Dashboard, run_dashboard
from run_targets import TAB_LABEL, TAB_OWNED_ENV, herdr


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

    # The control pane registers itself: it, and not the action, is what knows
    # its own id.
    state = load_state()
    try:
        live_tabs = {pane.get("tab_id") for pane in herdr.list_panes()}
        state = prune_state(state, {tab for tab in live_tabs if isinstance(tab, str)})
    except RuntimeError as error:
        # Pruning is an optimisation, not a condition for opening: a failing
        # Herdr call must not keep the dashboard from showing up.
        sys.stderr.write(f"Could not prune the plugin state: {error}\n")
    # Reloaded right before writing so we do not overwrite another dashboard
    # that registered itself in the meantime.
    register_control_pane(tab_id, pane_id)

    # The tab is only named if we created it: `toggle` sets the mark on that
    # path alone. A failed rename has no consequence, the dashboard opens
    # regardless.
    if os.environ.get(TAB_OWNED_ENV):
        try:
            herdr.tab_rename(tab_id, TAB_LABEL)
        except RuntimeError as error:
            sys.stderr.write(f"Could not rename the tab: {error}\n")

    dashboard = Dashboard(tab_id=tab_id, repo_root=repo_root, warnings=[])
    curses.wrapper(run_dashboard, dashboard)

    # On the way out the pane disappears: stop claiming to be the control pane.
    state = load_state()
    record = state.get(tab_id)
    if record is not None and record.control_pane_id == pane_id:
        record.control_pane_id = None
        save_state(state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
