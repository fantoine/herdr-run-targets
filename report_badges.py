#!/usr/bin/env python3
"""Event hook: refresh the focused workspace's service counts in the sidebar.

The dashboard only observes while it is open. Without this hook, a service
that crashes behind a closed dashboard would keep its "running" badge until
the next opening; with it, the badge catches up on the next tab or workspace
switch.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from run_targets import herdr
from run_targets.badges import publish
from run_targets.state import WorkspaceRecord, load_state


def main() -> int:
    workspace_id = os.environ.get("HERDR_WORKSPACE_ID")
    if not workspace_id:
        return 0
    record = load_state().get(workspace_id, WorkspaceRecord())
    publish(workspace_id, record, herdr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
