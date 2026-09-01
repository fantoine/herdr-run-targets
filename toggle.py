#!/usr/bin/env python3
"""The `toggle` action: open, reopen or close the dashboard."""

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
    """A still-live service pane of the tab, to serve as a split point."""
    for service in record.services.values():
        if service.pane_id in live_panes:
            return service.pane_id
    return None


def tab_workspace_id(record: TabRecord, live_panes: dict[str, dict]) -> str | None:
    """A tracked tab's workspace, inferred from its still-live panes.

    Inferring it rather than storing it avoids adding a field to the journal and
    keeps journals written by an earlier version readable.
    """
    candidates = [record.control_pane_id]
    candidates += [service.pane_id for service in record.services.values()]
    for pane_id in candidates:
        pane = live_panes.get(pane_id) if pane_id else None
        if isinstance(pane, dict) and isinstance(pane.get("workspace_id"), str):
            return pane["workspace_id"]
    return None


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
    state: dict[str, TabRecord],
    live_panes: dict[str, dict],
    workspace_id: str | None = None,
) -> tuple[str, str | None]:
    """What `toggle` should do, given the journal and the live panes.

    The order matters: close if the dashboard is there, else reopen it in the
    tab that still hosts services, and only then create. Without the middle
    case, closing the dashboard would leave an orphaned tab no shortcut could
    find again.

    Closing a tab with no live service at all closes the whole tab, not just the
    pane: otherwise a tab would remain that neither the "reopen" case nor the
    "close" case would recognise, and every later toggle would create one more.
    What the toggle created, it takes away.
    """

    def ours(record: TabRecord) -> bool:
        # The journal is global: without this filter, the toggle acts on the
        # first tracked tab it finds, including one in another worktree -- it has
        # already closed the dashboard of a workspace the user was working in.
        # Unknown workspace: fall back to the old behaviour rather than doing
        # nothing at all.
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
    """The workspace's working directory, read from the action's context.

    Herdr injects `HERDR_PLUGIN_CONTEXT_JSON`; it is the only source of the
    project's directory when no service pane exists yet to inherit it from. A
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
            # The plugin's structural guard holds for tabs too: we only close
            # what the journal claims as its own.
            if argument not in state:
                sys.stderr.write(f"{argument} is not a run-targets tab.\n")
                return 1
            herdr.tab_close(argument)
            del state[argument]
            save_state(state)
            print(f"Closed the run-targets tab {argument}.")
        elif decision == "reopen":
            # The dashboard settles back into the existing tab: it opens as a
            # split of a still-live service pane, the explicit target
            # `--placement split` requires to know where to divide.
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
                # Herdr runs plugin commands in the plugin's directory, not the
                # repository's: without `--cwd`, the reopened dashboard would
                # resolve its own root instead of the one watched by the service
                # pane it is joining.
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
            # Without this the tab is born in the focused workspace, which is
            # not necessarily the one it was invoked from -- inconsistent with
            # the lookup, now restricted to the current workspace.
            # The id is only passed if the workspace is still alive: closing a
            # workspace's last tab closes the workspace itself, so the current
            # id may name something gone. Herdr would answer
            # `workspace_not_found` and the toggle would fail instead of falling
            # back to the focused workspace.
            live_workspaces = {
                pane.get("workspace_id")
                for pane in live_panes.values()
                if isinstance(pane, dict)
            }
            if workspace_id is not None and workspace_id in live_workspaces:
                open_args += ["--workspace", workspace_id]
            # No service pane to inherit a directory from: it is the action's
            # context, injected by Herdr, that carries the user's workspace
            # directory.
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
