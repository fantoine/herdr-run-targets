"""Tableau de bord curses : rendu, modes, boucle clavier."""

from __future__ import annotations

import curses
import time

from . import herdr
from .config import ORIGIN_LOCAL, load_run_config
from .services import ServiceView, apply_action, observe, resolve_selection
from .state import TabRecord, load_state, save_state

MODE_VIEW = "view"
MODE_EDIT = "edit"

REFRESH_SECONDS = 1.0


def format_row(view: ServiceView, checked: bool, cursor: bool, mode: str) -> str:
    """Une ligne du tableau, en texte pur pour rester testable."""
    marker = ">" if cursor else " "
    box = ("[x] " if checked else "[ ] ") if mode == MODE_EDIT else ""
    origin = "  local" if view.target.origin == ORIGIN_LOCAL else ""
    return f"{marker} {box}{view.target.name:<16}{view.state:<10}{origin}"


def footer_text(mode: str) -> str:
    """La barre d'aide, qui change avec le mode.

    Le mode vue n'offre aucune touche destructrice : c'est d'abord un affichage.
    Il n'y a pas non plus de touche « focus » — Herdr 0.8.2 n'expose aucun moyen
    de donner le focus à un pane arbitraire par son identifiant, l'utilisateur
    navigue avec le préfixe Herdr.
    """
    if mode == MODE_EDIT:
        return "EDIT  space select  enter start  s stop  r restart  x close  esc cancel"
    return "VIEW  e edit  q close"


class Dashboard:
    """L'état de l'écran : mode, curseur, cases cochées, dernier message."""

    def __init__(self, tab_id: str, repo_root: str, warnings: list[str]) -> None:
        self.tab_id = tab_id
        self.repo_root = repo_root
        self.mode = MODE_VIEW
        self.cursor = 0
        self.checked: set[str] = set()
        self.warnings: list[str] = list(warnings)
        self.messages: list[str] = []
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
        # `refresh` ne touche jamais `messages` : un avertissement de configuration
        # est un état permanent des fichiers, le retour d'une action est un
        # événement ponctuel. Les confondre effacerait le « skipped » que
        # l'utilisateur doit voir, à chaque rafraîchissement.
        self.warnings = warnings
        self.views = observe(self.tab(), targets, herdr, self.tab_id)
        if self.cursor >= len(self.views):
            self.cursor = max(0, len(self.views) - 1)

    def act(self, action: str) -> None:
        selected = resolve_selection(self.names(), self.checked, self.cursor_name())
        chosen = [view for view in self.views if view.target.name in selected]
        state = load_state()
        tab = state.setdefault(self.tab_id, TabRecord())
        self.messages = apply_action(action, chosen, tab, self.repo_root, herdr, self.tab_id)
        save_state(state)
        self.checked.clear()
        self.mode = MODE_VIEW
        self.refresh()


def run_dashboard(stdscr, dashboard: Dashboard) -> None:
    """Boucle de rendu et de clavier."""
    curses.curs_set(0)
    stdscr.nodelay(True)
    last_refresh = 0.0

    while True:
        now = time.monotonic()
        if now - last_refresh >= REFRESH_SECONDS:
            dashboard.refresh()
            last_refresh = now

        stdscr.erase()
        height, width = stdscr.getmaxyx()
        if height < 4 or width < 30:
            stdscr.addstr(0, 0, "Terminal too small"[: max(0, width - 1)])
            stdscr.refresh()
            time.sleep(0.2)
            continue

        stdscr.addstr(0, 0, "RUN TARGETS"[: width - 1], curses.A_BOLD)
        for index, view in enumerate(dashboard.views):
            if index + 2 >= height - 2:
                break
            row = format_row(
                view,
                checked=view.target.name in dashboard.checked,
                cursor=index == dashboard.cursor,
                mode=dashboard.mode,
            )
            stdscr.addstr(index + 2, 0, row[: width - 1])

        if not dashboard.views:
            stdscr.addstr(2, 0, "No targets. Add .herdr-run.toml or .herdr-run.local.toml"[: width - 1])

        footer_message = dashboard.messages or dashboard.warnings
        if footer_message:
            stdscr.addstr(height - 2, 0, footer_message[0][: width - 1])
        attribute = curses.A_REVERSE if dashboard.mode == MODE_EDIT else curses.A_DIM
        stdscr.addstr(height - 1, 0, footer_text(dashboard.mode)[: width - 1], attribute)
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
            if key == 27:  # échap
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
