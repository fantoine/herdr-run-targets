"""Service counts published to Herdr's Spaces sidebar.

Herdr renders custom workspace metadata wherever the user's
`[ui.sidebar.spaces]` rows name a `$token`. The plugin only reports values;
placement and colours stay in the user's own sidebar configuration.

Only two counts: what runs, and what ended on its own. A service the user
stopped is not news, and a service whose tab was closed no longer exists from
the sidebar's point of view.
"""

from __future__ import annotations

from .services import EXITED, RUNNING, derive_state
from .state import WorkspaceRecord

SOURCE = "fantoine.run-targets"
TOKEN_UP = "svc_up"
TOKEN_DOWN = "svc_down"
MARK_UP = "▶"
MARK_DOWN = "✕"


def count_badges(record: WorkspaceRecord, client) -> tuple[int, int]:
    """How many of the journal's services run, and how many ended on their own.

    Read from the journal, not the target files: an event hook knows the
    workspace but not its repository, and only the services the plugin
    launched have a pane to observe anyway.
    """
    live = client.live_pane_ids()
    up = down = 0
    for service in record.services.values():
        pane_alive = service.pane_id in live
        foreground = pane_alive and client.has_foreground_command(
            client.process_info(service.pane_id)
        )
        state = derive_state(service, pane_alive, foreground)
        if state == RUNNING:
            up += 1
        elif state == EXITED:
            down += 1
    return up, down


def badge_args(workspace_id: str, up: int, down: int) -> list[str]:
    """The `herdr` command reporting both counts on a workspace.

    A zero is cleared rather than reported: Herdr drops a token without a value
    and its separator, so an idle workspace keeps a quiet sidebar row.
    """
    args = ["workspace", "report-metadata", workspace_id, "--source", SOURCE]
    for token, mark, count in ((TOKEN_UP, MARK_UP, up), (TOKEN_DOWN, MARK_DOWN, down)):
        if count > 0:
            args += ["--token", f"{token}={mark}{count}"]
        else:
            args += ["--clear-token", token]
    return args


def publish(
    workspace_id: str,
    record: WorkspaceRecord,
    client,
    previous: tuple[int, int] | None = None,
) -> tuple[int, int] | None:
    """Report a workspace's counts. Returns what was published, or None.

    Best effort: a badge is decoration, so a Herdr failure -- a server
    restarting, or a Herdr too old to know `workspace report-metadata` -- must
    never reach the dashboard or fail a hook. None makes the next call retry.
    Unchanged counts are not re-sent, since the dashboard refreshes every second.
    """
    try:
        counts = count_badges(record, client)
        if counts == previous:
            return counts
        client.herdr_call(badge_args(workspace_id, *counts))
    except RuntimeError:
        return None
    return counts
