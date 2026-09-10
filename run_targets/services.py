"""Service state and action decisions.

The deciding half of this file is pure: it speaks neither to Herdr nor to the
terminal, only to values. That is what makes the idempotence table verifiable
cell by cell.
"""

from __future__ import annotations

import os
import time
from collections.abc import Sequence
from dataclasses import dataclass

from .config import Target
from .settings import FOCUS_FIRST, FOCUS_LAST, Settings, tab_label
from .state import ServiceRecord, WorkspaceRecord

RUNNING = "running"
STOPPED = "stopped"
EXITED = "exited"
IDLE = "idle"
GONE = "gone"

OP_START = "start"
OP_CREATE = "create"
OP_STOP = "stop"
OP_RESTART = "restart"
OP_CLOSE = "close"
OP_FORGET = "forget"
OP_SKIP = "skip"

# Wait for a stop before restarting in the same pane. A dev server that handles
# SIGINT takes far longer to hand back control than the keystroke takes to
# arrive; typing the command in the meantime would have it swallowed by the
# dying process's standard input, and the service would stay stopped.
STOP_POLL_SECONDS = 0.1
STOP_POLL_ATTEMPTS = 30

# Indirection so the tests never actually wait.
_sleep = time.sleep


def derive_state(
    record: ServiceRecord | None, pane_alive: bool, foreground: bool
) -> str:
    """A service's state, crossing observation with what the plugin asked for.

    `process-info` only says which process holds the foreground, never why it
    left: a service that ends on its own leaves exactly the same trace as a
    ctrl+C. Only `stop_requested`, which the plugin sets itself, separates
    `stopped` from `exited`.
    """
    if record is None:
        return IDLE
    if not pane_alive:
        return GONE
    if foreground:
        return RUNNING
    return STOPPED if record.stop_requested else EXITED


_PLAN: dict[str, dict[str, str]] = {
    "start": {
        RUNNING: OP_SKIP,
        STOPPED: OP_START,
        EXITED: OP_START,
        IDLE: OP_CREATE,
        GONE: OP_CREATE,
    },
    "stop": {
        RUNNING: OP_STOP,
        STOPPED: OP_SKIP,
        EXITED: OP_SKIP,
        IDLE: OP_SKIP,
        GONE: OP_SKIP,
    },
    # Restarting a stopped service starts it: the state a restart aims for is
    # "running", and skipping it would work against that intent.
    "restart": {
        RUNNING: OP_RESTART,
        STOPPED: OP_START,
        EXITED: OP_START,
        IDLE: OP_CREATE,
        GONE: OP_CREATE,
    },
    "close": {
        RUNNING: OP_CLOSE,
        STOPPED: OP_CLOSE,
        EXITED: OP_CLOSE,
        IDLE: OP_SKIP,
        GONE: OP_FORGET,
    },
}


def plan_action(action: str, state: str) -> str:
    """The operation to carry out for a requested action on a given state."""
    return _PLAN.get(action, {}).get(state, OP_SKIP)


def resolve_selection(
    names: Sequence[str], checked: set[str], cursor: str | None
) -> list[str]:
    """The targets being acted on: the checked ones, else the one under the cursor.

    The convention of TUI file managers: you only check to act in bulk,
    otherwise you aim with the cursor.
    """
    selected = [name for name in names if name in checked]
    if selected:
        return selected
    if cursor is not None and cursor in names:
        return [cursor]
    return []


def focus_target(created: Sequence[str], mode: str) -> str | None:
    """The tab to focus once a batch has launched, per the user's setting.

    Returns None for the default: the dashboard is what shows the states, so
    staying on it is worth more than watching one service's output.
    """
    if not created:
        return None
    if mode == FOCUS_FIRST:
        return created[0]
    if mode == FOCUS_LAST:
        return created[-1]
    return None


def restart_blocked_message(name: str) -> str:
    """The message for a restart abandoned for want of a stop.

    Saying nothing would suggest a successful restart, while the service is
    still running its old code.
    """
    return f"{name}: still running after stop, restart skipped"


def skip_message(name: str, action: str, state: str) -> str:
    """The message for an action with no effect.

    An action skipped silently is indistinguishable from a keystroke that never
    registered.
    """
    return f"{name}: already {state}, {action} skipped"


@dataclass
class ServiceView:
    """A target and what we observe of it right now."""

    target: Target
    state: str
    tab_id: str | None
    pane_id: str | None


