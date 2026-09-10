"""Herdr CLI client: transport, JSON contract, and pane wrappers."""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Sequence


def herdr_bin() -> str:
    """The Herdr binary to call back into; Herdr injects it in the plugin env."""
    return os.environ.get("HERDR_BIN_PATH") or "herdr"


def describe_herdr_failure(
    args: Sequence[str], returncode: int, stdout: str, stderr: str
) -> str:
    """Compose a failure message, preferring Herdr's own words.

    Herdr returns `{"error": {"code": ..., "message": ...}}` on failure; that
    message says far more than an exit code or a parsing complaint.
    """
    try:
        payload = json.loads(stdout)
    except (ValueError, TypeError):
        payload = None
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            message = error.get("message")
            if isinstance(message, str) and message:
                return f"herdr {' '.join(args)} failed: {message}"
    detail = stderr.strip()
    if detail:
        return f"herdr {' '.join(args)} failed: {detail}"
    return f"herdr {' '.join(args)} failed with exit code {returncode}"


def herdr_call(args: Sequence[str]) -> str:
    """Run a Herdr command and return its standard output as-is.

    Kept apart from `herdr_result` because some commands -- `pane run` among
    them -- return nothing at all on success: demanding JSON would turn every
    success into a failure.
    """
    try:
        completed = subprocess.run(
            # A failing herdr command is reported through its own message, read
            # from the payload below, so the exit code is ours to inspect.
            [herdr_bin(), *args], capture_output=True, check=False
        )
    except OSError as error:
        raise RuntimeError(f"herdr {' '.join(args)} could not be started: {error}")
    stdout = completed.stdout.decode("utf-8", "replace")
    if completed.returncode != 0:
        raise RuntimeError(
            describe_herdr_failure(
                args, completed.returncode, stdout, completed.stderr.decode("utf-8", "replace")
            )
        )
    return stdout


def herdr_result(args: Sequence[str]) -> dict:
    """Run a Herdr command and return its `result` object."""
    stdout = herdr_call(args)
    try:
        payload = json.loads(stdout)
    except ValueError as error:
        raise RuntimeError(f"herdr {' '.join(args)} returned invalid JSON: {error}")
    result = payload.get("result") if isinstance(payload, dict) else None
    if not isinstance(result, dict):
        # RuntimeError, not TypeError: every caller of this module catches
        # RuntimeError to turn a Herdr failure into a line in the dashboard,
        # and a payload without a result is exactly such a failure.
        raise RuntimeError(f"herdr {' '.join(args)} returned no result object")  # noqa: TRY004
    return result


def list_panes() -> list[dict]:
    """Every pane in the session."""
    panes = herdr_result(["pane", "list"]).get("panes")
    return [p for p in panes if isinstance(p, dict)] if isinstance(panes, list) else []


def live_pane_ids() -> set[str]:
    """Every live pane id in the session.

    Global, not per tab: each service now lives in a tab of its own, so the
    journal's pane ids are what identify them, not the tab they sit in.
    """
    return {
        pane["pane_id"] for pane in list_panes() if isinstance(pane.get("pane_id"), str)
    }


def process_info(pane_id: str) -> dict:
    """What runs in a pane's foreground."""
    info = herdr_result(["pane", "process-info", "--pane", pane_id]).get("process_info")
    return info if isinstance(info, dict) else {}


def has_foreground_command(info: dict) -> bool:
    """True when a process other than the shell holds the foreground."""
    shell_pid = info.get("shell_pid")
    processes = info.get("foreground_processes")
    if not isinstance(shell_pid, int) or not isinstance(processes, list):
        return False
    for process in processes:
        if not isinstance(process, dict):
            continue
        pid = process.get("pid")
        if isinstance(pid, int) and pid != shell_pid:
            return True
    return False


def tab_create_args(
    workspace_id: str,
    label: str,
    cwd: str | None,
    env: dict[str, str] | None,
) -> list[str]:
    """A tab creation's command line. Isolated so it is testable without Herdr."""
    args = ["tab", "create", "--workspace", workspace_id, "--label", label, "--no-focus"]
    if cwd is not None:
        args += ["--cwd", cwd]
    for key, value in (env or {}).items():
        args += ["--env", f"{key}={value}"]
    return args


def tab_create(
    workspace_id: str,
    label: str,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
) -> tuple[str, str]:
    """Create a tab and return its id along with its root pane's.

    Herdr answers with both, so neither is ever inferred -- the ids do not run in
    sequence. The label is set here rather than renamed afterwards: one call
    instead of two, and the tab never flashes a generic name.
    """
    result = herdr_result(tab_create_args(workspace_id, label, cwd, env))
    tab = result.get("tab")
    pane = result.get("root_pane")
    tab_id = tab.get("tab_id") if isinstance(tab, dict) else None
    pane_id = pane.get("pane_id") if isinstance(pane, dict) else None
    if not isinstance(tab_id, str) or not tab_id:
        raise RuntimeError("herdr tab create returned no tab id")
    if not isinstance(pane_id, str) or not pane_id:
        raise RuntimeError("herdr tab create returned no root pane id")
    return tab_id, pane_id


def tab_focus(tab_id: str) -> None:
    herdr_call(["tab", "focus", tab_id])


def pane_run(pane_id: str, command: str) -> None:
    """Submit a command to the pane's shell, text and Enter in one operation."""
    herdr_call(["pane", "run", pane_id, command])


def pane_send_keys(pane_id: str, *keys: str) -> None:
    herdr_call(["pane", "send-keys", pane_id, *keys])


def pane_close(pane_id: str) -> None:
    herdr_call(["pane", "close", pane_id])


def tab_close(tab_id: str) -> None:
    herdr_call(["tab", "close", tab_id])
