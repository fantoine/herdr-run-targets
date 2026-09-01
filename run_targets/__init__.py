"""Herdr run-targets plugin."""

# Protocol between the two entry points: `toggle` sets this variable on the tab
# it creates, `dashboard` only renames itself when it is present. Defined here
# so a single string exists on both sides of the boundary.
TAB_OWNED_ENV = "RUN_TARGETS_TAB_OWNED"
TAB_LABEL = "run"
