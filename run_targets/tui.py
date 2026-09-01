"""Curses dashboard: rendering, modes, keyboard loop."""

from __future__ import annotations

import curses
import os
import time

from . import herdr
from .config import ORIGIN_LOCAL, load_run_config
from .services import ServiceView, apply_action, observe, resolve_selection
from .state import TabRecord, load_state, save_state

MODE_VIEW = "view"
MODE_EDIT = "edit"

REFRESH_SECONDS = 1.0

# An action's feedback is a one-off event: past this delay it clears, which
# makes the configuration warnings -- permanent ones -- visible again. Without
# expiry, a single "skipped" would hide them forever.
MESSAGE_SECONDS = 6.0

NAME_WIDTH = 12
STATE_WIDTH = 8
LOCAL_MARKER = " *"
SMALL_SCREEN_TEXT = "Too small - q to close"


def format_row(view: ServiceView, checked: bool, cursor: bool, mode: str) -> str:
    """One row of the table, as plain text so it stays testable.

    The columns are sized so a complete row fits in the dashboard's minimum 30
    characters: beyond that, the origin marker was the first thing to disappear,
    and with it the promise that "a different command is never a mystery".
    """
    marker = ">" if cursor else " "
    box = ("[x] " if checked else "[ ] ") if mode == MODE_EDIT else ""
    origin = LOCAL_MARKER if view.target.origin == ORIGIN_LOCAL else ""
    name = view.target.name[:NAME_WIDTH]
    return f"{marker} {box}{name:<{NAME_WIDTH}}{view.state:<{STATE_WIDTH}}{origin}"


def header_text(repo_root: str) -> str:
    """The title, which names the repository being watched.

    Without that name, a dashboard opened on the wrong directory -- the plugin
    is itself a git repository -- announces "no targets" with just as much
    confidence as a genuinely empty repository.
    """
    return f"RUN TARGETS  {os.path.basename(os.path.normpath(repo_root))}"


def empty_text(repo_root: str) -> str:
    """The line for a repository with no target, naming the directory inspected."""
    name = os.path.basename(os.path.normpath(repo_root))
    return f"No targets in {name}. Add .herdr-run.toml or .herdr-run.local.toml"


def visible_lines(
    messages: list[str], warnings: list[str], capacity: int
) -> list[str]:
    """The lines to show at the foot of the table, and what does not fit there.

    An action covers a selection: showing only one line would hide most of a
    batch's "skipped" entries. When it all does not fit, the last line counts
    the rest rather than keeping quiet about it.
    """
    lines = list(messages or warnings)
    if not lines or capacity <= 0:
        return []
    if len(lines) <= capacity:
        return lines
    kept = lines[: capacity - 1]
    kept.append(f"(+{len(lines) - len(kept)} more)")
    return kept


def footer_text(mode: str) -> str:
    """The help bar, which changes with the mode.

    View mode offers no destructive key: it is a display first. There is no
    "focus" key either -- Herdr 0.8.2 exposes no way to focus an arbitrary pane
    by its id. That leaves the Herdr prefix, which we have not verified a curses
    TUI lets through.
    """
    return "  ".join(footer_segments(mode))


def footer_segments(mode: str) -> list[str]:
    """The footer's items, each unbreakable.

    Keeping them separate lets the bar wrap without ever cutting a key in two.
    """
    if mode == MODE_EDIT:
        return [
            "EDIT",
            "space select",
            "enter start",
            "s stop",
            "r restart",
            "x close",
            "esc cancel",
        ]
    return ["VIEW", "e edit", "q close"]


def footer_lines(mode: str, width: int) -> list[str]:
    """Wrap the footer over as many lines as the width demands.

    Truncating hid `s stop`, `r restart` and `x close` as soon as one service
    shared the tab: an invisible key does not exist for whoever needs it. An
    item wider than the pane takes its line alone rather than being cut -- it
    will overflow, but stays identifiable.
    """
    lines: list[str] = []
    current = ""
    for segment in footer_segments(mode):
        if not current:
            current = segment
        elif len(current) + 2 + len(segment) <= width:
            current = f"{current}  {segment}"
        else:
            lines.append(current)
            current = segment
    if current:
        lines.append(current)
    return lines


