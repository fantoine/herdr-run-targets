#!/usr/bin/env python3
"""The `toggle` action: open or close the dashboard of the current workspace."""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from run_targets import herdr
from run_targets.settings import PLACEMENT_POPUP, Settings, load_settings
from run_targets.state import WorkspaceRecord, load_state

PLUGIN_ID = "fantoine.run-targets"
ENTRYPOINT = "dashboard"


def current_workspace_id() -> str | None:
    """The workspace the action is invoked from.

    Herdr injects `HERDR_WORKSPACE_ID` into every plugin command; the action's
    context carries the same information and serves as the fallback.
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
    state: dict[str, WorkspaceRecord],
    live_panes: dict[str, dict],
    workspace_id: str | None,
) -> tuple[str, str | None]:
    """What `toggle` should do, given the journal and the live panes.

    Two branches, because the dashboard is an overlay: it owns no tab, so
    closing it never leaves an orphaned one behind, and reopening it never has
    to find a tab again.

    Scoped to the invoking workspace. The journal is global, and without that
    scope the toggle acted on the first tracked entry it found -- it has already
    closed the dashboard of a workspace the user was working in.
    """
    record = state.get(workspace_id) if workspace_id else None
    if record is not None:
        pane_id = record.control_pane_id
        if pane_id and pane_id in live_panes:
            return "close", pane_id
    return "create", None


def workspace_cwd_from_context() -> str | None:
    """The workspace's working directory, read from the action's context.

    Herdr injects `HERDR_PLUGIN_CONTEXT_JSON`; it is the only source of the
    project's directory, since an overlay inherits nothing from a service tab. A
    missing variable, invalid JSON, a non-object payload, or a `workspace_cwd`
    that is absent or not a string all degrade to None rather than failing the
    action. That degradation is not harmless: without `--cwd`, the pane inherits
    the plugin's own directory -- itself a git repository -- and the dashboard
    announces "no targets" for the wrong repository. That is why the header
    names the repository it resolved: a visible mistake beats a silent wrong
    answer.
    """
    try:
        payload = json.loads(os.environ.get("HERDR_PLUGIN_CONTEXT_JSON") or "{}")
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    cwd = payload.get("workspace_cwd")
    return cwd if isinstance(cwd, str) and cwd else None


def open_args(settings: Settings, workspace_id: str | None) -> list[str]:
    """The command that opens the floating dashboard.

    Neither `--workspace` nor `--target-pane`, whichever the placement: Herdr
    answers `invalid_params: "overlay and popup plugin panes target the active
    pane"` if either is passed. Both land on the active pane, which is the
    workspace the key was pressed from.

    An overlay covers its host pane; only a popup can be sized, and Herdr
    refuses `--width`/`--height` on anything else. A popup also belongs to no
    pane, so it receives none of `HERDR_WORKSPACE_ID`, `HERDR_TAB_ID` or
    `HERDR_PANE_ID` -- the workspace has to be handed over explicitly, or the
    dashboard would not know whose tabs it manages.
    """
    args = [
        "plugin", "pane", "open",
        "--plugin", PLUGIN_ID,
        "--entrypoint", ENTRYPOINT,
        "--placement", settings.placement,
    ]
    if settings.placement == PLACEMENT_POPUP:
        if settings.popup_width is not None:
            args += ["--width", settings.popup_width]
        if settings.popup_height is not None:
            args += ["--height", settings.popup_height]
        if workspace_id is not None:
            args += ["--env", f"HERDR_WORKSPACE_ID={workspace_id}"]
    workspace_cwd = workspace_cwd_from_context()
    if workspace_cwd is not None:
        args += ["--cwd", workspace_cwd]
    return args


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

    workspace_id = current_workspace_id()
    decision, argument = decide_toggle(load_state(), live_panes, workspace_id)
    settings, warnings = load_settings()
    for warning in warnings:
        sys.stderr.write(warning + "\n")

    try:
        if decision == "close":
            herdr.pane_close(argument)
            print(f"Closed the dashboard pane {argument}.")
        else:
            herdr.herdr_result(open_args(settings, workspace_id))
            print("Opened the run-targets dashboard.")
    except RuntimeError as error:
        sys.stderr.write(f"{error}\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
