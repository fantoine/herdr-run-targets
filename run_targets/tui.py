"""Tableau de bord curses : rendu, modes, boucle clavier."""

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

# Un retour d'action est un événement ponctuel : passé ce délai il s'efface, ce
# qui rend de nouveau visibles les avertissements de configuration, eux
# permanents. Sans expiration, un seul « skipped » les masquerait pour toujours.
MESSAGE_SECONDS = 6.0

NAME_WIDTH = 12
STATE_WIDTH = 8
LOCAL_MARKER = " *"
SMALL_SCREEN_TEXT = "Too small - q to close"


def format_row(view: ServiceView, checked: bool, cursor: bool, mode: str) -> str:
    """Une ligne du tableau, en texte pur pour rester testable.

    Les colonnes sont taillées pour qu'une ligne complète tienne dans les 30
    caractères minimaux du tableau de bord : au-delà, c'est le marqueur
    d'origine qui disparaissait le premier, et la promesse « une commande
    différente n'est jamais un mystère » avec lui.
    """
    marker = ">" if cursor else " "
    box = ("[x] " if checked else "[ ] ") if mode == MODE_EDIT else ""
    origin = LOCAL_MARKER if view.target.origin == ORIGIN_LOCAL else ""
    name = view.target.name[:NAME_WIDTH]
    return f"{marker} {box}{name:<{NAME_WIDTH}}{view.state:<{STATE_WIDTH}}{origin}"


def header_text(repo_root: str) -> str:
    """Le titre, qui nomme le dépôt observé.

    Sans ce nom, un tableau de bord ouvert sur le mauvais répertoire — le
    plugin est lui-même un dépôt git — annonce « aucune target » avec le même
    aplomb qu'un dépôt réellement vide.
    """
    return f"RUN TARGETS  {os.path.basename(os.path.normpath(repo_root))}"


def empty_text(repo_root: str) -> str:
    """La ligne d'un dépôt sans target, qui nomme le répertoire inspecté."""
    name = os.path.basename(os.path.normpath(repo_root))
    return f"No targets in {name}. Add .herdr-run.toml or .herdr-run.local.toml"


def visible_lines(
    messages: list[str], warnings: list[str], capacity: int
) -> list[str]:
    """Les lignes à afficher en pied de tableau, et ce qui n'y tient pas.

    Une action porte sur une sélection : n'en montrer qu'une ligne cacherait la
    plupart des « skipped » d'un lot. Quand tout ne tient pas, la dernière ligne
    compte le reste plutôt que de le taire.
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
    """La barre d'aide, qui change avec le mode.

    Le mode vue n'offre aucune touche destructrice : c'est d'abord un affichage.
    Il n'y a pas non plus de touche « focus » — Herdr 0.8.2 n'expose aucun moyen
    de donner le focus à un pane arbitraire par son identifiant. Reste le
    préfixe Herdr, dont on n'a pas vérifié qu'un TUI curses le laisse passer.
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
        # `refresh` ne touche jamais `messages` : un avertissement de configuration
        # est un état permanent des fichiers, le retour d'une action est un
        # événement ponctuel. Les confondre effacerait le « skipped » que
        # l'utilisateur doit voir, à chaque rafraîchissement.
        self.warnings = warnings
        self.views = observe(self.tab(), targets, herdr, self.tab_id)
        if self.cursor >= len(self.views):
            self.cursor = max(0, len(self.views) - 1)

    def set_messages(self, messages: list[str]) -> None:
        """Pose les messages d'action et l'instant de leur affichage."""
        self.messages = list(messages)
        self.messages_at = time.monotonic()

    def expire_messages(self, now: float) -> None:
        """Efface les messages d'action périmés."""
        if self.messages and now - self.messages_at >= MESSAGE_SECONDS:
            self.messages = []

    def tick(self) -> None:
        """Rafraîchit sans laisser une panne de Herdr emporter le pane.

        Un appel qui échoue — serveur qui redémarre, pane fermé entre deux
        commandes — ne doit pas dérouler la pile jusqu'à la sortie du TUI : la
        promesse du tableau de bord est d'être un pane qu'on laisse ouvert. Les
        vues précédentes restent affichées, périmées mais lisibles.
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
        self.refresh()


def run_dashboard(stdscr, dashboard: Dashboard) -> None:
    """Boucle de rendu et de clavier."""
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
            # Le clavier est lu ici aussi : un pane trop étroit qui ignore
            # toutes les touches ne peut plus être fermé de l'intérieur, et
            # une poignée de divider suffit à l'y réduire.
            stdscr.addstr(0, 0, SMALL_SCREEN_TEXT[: max(0, width - 1)])
            stdscr.refresh()
            key = stdscr.getch()
            if key == ord("q"):
                return
            if key == -1:
                time.sleep(0.05)
            continue

        stdscr.addstr(0, 0, header_text(dashboard.repo_root)[: width - 1], curses.A_BOLD)
        rows_capacity = max(0, height - 4)
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

        lines = visible_lines(
            dashboard.messages, dashboard.warnings, max(0, height - 3 - used)
        )
        for offset, line in enumerate(lines):
            stdscr.addstr(height - 1 - len(lines) + offset, 0, line[: width - 1])
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
