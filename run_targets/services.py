"""Service state and action decisions.

This whole file is pure: it speaks neither to Herdr nor to the terminal, only to
values. That is what makes the idempotence table verifiable cell by cell.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Sequence

from .config import Target
from .state import ServiceRecord, TabRecord

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


def next_split_target(
    tab: TabRecord, live_pane_ids: set[str]
) -> tuple[str, str] | None:
    """The pane to split to host a new service, and the direction.

    Only considers panes from the journal. A fresh tab already holds
    `herdr-sidebar`'s docked pane; splitting "the tab's last pane" cut it in two
    during the design probe.
    """
    last = tab.last_service_pane_id
    if last is not None and last in live_pane_ids:
        return last, "down"
    control = tab.control_pane_id
    if control is not None and control in live_pane_ids:
        return control, "right"
    return None


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
    pane_id: str | None


def observe(
    tab: TabRecord, targets: Sequence[Target], client, tab_id: str
) -> list[ServiceView]:
    """Recompute each target's state from Herdr.

    State is never read from the journal alone: the journal says which panes
    belong to the plugin, observation says what happens in them.
    """
    live = set(client.panes_in_tab(tab_id))
    views: list[ServiceView] = []
    for target in targets:
        record = tab.services.get(target.name)
        pane_alive = record is not None and record.pane_id in live
        foreground = False
        if pane_alive:
            foreground = client.has_foreground_command(client.process_info(record.pane_id))
        views.append(
            ServiceView(
                target=target,
                state=derive_state(record, pane_alive, foreground),
                pane_id=record.pane_id if record is not None else None,
            )
        )
    return views


def _start_in_pane(tab: TabRecord, view: ServiceView, pane_id: str, client) -> None:
    # The pane is recorded before the launch: if `pane_run` fails, the service
    # stays associated with its pane rather than leaving a live pane no journal
    # entry claims -- a later "start" would reuse it, where an orphaned pane
    # would split one more pane on every attempt.
    tab.services[view.target.name] = ServiceRecord(pane_id=pane_id, stop_requested=False)
    client.pane_run(pane_id, view.target.command)


def _create_and_start(
    tab: TabRecord, view: ServiceView, repo_root: str, client, tab_id: str
) -> None:
    live = set(client.panes_in_tab(tab_id))
    destination = next_split_target(tab, live)
    if destination is None:
        raise RuntimeError("no pane of ours to split from")
    pane_id, direction = destination
    cwd = os.path.join(repo_root, view.target.cwd) if view.target.cwd else repo_root
    ratio = 0.25 if direction == "right" else None
    new_pane = client.pane_split(
        pane_id, direction, ratio=ratio, cwd=cwd, env=view.target.env or None
    )
    tab.last_service_pane_id = new_pane
    # The pane carries its target's name: in a column of services, a generic
    # terminal title says nothing about what runs there. The rename comes before
    # the launch, otherwise the command would set a title of its own.
    # A failure here must not keep the service from starting.
    try:
        client.pane_rename(new_pane, view.target.name)
    except RuntimeError:
        pass
    _start_in_pane(tab, view, new_pane, client)


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


def _forget(tab: TabRecord, name: str) -> None:
    """Drop a service from the journal, keeping `last_service_pane_id` coherent."""
    record = tab.services.pop(name, None)
    if record is not None and tab.last_service_pane_id == record.pane_id:
        remaining = [service.pane_id for service in tab.services.values()]
        tab.last_service_pane_id = remaining[-1] if remaining else None


def apply_action(
    action: str,
    views: Sequence[ServiceView],
    tab: TabRecord,
    repo_root: str,
    client,
    tab_id: str,
) -> list[str]:
    """Apply an action to a selection, and return the messages to display.

    One failing target does not stop the ones after it: a half-started batch
    beats a batch abandoned on the first error.
    """
    messages: list[str] = []
    for view in views:
        operation = plan_action(action, view.state)
        name = view.target.name
        try:
            if operation == OP_SKIP:
                messages.append(skip_message(name, action, view.state))
            elif operation == OP_CREATE:
                _create_and_start(tab, view, repo_root, client, tab_id)
            elif operation == OP_START:
                _start_in_pane(tab, view, view.pane_id, client)
            elif operation == OP_STOP:
                client.pane_send_keys(view.pane_id, "ctrl+c")
                record = tab.services.get(name)
                if record is not None:
                    record.stop_requested = True
            elif operation == OP_RESTART:
                client.pane_send_keys(view.pane_id, "ctrl+c")
                if _wait_until_stopped(view.pane_id, client):
                    _start_in_pane(tab, view, view.pane_id, client)
                else:
                    messages.append(restart_blocked_message(name))
            elif operation == OP_CLOSE:
                client.pane_close(view.pane_id)
                _forget(tab, name)
            elif operation == OP_FORGET:
                _forget(tab, name)
        except RuntimeError as error:
            messages.append(f"{name}: {error}")
    return messages