def observe(
    record: WorkspaceRecord, targets: Sequence[Target], client
) -> list[ServiceView]:
    """Recompute each target's state from Herdr.

    State is never read from the journal alone: the journal says which tabs and
    panes belong to the plugin, observation says what happens in them.
    """
    live = client.live_pane_ids()
    views: list[ServiceView] = []
    for target in targets:
        service = record.services.get(target.name)
        pane_alive = service is not None and service.pane_id in live
        foreground = False
        if pane_alive:
            foreground = client.has_foreground_command(client.process_info(service.pane_id))
        views.append(
            ServiceView(
                target=target,
                state=derive_state(service, pane_alive, foreground),
                tab_id=service.tab_id if service is not None else None,
                pane_id=service.pane_id if service is not None else None,
            )
        )
    return views


def _start_in_pane(
    record: WorkspaceRecord, view: ServiceView, tab_id: str, pane_id: str, client
) -> None:
    # The tab is recorded before the launch: if `pane_run` fails, the service
    # stays associated with its tab rather than leaving a live tab no journal
    # entry claims -- a later "start" would reuse it, where an orphaned tab
    # would open one more on every attempt.
    record.services[view.target.name] = ServiceRecord(
        tab_id=tab_id, pane_id=pane_id, stop_requested=False
    )
    client.pane_run(pane_id, view.target.command)


def _create_and_start(
    record: WorkspaceRecord,
    view: ServiceView,
    repo_root: str,
    workspace_id: str,
    settings: Settings,
    client,
) -> str:
    """Open a tab for the service and start it. Returns the new tab's id."""
    cwd = os.path.join(repo_root, view.target.cwd) if view.target.cwd else repo_root
    tab_id, pane_id = client.tab_create(
        workspace_id,
        tab_label(view.target.name, settings),
        cwd=cwd,
        env=view.target.env or None,
    )
    _start_in_pane(record, view, tab_id, pane_id, client)
    return tab_id


def _wait_until_stopped(pane_id: str, client) -> bool:
    """Poll the pane's foreground until it frees up.

    Returns true as soon as nothing occupies the pane, false if the waiting
    budget runs out. The budget is bounded: a service that refuses to die must
    not freeze the dashboard.
    """
    for _ in range(STOP_POLL_ATTEMPTS):
        if not client.has_foreground_command(client.process_info(pane_id)):
            return True
        _sleep(STOP_POLL_SECONDS)
    return not client.has_foreground_command(client.process_info(pane_id))


def _forget(record: WorkspaceRecord, name: str) -> None:
    """Drop a service from the journal."""
    record.services.pop(name, None)


def apply_action(
    action: str,
    views: Sequence[ServiceView],
    record: WorkspaceRecord,
    repo_root: str,
    workspace_id: str,
    settings: Settings,
    client,
) -> list[str]:
    """Apply an action to a selection, and return the messages to display.

    One failing target does not stop the ones after it: a half-started batch
    beats a batch abandoned on the first error.
    """
    messages: list[str] = []
    created: list[str] = []
    for view in views:
        operation = plan_action(action, view.state)
        name = view.target.name
        try:
            if operation == OP_SKIP:
                messages.append(skip_message(name, action, view.state))
            elif operation == OP_CREATE:
                created.append(
                    _create_and_start(
                        record, view, repo_root, workspace_id, settings, client
                    )
                )
            elif operation == OP_START:
                _start_in_pane(record, view, view.tab_id, view.pane_id, client)
            elif operation == OP_STOP:
                client.pane_send_keys(view.pane_id, "ctrl+c")
                service = record.services.get(name)
                if service is not None:
                    service.stop_requested = True
            elif operation == OP_RESTART:
                client.pane_send_keys(view.pane_id, "ctrl+c")
                if _wait_until_stopped(view.pane_id, client):
                    _start_in_pane(record, view, view.tab_id, view.pane_id, client)
                else:
                    messages.append(restart_blocked_message(name))
            elif operation == OP_CLOSE:
                client.tab_close(view.tab_id)
                _forget(record, name)
            elif operation == OP_FORGET:
                _forget(record, name)
        except RuntimeError as error:
            messages.append(f"{name}: {error}")

    # Focus comes last, once every tab exists: focusing between two creations
    # would leave the batch's tail opening behind a tab the user is now reading.
    # A failed focus is worth saying and nothing more -- the services are up.
    tab_id = focus_target(created, settings.focus_mode)
    if tab_id is not None:
        try:
            client.tab_focus(tab_id)
        except RuntimeError as error:
            messages.append(f"could not focus the tab: {error}")
    return messages
