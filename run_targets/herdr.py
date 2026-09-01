"""Herdr CLI client: transport, JSON contract, and pane wrappers."""

from __future__ import annotations

import json
import os
import subprocess
from typing import Sequence


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
            [herdr_bin(), *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE
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
        raise RuntimeError(f"herdr {' '.join(args)} returned no result object")
    return result


def list_panes() -> list[dict]:
    """Every pane in the session."""
    panes = herdr_result(["pane", "list"]).get("panes")
    return [p for p in panes if isinstance(p, dict)] if isinstance(panes, list) else []


def panes_in_tab(tab_id: str) -> dict[str, dict]:
    """A tab's panes, keyed by pane id."""
    return {
        pane["pane_id"]: pane
        for pane in list_panes()
        if isinstance(pane.get("pane_id"), str) and pane.get("tab_id") == tab_id
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


def split_args(
    pane_id: str,
    direction: str,
    ratio: float | None,
    cwd: str | None,
    env: dict[str, str] | None,
) -> list[str]:
    """A split's command line. Isolated so it is testable without Herdr."""
    args = ["pane", "split", pane_id, "--direction", direction, "--no-focus"]
    if ratio is not None:
        args += ["--ratio", str(ratio)]
    if cwd is not None:
        args += ["--cwd", cwd]
    for key, value in (env or {}).items():
        args += ["--env", f"{key}={value}"]
    return args


def pane_split(
    pane_id: str,
    direction: str,
    ratio: float | None = None,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
) -> str:
    """Create a pane and return its id.

    The id is read from the response, never inferred: splitting `w4:p5` returned
    `w4:p7` during the probe, so ids do not run in sequence.
    """
    result = herdr_result(split_args(pane_id, direction, ratio, cwd, env))
    pane = result.get("pane")
    new_id = pane.get("pane_id") if isinstance(pane, dict) else None
    if not isinstance(new_id, str) or not new_id:
        raise RuntimeError("herdr pane split returned no pane id")
    return new_id


def pane_run(pane_id: str, command: str) -> None:
    """Submit a command to the pane's shell, text and Enter in one operation."""
    herdr_call(["pane", "run", pane_id, command])


def pane_send_keys(pane_id: str, *keys: str) -> None:
    herdr_call(["pane", "send-keys", pane_id, *keys])


def pane_rename(pane_id: str, label: str) -> None:
    """Name a pane. Used to carry a target's name onto its service pane."""
    herdr_call(["pane", "rename", pane_id, label])


def tab_rename(tab_id: str, label: str) -> None:
    """Name a tab.

    `plugin pane open` has no label flag -- the manifest's `title` names the
    pane, not the tab -- so renaming happens afterwards.
    """
    herdr_call(["tab", "rename", tab_id, label])


def pane_close(pane_id: str) -> None:
    herdr_call(["pane", "close", pane_id])


def tab_close(tab_id: str) -> None:
    herdr_call(["tab", "close", tab_id])
