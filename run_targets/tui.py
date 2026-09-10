"""Curses dashboard: rendering, modes, keyboard loop."""

from __future__ import annotations

import curses
import os
import time
from collections.abc import Sequence

from . import herdr
from .config import ORIGIN_LOCAL, load_run_config
from .services import ServiceView, apply_action, observe, resolve_selection
from .settings import Settings, load_settings
from .state import WorkspaceRecord, load_state, save_state

# Simple mode acts on the row under the cursor; multi-select mode acts on the
# checked rows. The keys are the same in both, so a service can be handled on
# its own without a detour through a second mode.
MODE_SIMPLE = "simple"
MODE_MULTI = "multiselect"

REFRESH_SECONDS = 1.0

# An action's feedback is a one-off event: past this delay it clears, which
# makes the configuration warnings -- permanent ones -- visible again. Without
# expiry, a single "skipped" would hide them forever.
MESSAGE_SECONDS = 6.0

NAME_WIDTH_MIN = 12
NAME_WIDTH_MAX = 40
STATE_WIDTH = 8
LOCAL_MARKER = " *"
SMALL_SCREEN_TEXT = "Too small - q to close"
CHECKBOX_WIDTH = 4
MARKER_WIDTH = 2


def name_column(names: Sequence[str], mode: str, width: int | None = None) -> int:
    """How wide the name column should be for these targets.

    Aligned on the longest name rather than fixed: a floating dashboard has room
    a docked column did not. Capped at `NAME_WIDTH_MAX` so one very long name
    cannot push the state off screen, and never narrower than
    `NAME_WIDTH_MIN` -- a pane too narrow for that has the row sliced, which is
    what the too-small notice is for.

    The extra character keeps a space between the longest name and the state.
    """
    longest = max((len(name) for name in names), default=0)
    column = min(NAME_WIDTH_MAX, max(NAME_WIDTH_MIN, longest + 1))
    if width is not None:
        overhead = MARKER_WIDTH + (CHECKBOX_WIDTH if mode == MODE_MULTI else 0)
        room = width - overhead - STATE_WIDTH - len(LOCAL_MARKER)
        column = max(NAME_WIDTH_MIN, min(column, room))
    return column


def format_row(
    view: ServiceView,
    checked: bool,
    cursor: bool,
    mode: str,
    name_width: int = NAME_WIDTH_MIN,
) -> str:
    """One row of the table, as plain text so it stays testable."""
    marker = ">" if cursor else " "
    box = ("[x] " if checked else "[ ] ") if mode == MODE_MULTI else ""
    origin = LOCAL_MARKER if view.target.origin == ORIGIN_LOCAL else ""
    # One character short of the column, so a name that fills it still keeps a
    # space before the state: real target names ran to `community-sdk-playground`
    # and printed `community-sdidle`.
    name = view.target.name[: name_width - 1]
    return f"{marker} {box}{name:<{name_width}}{view.state:<{STATE_WIDTH}}{origin}"


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


# Three spaces between blocks, one inside a block: the gap that separates two
# shortcuts has to read as wider than the gap between a key and what it does,
# or the bar becomes a word soup.
BLOCK_GAP = "   "

# Item kinds, so the renderer knows what to paint each part as.
CHIP = "chip"
KEY = "key"


def footer_items(mode: str, has_local: bool = False) -> list[tuple[str, str, str]]:
    """The footer's blocks, as (kind, key, description).

    Structured rather than pre-joined so the renderer can paint the mode chip,
    the keys and their descriptions differently -- a flat string gave every
    part the same weight, and telling which key went with which action meant
    counting spaces.
    """
    if mode == MODE_MULTI:
        items = [
            (CHIP, "MULTI", ""),
            (KEY, "space", "select"),
            (KEY, "enter", "start"),
            (KEY, "s", "stop"),
            (KEY, "r", "restart"),
            (KEY, "x", "close"),
            (KEY, "esc", "cancel"),
        ]
    else:
        items = [
            (CHIP, "SIMPLE", ""),
            (KEY, "enter", "start"),
            (KEY, "s", "stop"),
            (KEY, "r", "restart"),
            (KEY, "x", "close"),
            (KEY, "space", "multi"),
            (KEY, "esc", "close"),
        ]
    if has_local:
        # The only column marker the table explains, because it is the only one
        # that needs it: a name and a state read themselves, a bare asterisk
        # does not. In the footer rather than a header row, which would cost a
        # line of a popup 40% of the window tall.
        items.append((KEY, "*", "local"))
    return items


