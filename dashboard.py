#!/usr/bin/env python3
"""Pane entry point: prepare the context, then start the TUI."""

from __future__ import annotations

import curses
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from run_targets import herdr
from run_targets.config import resolve_repo_root
from run_targets.state import (
    load_state,
    prune_state,
    register_control_pane,
    save_state,
)
from run_targets.tui import Dashboard, run_dashboard


def main() -> int:
    workspace_id = os.environ.get("HERDR_WORKSPACE_ID")
    pane_id = os.environ.get("HERDR_PANE_ID")
    if not workspace_id or not pane_id:
        sys.stderr.write(
            "No Herdr workspace or pane in the environment; nothing to show.\n"
        )
        return 1

    repo_root = resolve_repo_root(os.getcwd())
    if repo_root is None:
        sys.stderr.write(f"{os.getcwd()} is not inside a git repository.\n")
        return 1

    # The dashboard pane registers itself: it, and not the action, is what knows
    # its own id. It also knows its workspace, which the action cannot choose --
    # Herdr opens an overlay over whatever pane is active.
    try:
        live = {pane.get("workspace_id") for pane in herdr.list_panes()}
        state = prune_state(
            load_state(), {item for item in live if isinstance(item, str)}
        )
        save_state(state)
    except RuntimeError as error:
        # Pruning is an optimisation, not a condition for opening: a failing
        # Herdr call must not keep the dashboard from showing up.
        sys.stderr.write(f"Could not prune the plugin state: {error}\n")
    # Reloaded right before writing so we do not overwrite another dashboard
    # that registered itself in the meantime.
    register_control_pane(workspace_id, pane_id)

    dashboard = Dashboard(workspace_id=workspace_id, repo_root=repo_root, warnings=[])
    curses.wrapper(run_dashboard, dashboard)

    # On the way out the pane disappears: stop claiming to be the dashboard.
    state = load_state()
    record = state.get(workspace_id)
    if record is not None and record.control_pane_id == pane_id:
        record.control_pane_id = None
        save_state(state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