class Dashboard:
    """The screen's state: mode, cursor, checked boxes, latest message."""

    def __init__(self, tab_id: str, repo_root: str, warnings: list[str]) -> None:
        self.tab_id = tab_id
        self.repo_root = repo_root
        self.mode = MODE_VIEW
        self.cursor = 0
        self.checked: set[str] = set()
        self.warnings: list[str] = list(warnings)
        self.messages: list[str] = []
        self.messages_at: float = 0.0
        self.views: list[ServiceView] = []

    def names(self) -> list[str]:
        return [view.target.name for view in self.views]

    def cursor_name(self) -> str | None:
        names = self.names()
        return names[self.cursor] if 0 <= self.cursor < len(names) else None

    def tab(self) -> TabRecord:
        state = load_state()
        return state.get(self.tab_id, TabRecord())

    def refresh(self) -> None:
        targets, warnings = load_run_config(self.repo_root)
        # `refresh` never touches `messages`: a configuration warning is a
        # permanent state of the files, an action's feedback is a one-off event.
        # Conflating them would erase, on every refresh, the "skipped" the user
        # needs to see.
        self.warnings = warnings
        self.views = observe(self.tab(), targets, herdr, self.tab_id)
        if self.cursor >= len(self.views):
            self.cursor = max(0, len(self.views) - 1)

    def set_messages(self, messages: list[str]) -> None:
        """Set the action messages, and the instant they went on screen."""
        self.messages = list(messages)
        self.messages_at = time.monotonic()

    def expire_messages(self, now: float) -> None:
        """Clear action messages that have expired."""
        if self.messages and now - self.messages_at >= MESSAGE_SECONDS:
            self.messages = []

    def tick(self) -> None:
        """Refresh without letting a Herdr failure take the pane down with it.

        A call that fails -- a server restarting, a pane closed between two
        commands -- must not unwind the stack out of the TUI: the dashboard's
        promise is to be a pane you leave open. The previous views stay on
        screen, stale but readable.
        """
        try:
            self.refresh()
        except RuntimeError as error:
            self.set_messages([str(error)])

    def act(self, action: str) -> None:
        selected = resolve_selection(self.names(), self.checked, self.cursor_name())
        chosen = [view for view in self.views if view.target.name in selected]
        state = load_state()
        tab = state.setdefault(self.tab_id, TabRecord())
        self.set_messages(
            apply_action(action, chosen, tab, self.repo_root, herdr, self.tab_id)
        )
        save_state(state)
        self.checked.clear()
        self.mode = MODE_VIEW
        # Same guarded route as the periodic loop: the refresh that follows an
        # action is the most exposed -- the plugin has just chained several
        # Herdr calls -- and it must no more than any other surface up to
        # `curses.wrapper`. If that refresh fails, the error replaces the
        # action's feedback: what the user needs to read first is that the
        # display is no longer trustworthy.
        self.tick()


def run_dashboard(stdscr, dashboard: Dashboard) -> None:
    """Rendering and keyboard loop."""
    curses.curs_set(0)
    stdscr.nodelay(True)
    last_refresh = 0.0

    while True:
        now = time.monotonic()
        dashboard.expire_messages(now)
        if now - last_refresh >= REFRESH_SECONDS:
            dashboard.tick()
            last_refresh = now

        stdscr.erase()
        height, width = stdscr.getmaxyx()
        if height < 4 or width < 30:
            # The keyboard is read here too: a pane too narrow that ignores
            # every key can no longer be closed from the inside, and a handful
            # of divider drags is enough to shrink it that far.
            stdscr.addstr(0, 0, SMALL_SCREEN_TEXT[: max(0, width - 1)])
            stdscr.refresh()
            key = stdscr.getch()
            if key == ord("q"):
                return
            if key == -1:
                time.sleep(0.05)
            continue

        stdscr.addstr(0, 0, header_text(dashboard.repo_root)[: width - 1], curses.A_BOLD)
        # The footer can take several lines in edit mode; the service rows must
        # not encroach on it.
        footer_height = len(footer_lines(dashboard.mode, max(1, width - 1)))
        rows_capacity = max(0, height - 3 - footer_height)
        used = 0
        for index, view in enumerate(dashboard.views[:rows_capacity]):
            row = format_row(
                view,
                checked=view.target.name in dashboard.checked,
                cursor=index == dashboard.cursor,
                mode=dashboard.mode,
            )
            stdscr.addstr(index + 2, 0, row[: width - 1])
            used = index + 1

        if not dashboard.views and rows_capacity > 0:
            stdscr.addstr(2, 0, empty_text(dashboard.repo_root)[: width - 1])
            used = 1

        footer = footer_lines(dashboard.mode, max(1, width - 1))
        footer_top = height - len(footer)
        lines = visible_lines(
            dashboard.messages, dashboard.warnings, max(0, footer_top - 2 - used)
        )
        for offset, line in enumerate(lines):
            stdscr.addstr(footer_top - len(lines) + offset, 0, line[: width - 1])
        attribute = curses.A_REVERSE if dashboard.mode == MODE_EDIT else curses.A_DIM
        for offset, line in enumerate(footer):
            stdscr.addstr(footer_top + offset, 0, line[: width - 1], attribute)
        stdscr.refresh()

        key = stdscr.getch()
        if key == -1:
            time.sleep(0.05)
            continue

        if dashboard.mode == MODE_VIEW:
            if key in (ord("q"),):
                return
            if key in (ord("e"),):
                dashboard.mode = MODE_EDIT
            elif key in (curses.KEY_DOWN, ord("j")):
                dashboard.cursor = min(dashboard.cursor + 1, max(0, len(dashboard.views) - 1))
            elif key in (curses.KEY_UP, ord("k")):
                dashboard.cursor = max(dashboard.cursor - 1, 0)
        else:
            if key == 27:  # escape
                dashboard.checked.clear()
                dashboard.mode = MODE_VIEW
            elif key == ord(" "):
                name = dashboard.cursor_name()
                if name is not None:
                    dashboard.checked.symmetric_difference_update({name})
            elif key in (curses.KEY_DOWN, ord("j")):
                dashboard.cursor = min(dashboard.cursor + 1, max(0, len(dashboard.views) - 1))
            elif key in (curses.KEY_UP, ord("k")):
                dashboard.cursor = max(dashboard.cursor - 1, 0)
            elif key in (curses.KEY_ENTER, 10, 13):
                dashboard.act("start")
            elif key == ord("s"):
                dashboard.act("stop")
            elif key == ord("r"):
                dashboard.act("restart")
            elif key == ord("x"):
                dashboard.act("close")