def item_text(item: tuple[str, str, str]) -> str:
    """One block as plain text: `esc cancel`, or the bare chip."""
    _, key, description = item
    return f"{key} {description}" if description else key


def footer_rows(
    items: Sequence[tuple[str, str, str]], width: int
) -> list[list[tuple[str, str, str]]]:
    """Wrap the blocks over as many lines as the width demands.

    Truncating hid `s stop`, `r restart` and `x close` as soon as the pane was
    narrow: an invisible key does not exist for whoever needs it. A block wider
    than the pane takes its line alone rather than being cut.
    """
    rows: list[list[tuple[str, str, str]]] = []
    current: list[tuple[str, str, str]] = []
    used = 0
    for item in items:
        length = len(item_text(item))
        if not current:
            current, used = [item], length
        elif used + len(BLOCK_GAP) + length <= width:
            current.append(item)
            used += len(BLOCK_GAP) + length
        else:
            rows.append(current)
            current, used = [item], length
    if current:
        rows.append(current)
    return rows


def footer_text(mode: str, has_local: bool = False) -> str:
    """The help bar as one line of plain text, for tests and narrow renders."""
    return BLOCK_GAP.join(item_text(item) for item in footer_items(mode, has_local))


def footer_lines(mode: str, width: int, has_local: bool = False) -> list[str]:
    """The wrapped help bar, as plain text lines."""
    return [
        BLOCK_GAP.join(item_text(item) for item in row)
        for row in footer_rows(footer_items(mode, has_local), width)
    ]


class Dashboard:
    """The screen's state: mode, cursor, checked boxes, latest message."""

    def __init__(self, workspace_id: str, repo_root: str, warnings: list[str]) -> None:
        self.workspace_id = workspace_id
        self.repo_root = repo_root
        self.settings = Settings()
        self.mode = MODE_SIMPLE
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

    def record(self) -> WorkspaceRecord:
        state = load_state()
        return state.get(self.workspace_id, WorkspaceRecord())

    def refresh(self) -> None:
        targets, warnings = load_run_config(self.repo_root)
        # Settings are re-read on every tick, like the target files: a prefix or
        # focus mode edited in another pane takes effect on the next launch,
        # with no dashboard restart.
        self.settings, settings_warnings = load_settings()
        # `refresh` never touches `messages`: a configuration warning is a
        # permanent state of the files, an action's feedback is a one-off event.
        # Conflating them would erase, on every refresh, the "skipped" the user
        # needs to see.
        self.warnings = settings_warnings + warnings
        self.views = observe(self.record(), targets, herdr)
        if self.cursor >= len(self.views):
            self.cursor = max(0, len(self.views) - 1)

    def toggle_check(self) -> None:
        """Check or uncheck the row under the cursor, and follow the mode.

        Checking is what opens multi-select mode, and unchecking the last row
        leaves it: the mode is a consequence of the selection, never a state to
        maintain by hand.
        """
        name = self.cursor_name()
        if name is None:
            return
        self.checked.symmetric_difference_update({name})
        self.mode = MODE_MULTI if self.checked else MODE_SIMPLE

    def clear_selection(self) -> None:
        """Drop the whole selection and fall back to the single-target mode."""
        self.checked.clear()
        self.mode = MODE_SIMPLE

    def escape(self) -> bool:
        """Back out one level. Returns whether the dashboard should close.

        `esc` cancels a selection first and closes the dashboard only when
        there is nothing left to cancel -- the same escalation as any nested
        view. It used to close nothing at all in simple mode, because in the
        first design it was the only way out of edit mode.
        """
        if self.mode == MODE_MULTI:
            self.clear_selection()
            return False
        return True

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
        record = state.setdefault(self.workspace_id, WorkspaceRecord())
        self.set_messages(
            apply_action(
                action,
                chosen,
                record,
                self.repo_root,
                self.workspace_id,
                self.settings,
                herdr,
            )
        )
        save_state(state)
        self.checked.clear()
        # A batch is done once applied, so multi-select mode hands the keyboard
        # back to the single-target mode rather than keeping stale checkboxes.
        self.mode = MODE_SIMPLE
        # Same guarded route as the periodic loop: the refresh that follows an
        # action is the most exposed -- the plugin has just chained several
        # Herdr calls -- and it must no more than any other surface up to
        # `curses.wrapper`. If that refresh fails, the error replaces the
        # action's feedback: what the user needs to read first is that the
        # display is no longer trustworthy.
        self.tick()


