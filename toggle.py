#!/usr/bin/env python3
"""Action `toggle` : ouvre, rouvre ou ferme le tableau de bord."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from run_targets import herdr
from run_targets.state import TabRecord, load_state

PLUGIN_ID = "fantoine.run-targets"
ENTRYPOINT = "dashboard"


def decide_toggle(
    state: dict[str, TabRecord], live_panes: dict[str, dict]
) -> tuple[str, str | None]:
    """Ce que `toggle` doit faire, au vu du journal et des panes vivants.

    L'ordre compte : fermer si le tableau de bord est là, sinon le rouvrir dans
    l'onglet qui héberge encore des services, sinon seulement créer. Sans le
    cas intermédiaire, fermer le tableau de bord laisserait un onglet orphelin
    qu'aucun raccourci ne saurait retrouver.
    """
    for tab_id, record in state.items():
        if record.control_pane_id and record.control_pane_id in live_panes:
            return "close", record.control_pane_id
    for tab_id, record in state.items():
        if any(service.pane_id in live_panes for service in record.services.values()):
            return "reopen", tab_id
    return "create", None


def live_service_pane(record: TabRecord, live_panes: dict[str, dict]) -> str | None:
    """Un pane de service encore vivant de l'onglet, pour servir de point de split."""
    for service in record.services.values():
        if service.pane_id in live_panes:
            return service.pane_id
    return None


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
    decision, argument = decide_toggle(state, live_panes)

    try:
        if decision == "close":
            herdr.pane_close(argument)
            print(f"Closed the dashboard pane {argument}.")
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
            herdr.herdr_result(open_args)
            print(f"Reopened the dashboard in {argument}.")
        else:
            herdr.herdr_result(
                [
                    "plugin", "pane", "open",
                    "--plugin", PLUGIN_ID,
                    "--entrypoint", ENTRYPOINT,
                    "--placement", "tab",
                ]
            )
            print("Opened a run-targets tab.")
    except RuntimeError as error:
        sys.stderr.write(f"{error}\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
