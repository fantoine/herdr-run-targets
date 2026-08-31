"""Client du CLI Herdr : transport, contrat JSON, et enveloppes de panes."""

from __future__ import annotations

import json
import os
import subprocess
from typing import Sequence


def herdr_bin() -> str:
    """Le binaire Herdr à rappeler ; Herdr l'injecte dans l'environnement plugin."""
    return os.environ.get("HERDR_BIN_PATH") or "herdr"


def describe_herdr_failure(
    args: Sequence[str], returncode: int, stdout: str, stderr: str
) -> str:
    """Compose le message d'un échec, en préférant les mots de Herdr.

    Herdr rend `{"error": {"code": ..., "message": ...}}` sur échec ; ce message
    est bien plus parlant qu'un code de sortie ou qu'une plainte de parsing.
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
    """Lance une commande Herdr et rend sa sortie standard telle quelle.

    Séparé de `herdr_result` parce que certaines commandes — `pane run` par
    exemple — ne rendent rien du tout en cas de succès : exiger du JSON ferait
    passer chaque réussite pour un échec.
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
    """Lance une commande Herdr et rend son objet `result`."""
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
    """Tous les panes de la session."""
    panes = herdr_result(["pane", "list"]).get("panes")
    return [p for p in panes if isinstance(p, dict)] if isinstance(panes, list) else []


def panes_in_tab(tab_id: str) -> dict[str, dict]:
    """Les panes d'un onglet, indexés par identifiant."""
    return {
        pane["pane_id"]: pane
        for pane in list_panes()
        if isinstance(pane.get("pane_id"), str) and pane.get("tab_id") == tab_id
    }


def process_info(pane_id: str) -> dict:
    """Ce qui tourne au premier plan d'un pane."""
    info = herdr_result(["pane", "process-info", "--pane", pane_id]).get("process_info")
    return info if isinstance(info, dict) else {}


def has_foreground_command(info: dict) -> bool:
    """Vrai quand un processus autre que le shell occupe le premier plan."""
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
    """La ligne de commande d'un split. Isolée pour être testable sans Herdr."""
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
    """Crée un pane et rend son identifiant.

    L'identifiant est lu dans la réponse, jamais déduit : splitter `w4:p5` a
    rendu `w4:p7` lors de la sonde, les identifiants ne se suivent pas.
    """
    result = herdr_result(split_args(pane_id, direction, ratio, cwd, env))
    pane = result.get("pane")
    new_id = pane.get("pane_id") if isinstance(pane, dict) else None
    if not isinstance(new_id, str) or not new_id:
        raise RuntimeError("herdr pane split returned no pane id")
    return new_id


def pane_run(pane_id: str, command: str) -> None:
    """Soumet une commande au shell du pane, texte et Entrée en une opération."""
    herdr_call(["pane", "run", pane_id, command])


def pane_send_keys(pane_id: str, *keys: str) -> None:
    herdr_call(["pane", "send-keys", pane_id, *keys])


def pane_close(pane_id: str) -> None:
    herdr_call(["pane", "close", pane_id])


def tab_close(tab_id: str) -> None:
    herdr_call(["tab", "close", tab_id])