PAIR_KEY = 1
PAIR_CHIP = 2


def use_terminal_colors() -> bool:
    """Let the pane keep the terminal's own background, and set up the footer.

    `curses.wrapper` starts colour but not `use_default_colors`, so pair 0
    resolves to ncurses' own black-on-white instead of staying transparent --
    the pane then paints a grey block over a themed background, which reads as
    a foreign pane. A terminal without colour support raises here, and having
    no colours is not a reason to refuse to draw: it only means the footer falls
    back to bold and dim.

    Returns whether colour is available.
    """
    try:
        curses.start_color()
        curses.use_default_colors()
        # -1 is the terminal's own background, kept by use_default_colors.
        curses.init_pair(PAIR_KEY, curses.COLOR_CYAN, -1)
        curses.init_pair(PAIR_CHIP, curses.COLOR_CYAN, -1)
    except (curses.error, ValueError):
        # A terminal with no colour pairs to hand out raises ValueError here
        # rather than curses.error, and a monochrome footer is still a footer.
        return False
    return True


def key_attributes(colored: bool) -> tuple[int, int, int]:
    """Attributes for (chip, key, description).

    The key has to be the loudest part of a block and its description the
    quietest, otherwise the eye cannot pair them: everything at one weight is
    what made the bar unreadable.
    """
    if colored:
        chip = curses.color_pair(PAIR_CHIP) | curses.A_REVERSE | curses.A_BOLD
        key = curses.color_pair(PAIR_KEY) | curses.A_BOLD
    else:
        chip = curses.A_REVERSE | curses.A_BOLD
        key = curses.A_BOLD
    return chip, key, curses.A_DIM


def draw_footer_row(
    stdscr, row_index: int, items: Sequence[tuple[str, str, str]], width: int, colored: bool
) -> None:
    """Paint one footer line block by block, clipping at the pane's edge."""
    chip_attribute, key_attribute, text_attribute = key_attributes(colored)
    column = 0
    for position, (kind, key, description) in enumerate(items):
        if position:
            column += len(BLOCK_GAP)
        if kind == CHIP:
            label = f" {key} "
            if column + len(label) >= width:
                return
            stdscr.addstr(row_index, column, label, chip_attribute)
            column += len(label)
            continue
        if column + len(key) >= width:
            return
        stdscr.addstr(row_index, column, key, key_attribute)
        column += len(key)
        if description:
            remaining = width - column - 2
            if remaining <= 0:
                return
            stdscr.addstr(row_index, column, f" {description}"[:remaining], text_attribute)
            column += 1 + len(description)


