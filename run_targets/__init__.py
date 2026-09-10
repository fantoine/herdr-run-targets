"""Herdr run-targets plugin.

The version guard lives here so it runs before any submodule imports `tomllib`,
which only exists from 3.11 on. The manifest invokes a bare `python3`; nothing
guarantees it is a 3.11, and a `ModuleNotFoundError` traceback would be an
unreadable way to say so.
"""

import sys

if sys.version_info < (3, 11):
    sys.stderr.write("run-targets requires Python 3.11 or newer.\n")
    raise SystemExit(1)

# Protocol between the two entry points: `toggle` sets this variable on the tab
# it creates, `dashboard` only renames itself when it is present.
TAB_OWNED_ENV = "RUN_TARGETS_TAB_OWNED"
TAB_LABEL = "run"