def run_dashboard(stdscr, dashboard: Dashboard) -> None:
    """Rendering and keyboard loop."""
    colored = use_terminal_colors()
    # ncurses waits a full second on a bare escape, in case it opens a longer
    # sequence. `esc` is how multi-select mode is cancelled, and a mode that
    # takes a second to leave feels broken.
    try:
        curses.set_escdelay(25)
    except (AttributeError, curses.error):
        pass
    curses.curs_set(0)
    stdscr.nodelay(True)
    last_refresh = 0.0
    last_size = stdscr.getmaxyx()

    while True:
        now = time.monotonic()
        # Herdr can hand the pane its final geometry after the process has
        # started, and curses keeps the size it was born with until a resize is
        # taken in. A layout drawn against the stale size puts the footer where
        # nothing is displayed -- the help line stayed invisible until the pane
        # was clicked, which is what finally delivered the resize.
        size = stdscr.getmaxyx()
        if size != last_size:
            curses.update_lines_cols()
            stdscr.clear()
            last_size = size
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
            if key == curses.KEY_RESIZE:
                curses.update_lines_cols()
                stdscr.clear()
                last_size = stdscr.getmaxyx()
            elif key == -1:
                time.sleep(0.05)
            continue

        stdscr.addstr(0, 0, header_text(dashboard.repo_root)[: width - 1], curses.A_BOLD)
        # The footer can take several lines in edit mode; the service rows must
        # not encroach on it.
        has_local = any(
            view.target.origin == ORIGIN_LOCAL for view in dashboard.views
        )
        items = footer_items(dashboard.mode, has_local)
        footer = footer_rows(items, max(1, width - 1))
        rows_capacity = max(0, height - 3 - len(footer))
        column = name_column(
            [view.target.name for view in dashboard.views], dashboard.mode, width - 1
        )
        used = 0
        for index, view in enumerate(dashboard.views[:rows_capacity]):
            row = format_row(
                view,
                checked=view.target.name in dashboard.checked,
                cursor=index == dashboard.cursor,
                mode=dashboard.mode,
                name_width=column,
            )
            stdscr.addstr(index + 2, 0, row[: width - 1])
            used = index + 1

        if not dashboard.views and rows_capacity > 0:
            stdscr.addstr(2, 0, empty_text(dashboard.repo_root)[: width - 1])
            used = 1

        footer_top = height - len(footer)
        lines = visible_lines(
            dashboard.messages, dashboard.warnings, max(0, footer_top - 2 - used)
        )
        for offset, line in enumerate(lines):
            stdscr.addstr(footer_top - len(lines) + offset, 0, line[: width - 1])
        for offset, row in enumerate(footer):
            draw_footer_row(stdscr, footer_top + offset, row, width, colored)
        stdscr.refresh()

        key = stdscr.getch()
        if key == -1:
            time.sleep(0.05)
            continue
        if key == curses.KEY_RESIZE:
            curses.update_lines_cols()
            stdscr.clear()
            last_size = stdscr.getmaxyx()
            continue

        if key in (curses.KEY_DOWN, ord("j")):
            dashboard.cursor = min(dashboard.cursor + 1, max(0, len(dashboard.views) - 1))
        elif key in (curses.KEY_UP, ord("k")):
            dashboard.cursor = max(dashboard.cursor - 1, 0)
        # The action keys are shared: in simple mode the selection is the row
        # under the cursor, in multi-select mode it is every checked row.
        elif key in (curses.KEY_ENTER, 10, 13):
            dashboard.act("start")
        elif key == ord("s"):
            dashboard.act("stop")
        elif key == ord("r"):
            dashboard.act("restart")
        elif key == ord("x"):
            dashboard.act("close")
        elif key == ord(" "):
            # Checking a row is what opens multi-select mode: one key for
            # "these ones too", rather than a mode to enter before selecting.
            dashboard.toggle_check()
        elif key == 27:  # escape
            if dashboard.escape():
                return
        elif key == ord("q") and dashboard.mode == MODE_SIMPLE:
            # Kept alongside `esc`: it is the TUI convention, and it was the
            # only way out while `esc` meant "leave edit mode".
            return
