import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from run_targets.services import (
    EXITED,
    GONE,
    IDLE,
    OP_CLOSE,
    OP_CREATE,
    OP_FORGET,
    OP_RESTART,
    OP_SKIP,
    OP_START,
    OP_STOP,
    RUNNING,
    STOPPED,
    ServiceView,
    apply_action,
    derive_state,
    next_split_target,
    observe,
    plan_action,
    resolve_selection,
    restart_blocked_message,
    skip_message,
)
from run_targets.config import Target
from run_targets.state import ServiceRecord, TabRecord
from run_targets.tui import (
    footer_lines,
    MODE_EDIT,
    MODE_VIEW,
    empty_text,
    footer_text,
    format_row,
    header_text,
    visible_lines,
)


def no_sleep():
    """Neutralise l'attente d'arrêt : aucun test ne doit dormir pour de vrai."""
    return patch("run_targets.services._sleep", lambda seconds: None)


class DeriveStateTest(unittest.TestCase):
    def test_no_record_is_idle(self):
        self.assertEqual(derive_state(None, False, False), IDLE)

    def test_a_record_whose_pane_vanished_is_gone(self):
        self.assertEqual(derive_state(ServiceRecord("w1:p2"), False, False), GONE)

    def test_a_foreground_process_is_running(self):
        self.assertEqual(derive_state(ServiceRecord("w1:p2"), True, True), RUNNING)

    def test_stopped_when_the_plugin_asked_for_it(self):
        record = ServiceRecord("w1:p2", stop_requested=True)
        self.assertEqual(derive_state(record, True, False), STOPPED)

    def test_exited_when_nobody_asked(self):
        record = ServiceRecord("w1:p2", stop_requested=False)
        self.assertEqual(derive_state(record, True, False), EXITED)

    def test_a_running_process_is_running_even_if_a_stop_was_requested(self):
        """Le processus n'a pas encore répondu au ctrl+C : il tourne toujours."""
        record = ServiceRecord("w1:p2", stop_requested=True)
        self.assertEqual(derive_state(record, True, True), RUNNING)


class PlanActionTest(unittest.TestCase):
    """La table d'idempotence du spec, une assertion par case."""

    def test_start(self):
        self.assertEqual(plan_action("start", RUNNING), OP_SKIP)
        self.assertEqual(plan_action("start", STOPPED), OP_START)
        self.assertEqual(plan_action("start", EXITED), OP_START)
        self.assertEqual(plan_action("start", IDLE), OP_CREATE)
        self.assertEqual(plan_action("start", GONE), OP_CREATE)

    def test_stop(self):
        self.assertEqual(plan_action("stop", RUNNING), OP_STOP)
        self.assertEqual(plan_action("stop", STOPPED), OP_SKIP)
        self.assertEqual(plan_action("stop", EXITED), OP_SKIP)
        self.assertEqual(plan_action("stop", IDLE), OP_SKIP)
        self.assertEqual(plan_action("stop", GONE), OP_SKIP)

    def test_restart(self):
        self.assertEqual(plan_action("restart", RUNNING), OP_RESTART)
        self.assertEqual(plan_action("restart", STOPPED), OP_START)
        self.assertEqual(plan_action("restart", EXITED), OP_START)
        self.assertEqual(plan_action("restart", IDLE), OP_CREATE)
        self.assertEqual(plan_action("restart", GONE), OP_CREATE)

    def test_close(self):
        self.assertEqual(plan_action("close", RUNNING), OP_CLOSE)
        self.assertEqual(plan_action("close", STOPPED), OP_CLOSE)
        self.assertEqual(plan_action("close", EXITED), OP_CLOSE)
        self.assertEqual(plan_action("close", IDLE), OP_SKIP)
        self.assertEqual(plan_action("close", GONE), OP_FORGET)

    def test_an_unknown_action_is_skipped(self):
        self.assertEqual(plan_action("dance", RUNNING), OP_SKIP)


class NextSplitTargetTest(unittest.TestCase):
    def test_splits_down_from_the_last_service_pane(self):
        tab = TabRecord("w1:p1", "w1:p5", {"api": ServiceRecord("w1:p5")})
        self.assertEqual(next_split_target(tab, {"w1:p1", "w1:p5"}), ("w1:p5", "down"))

    def test_the_first_service_splits_right_from_the_control_pane(self):
        tab = TabRecord("w1:p1", None, {})
        self.assertEqual(next_split_target(tab, {"w1:p1"}), ("w1:p1", "right"))

    def test_falls_back_to_the_control_pane_when_the_last_service_vanished(self):
        tab = TabRecord("w1:p1", "w1:p5", {})
        self.assertEqual(next_split_target(tab, {"w1:p1"}), ("w1:p1", "right"))

    def test_returns_none_without_a_live_control_pane(self):
        tab = TabRecord("w1:p1", None, {})
        self.assertIsNone(next_split_target(tab, set()))

    def test_never_targets_a_pane_the_plugin_does_not_own(self):
        """Le pane de herdr-sidebar vit dans le même onglet et ne doit jamais
        servir de point de split."""
        tab = TabRecord("w1:p1", None, {})
        target = next_split_target(tab, {"w1:p1", "w1:p9"})
        self.assertEqual(target, ("w1:p1", "right"))


class ResolveSelectionTest(unittest.TestCase):
    def test_checked_names_win(self):
        self.assertEqual(
            resolve_selection(["api", "web", "db"], {"api", "db"}, "web"), ["api", "db"]
        )

    def test_checked_names_keep_the_display_order(self):
        self.assertEqual(
            resolve_selection(["api", "web", "db"], {"db", "api"}, None), ["api", "db"]
        )

    def test_falls_back_to_the_cursor_when_nothing_is_checked(self):
        self.assertEqual(resolve_selection(["api", "web"], set(), "web"), ["web"])

    def test_nothing_checked_and_no_cursor_selects_nothing(self):
        self.assertEqual(resolve_selection(["api"], set(), None), [])

    def test_a_checked_name_that_no_longer_exists_is_dropped(self):
        self.assertEqual(resolve_selection(["api"], {"api", "ghost"}, None), ["api"])


class SkipMessageTest(unittest.TestCase):
    def test_names_the_target_the_action_and_the_state(self):
        self.assertEqual(
            skip_message("db", "stop", STOPPED), "db: already stopped, stop skipped"
        )

    def test_the_action_is_part_of_the_message(self):
        """Deux messages d'omission coexistent désormais ; ils doivent se
        distinguer par autre chose que le nom du service."""
        self.assertEqual(
            skip_message("db", "close", IDLE), "db: already idle, close skipped"
        )

    def test_a_blocked_restart_says_the_service_is_still_running(self):
        self.assertEqual(
            restart_blocked_message("api"),
            "api: still running after stop, restart skipped",
        )


class FakeClient:
    """Un double du module `herdr`, qui enregistre ce qu'on lui demande."""

    def __init__(self, panes=None, foreground=None, split_result="w1:p9", fail=None):
        self.panes = panes if panes is not None else {}
        self.foreground = foreground or {}
        self.split_result = split_result
        self.fail = fail or set()
        self.calls = []

    def panes_in_tab(self, tab_id):
        return {pane_id: {"pane_id": pane_id} for pane_id in self.panes}

    def process_info(self, pane_id):
        self.calls.append(("poll", pane_id))
        return {"pane_id": pane_id}

    def has_foreground_command(self, info):
        return self.foreground.get(info["pane_id"], False)

    def pane_split(self, pane_id, direction, ratio=None, cwd=None, env=None):
        self.calls.append(("split", pane_id, direction))
        if "split" in self.fail:
            raise RuntimeError("split refused")
        self.panes.add(self.split_result) if isinstance(self.panes, set) else None
        return self.split_result

    def pane_run(self, pane_id, command):
        self.calls.append(("run", pane_id, command))
        if "run" in self.fail:
            raise RuntimeError("run refused")

    def pane_send_keys(self, pane_id, *keys):
        self.calls.append(("keys", pane_id, keys))

    def pane_rename(self, pane_id, label):
        self.calls.append(("rename", pane_id, label))

    def pane_close(self, pane_id):
        self.calls.append(("close", pane_id))


class DyingClient(FakeClient):
    """Un pane dont le premier plan se libère après `alive_polls` sondages.

    `alive_polls=None` modélise le service qui ignore le ctrl+C.
    """

    def __init__(self, alive_polls, **kwargs):
        super().__init__(**kwargs)
        self.alive_polls = alive_polls

    def process_info(self, pane_id):
        self.calls.append(("poll", pane_id))
        if self.alive_polls is None:
            return {"pane_id": pane_id, "alive": True}
        alive = self.alive_polls > 0
        self.alive_polls -= 1
        return {"pane_id": pane_id, "alive": alive}

    def has_foreground_command(self, info):
        return info["alive"]


def target(name="api", command="run-it"):
    return Target(name=name, command=command, cwd=None, env={}, origin="team")


class ObserveTest(unittest.TestCase):
    def test_an_untracked_target_is_idle(self):
        tab = TabRecord("w1:p1", None, {})
        views = observe(tab, [target()], FakeClient(panes={"w1:p1"}), "w1:t1")
        self.assertEqual(views[0].state, IDLE)
        self.assertIsNone(views[0].pane_id)

    def test_a_tracked_pane_with_a_foreground_process_is_running(self):
        tab = TabRecord("w1:p1", "w1:p2", {"api": ServiceRecord("w1:p2")})
        client = FakeClient(panes={"w1:p1", "w1:p2"}, foreground={"w1:p2": True})
        views = observe(tab, [target()], client, "w1:t1")
        self.assertEqual(views[0].state, RUNNING)
        self.assertEqual(views[0].pane_id, "w1:p2")

    def test_a_tracked_pane_that_vanished_is_gone(self):
        tab = TabRecord("w1:p1", "w1:p2", {"api": ServiceRecord("w1:p2")})
        views = observe(tab, [target()], FakeClient(panes={"w1:p1"}), "w1:t1")
        self.assertEqual(views[0].state, GONE)


class ApplyActionTest(unittest.TestCase):
    def test_starting_an_idle_target_splits_then_runs(self):
        tab = TabRecord("w1:p1", None, {})
        client = FakeClient(panes={"w1:p1"}, split_result="w1:p7")
        views = [ServiceView(target=target(), state=IDLE, pane_id=None)]
        apply_action("start", views, tab, "/repo", client, "w1:t1")
        self.assertEqual(
            client.calls,
            [
                ("split", "w1:p1", "right"),
                ("rename", "w1:p7", "api"),
                ("run", "w1:p7", "run-it"),
            ],
        )
        self.assertEqual(tab.services["api"].pane_id, "w1:p7")
        self.assertEqual(tab.last_service_pane_id, "w1:p7")
        self.assertFalse(tab.services["api"].stop_requested)

    def test_starting_a_stopped_target_reuses_its_pane(self):
        tab = TabRecord("w1:p1", "w1:p2", {"api": ServiceRecord("w1:p2", stop_requested=True)})
        client = FakeClient(panes={"w1:p1", "w1:p2"})
        views = [ServiceView(target=target(), state=STOPPED, pane_id="w1:p2")]
        apply_action("start", views, tab, "/repo", client, "w1:t1")
        self.assertEqual(client.calls, [("run", "w1:p2", "run-it")])
        self.assertFalse(tab.services["api"].stop_requested)

    def test_stopping_sends_ctrl_c_and_records_the_request(self):
        tab = TabRecord("w1:p1", "w1:p2", {"api": ServiceRecord("w1:p2")})
        client = FakeClient(panes={"w1:p1", "w1:p2"}, foreground={"w1:p2": True})
        views = [ServiceView(target=target(), state=RUNNING, pane_id="w1:p2")]
        apply_action("stop", views, tab, "/repo", client, "w1:t1")
        self.assertEqual(client.calls, [("keys", "w1:p2", ("ctrl+c",))])
        self.assertTrue(tab.services["api"].stop_requested)

    def test_closing_removes_the_pane_and_forgets_the_service(self):
        tab = TabRecord("w1:p1", "w1:p2", {"api": ServiceRecord("w1:p2")})
        client = FakeClient(panes={"w1:p1", "w1:p2"})
        views = [ServiceView(target=target(), state=STOPPED, pane_id="w1:p2")]
        apply_action("close", views, tab, "/repo", client, "w1:t1")
        self.assertEqual(client.calls, [("close", "w1:p2")])
        self.assertNotIn("api", tab.services)
        self.assertIsNone(tab.last_service_pane_id)

    def test_closing_a_gone_service_only_forgets_it(self):
        tab = TabRecord("w1:p1", "w1:p2", {"api": ServiceRecord("w1:p2")})
        client = FakeClient(panes={"w1:p1"})
        views = [ServiceView(target=target(), state=GONE, pane_id="w1:p2")]
        apply_action("close", views, tab, "/repo", client, "w1:t1")
        self.assertEqual(client.calls, [])
        self.assertNotIn("api", tab.services)

    def test_a_skipped_action_is_reported_and_touches_nothing(self):
        tab = TabRecord("w1:p1", "w1:p2", {"api": ServiceRecord("w1:p2", stop_requested=True)})
        client = FakeClient(panes={"w1:p1", "w1:p2"})
        views = [ServiceView(target=target(), state=STOPPED, pane_id="w1:p2")]
        messages = apply_action("stop", views, tab, "/repo", client, "w1:t1")
        self.assertEqual(client.calls, [])
        self.assertEqual(len(messages), 1)
        self.assertIn("skipped", messages[0])

    def test_one_failing_target_does_not_stop_the_others(self):
        tab = TabRecord("w1:p1", None, {})
        client = FakeClient(panes={"w1:p1"}, fail={"run"})
        views = [
            ServiceView(target=target("api"), state=IDLE, pane_id=None),
            ServiceView(target=target("web"), state=IDLE, pane_id=None),
        ]
        messages = apply_action("start", views, tab, "/repo", client, "w1:t1")
        self.assertEqual(len([c for c in client.calls if c[0] == "split"]), 2)
        self.assertEqual(len(messages), 2)
        self.assertTrue(all("refused" in m for m in messages))

    def test_a_target_cwd_is_resolved_against_the_repository_root(self):
        tab = TabRecord("w1:p1", None, {})
        captured = {}

        class CwdClient(FakeClient):
            def pane_split(self, pane_id, direction, ratio=None, cwd=None, env=None):
                captured["cwd"] = cwd
                return "w1:p7"

        views = [
            ServiceView(
                target=Target("web", "serve", cwd="apps/web", env={}, origin="team"),
                state=IDLE,
                pane_id=None,
            )
        ]
        apply_action("start", views, tab, "/repo", CwdClient(panes={"w1:p1"}), "w1:t1")
        self.assertEqual(captured["cwd"], os.path.join("/repo", "apps/web"))

    def test_a_failed_start_still_tracks_the_pane_it_created(self):
        """Sans cela, une relance splitterait un pane de plus à chaque échec."""
        tab = TabRecord("w1:p1", None, {})
        client = FakeClient(panes={"w1:p1"}, split_result="w1:p7", fail={"run"})
        views = [ServiceView(target=target(), state=IDLE, pane_id=None)]
        messages = apply_action("start", views, tab, "/repo", client, "w1:t1")
        self.assertEqual(tab.services["api"].pane_id, "w1:p7")
        self.assertEqual(tab.last_service_pane_id, "w1:p7")
        self.assertEqual(len(messages), 1)

    def test_restarting_waits_for_the_process_to_die_before_starting(self):
        """Lancer la commande sans attendre la ferait avaler par l'entrée
        standard du processus mourant : le service resterait arrêté."""
        tab = TabRecord("w1:p1", "w1:p2", {"api": ServiceRecord("w1:p2")})
        client = DyingClient(alive_polls=1, panes={"w1:p1", "w1:p2"})
        views = [ServiceView(target=target(), state=RUNNING, pane_id="w1:p2")]
        with no_sleep():
            messages = apply_action("restart", views, tab, "/repo", client, "w1:t1")
        self.assertEqual(
            client.calls,
            [
                ("keys", "w1:p2", ("ctrl+c",)),
                ("poll", "w1:p2"),
                ("poll", "w1:p2"),
                ("run", "w1:p2", "run-it"),
            ],
        )
        self.assertEqual(messages, [])
        self.assertFalse(tab.services["api"].stop_requested)

    def test_a_process_that_never_dies_is_not_restarted_but_reported(self):
        tab = TabRecord("w1:p1", "w1:p2", {"api": ServiceRecord("w1:p2")})
        client = DyingClient(alive_polls=None, panes={"w1:p1", "w1:p2"})
        views = [ServiceView(target=target(), state=RUNNING, pane_id="w1:p2")]
        with no_sleep():
            messages = apply_action("restart", views, tab, "/repo", client, "w1:t1")
        self.assertEqual([call for call in client.calls if call[0] == "run"], [])
        self.assertEqual(messages, ["api: still running after stop, restart skipped"])

    def test_a_missing_split_target_is_reported_verbatim(self):
        """Le README documente ce message mot pour mot."""
        tab = TabRecord(None, None, {})
        client = FakeClient(panes=set())
        views = [ServiceView(target=target(), state=IDLE, pane_id=None)]
        messages = apply_action("start", views, tab, "/repo", client, "w1:t1")
        self.assertEqual(messages, ["api: no pane of ours to split from"])


class FormatRowTest(unittest.TestCase):
    def _view(self, state=RUNNING, origin="team"):
        return ServiceView(
            target=Target("api", "cmd", cwd=None, env={}, origin=origin),
            state=state,
            pane_id="w1:p2",
        )

    def test_view_mode_shows_no_checkbox(self):
        row = format_row(self._view(), checked=False, cursor=False, mode=MODE_VIEW)
        self.assertNotIn("[", row)
        self.assertIn("api", row)
        self.assertIn("running", row)

    def test_edit_mode_shows_an_empty_checkbox(self):
        row = format_row(self._view(), checked=False, cursor=False, mode=MODE_EDIT)
        self.assertIn("[ ]", row)

    def test_edit_mode_shows_a_checked_checkbox(self):
        row = format_row(self._view(), checked=True, cursor=False, mode=MODE_EDIT)
        self.assertIn("[x]", row)

    def test_the_cursor_row_is_marked(self):
        row = format_row(self._view(), checked=False, cursor=True, mode=MODE_VIEW)
        self.assertTrue(row.startswith(">"))

    def test_a_local_target_is_marked(self):
        row = format_row(self._view(origin="local"), checked=False, cursor=False, mode=MODE_VIEW)
        self.assertTrue(row.endswith("*"), row)

    def test_a_team_target_carries_no_origin_marker(self):
        row = format_row(self._view(origin="team"), checked=False, cursor=False, mode=MODE_VIEW)
        self.assertNotIn("team", row)
        self.assertNotIn("*", row)

    def test_a_full_row_fits_the_narrowest_dashboard(self):
        """30 colonnes est la largeur minimale du tableau ; au-delà, c'est le
        marqueur d'origine qui disparaissait le premier."""
        view = ServiceView(
            target=Target("a-very-long-name", "cmd", cwd=None, env={}, origin="local"),
            state=RUNNING,
            pane_id="w1:p2",
        )
        row = format_row(view, checked=True, cursor=True, mode=MODE_EDIT)
        self.assertLessEqual(len(row), 29)
        self.assertTrue(row.endswith("*"), row)
        self.assertIn("running", row)

    def test_a_long_name_is_truncated_rather_than_pushing_the_columns(self):
        view = ServiceView(
            target=Target("abcdefghijklmnop", "cmd", cwd=None, env={}, origin="team"),
            state=RUNNING,
            pane_id="w1:p2",
        )
        row = format_row(view, checked=False, cursor=False, mode=MODE_VIEW)
        self.assertIn("abcdefghijkl", row)
        self.assertNotIn("abcdefghijklm", row)


class FooterTextTest(unittest.TestCase):
    def test_view_mode_advertises_edit_and_quit(self):
        self.assertEqual(footer_text(MODE_VIEW), "VIEW  e edit  q close")

    def test_edit_mode_advertises_the_actions(self):
        self.assertEqual(
            footer_text(MODE_EDIT),
            "EDIT  space select  enter start  s stop  r restart  x close  esc cancel",
        )


class HeaderAndEmptyTextTest(unittest.TestCase):
    """Un mauvais dépôt ne doit pas se lire comme un dépôt vide."""

    def test_the_header_names_the_repository(self):
        self.assertEqual(header_text("/home/me/projects/shop"), "RUN TARGETS  shop")

    def test_the_header_ignores_a_trailing_separator(self):
        self.assertEqual(header_text("/home/me/projects/shop/"), "RUN TARGETS  shop")

    def test_the_empty_line_names_the_directory_it_looked_in(self):
        self.assertEqual(
            empty_text("/home/me/projects/shop"),
            "No targets in shop. Add .herdr-run.toml or .herdr-run.local.toml",
        )


class VisibleLinesTest(unittest.TestCase):
    def test_every_message_is_shown_when_they_all_fit(self):
        self.assertEqual(visible_lines(["a", "b"], [], 3), ["a", "b"])

    def test_the_overflow_is_counted_rather_than_hidden(self):
        self.assertEqual(visible_lines(["a", "b", "c", "d"], [], 3), ["a", "b", "(+2 more)"])

    def test_a_single_line_of_room_only_carries_the_count(self):
        self.assertEqual(visible_lines(["a", "b"], [], 1), ["(+2 more)"])

    def test_warnings_show_when_no_message_is_live(self):
        self.assertEqual(visible_lines([], ["w1", "w2"], 5), ["w1", "w2"])

    def test_messages_take_precedence_over_warnings(self):
        self.assertEqual(visible_lines(["m"], ["w"], 5), ["m"])

    def test_no_room_shows_nothing(self):
        self.assertEqual(visible_lines(["a"], [], 0), [])


class DashboardMessagesTest(unittest.TestCase):
    """Le retour d'une action doit survivre au rafraîchissement qui le suit."""

    def test_refresh_does_not_erase_the_action_feedback(self):
        from unittest.mock import patch
        import tempfile
        from run_targets.tui import Dashboard

        class NoPanes:
            def panes_in_tab(self, tab_id):
                return {}

            def process_info(self, pane_id):
                return {}

            def has_foreground_command(self, info):
                return False

        with tempfile.TemporaryDirectory() as root:
            # Un doublon dans un même fichier produit un avertissement à chaque
            # lecture : c'est la condition qui faisait disparaître le message.
            with open(os.path.join(root, ".herdr-run.toml"), "w", encoding="utf-8") as handle:
                handle.write(
                    '[[target]]\nname = "api"\ncommand = "a"\n'
                    '[[target]]\nname = "api"\ncommand = "b"\n'
                )
            dashboard = Dashboard(tab_id="w1:t1", repo_root=root, warnings=[])
            dashboard.messages = ["api: already stopped, stop skipped"]
            with patch("run_targets.tui.herdr", NoPanes()):
                dashboard.refresh()
            self.assertEqual(dashboard.messages, ["api: already stopped, stop skipped"])
            self.assertTrue(dashboard.warnings)

    def test_action_messages_expire_so_warnings_come_back(self):
        """Sans expiration, un seul « skipped » masquerait à jamais les
        avertissements de configuration, qui sont eux permanents."""
        from run_targets.tui import MESSAGE_SECONDS, Dashboard

        dashboard = Dashboard(tab_id="w1:t1", repo_root="/repo", warnings=["boom"])
        with patch("run_targets.tui.time.monotonic", return_value=100.0):
            dashboard.set_messages(["api: already stopped, stop skipped"])
        dashboard.expire_messages(100.0 + MESSAGE_SECONDS - 0.1)
        self.assertTrue(dashboard.messages)
        dashboard.expire_messages(100.0 + MESSAGE_SECONDS)
        self.assertEqual(dashboard.messages, [])
        self.assertEqual(dashboard.warnings, ["boom"])


class DashboardTickTest(unittest.TestCase):
    """Un appel Herdr en échec ne doit pas emporter le pane."""

    def test_a_failing_refresh_keeps_the_previous_views_and_says_so(self):
        import tempfile
        from run_targets.tui import Dashboard

        class Broken:
            def panes_in_tab(self, tab_id):
                raise RuntimeError("herdr pane list failed: socket closed")

        with tempfile.TemporaryDirectory() as root:
            dashboard = Dashboard(tab_id="w1:t1", repo_root=root, warnings=[])
            previous = [ServiceView(target=target(), state=RUNNING, pane_id="w1:p2")]
            dashboard.views = previous
            with patch("run_targets.tui.herdr", Broken()), \
                 patch.dict(os.environ, {"HERDR_PLUGIN_STATE_DIR": root}, clear=False):
                dashboard.tick()
            self.assertEqual(dashboard.views, previous)
            self.assertEqual(
                dashboard.messages, ["herdr pane list failed: socket closed"]
            )

    def test_the_refresh_that_follows_an_action_is_guarded_too(self):
        """`act` est le moment le plus exposé — le plugin vient d'enchaîner
        plusieurs appels Herdr — et sa relecture doit passer par la même route
        gardée que la boucle périodique."""
        import tempfile
        from run_targets.tui import Dashboard

        class Broken:
            def panes_in_tab(self, tab_id):
                raise RuntimeError("herdr pane list failed: socket closed")

        with tempfile.TemporaryDirectory() as root:
            dashboard = Dashboard(tab_id="w1:t1", repo_root=root, warnings=[])
            previous = [ServiceView(target=target(), state=RUNNING, pane_id="w1:p2")]
            dashboard.views = previous
            with patch("run_targets.tui.herdr", Broken()), \
                 patch.dict(os.environ, {"HERDR_PLUGIN_STATE_DIR": root}, clear=False):
                dashboard.act("start")
            self.assertEqual(dashboard.views, previous)
            # L'erreur prend la place du retour de l'action : savoir que
            # l'affichage n'est plus fiable prime sur un « skipped ».
            self.assertEqual(
                dashboard.messages, ["herdr pane list failed: socket closed"]
            )


class ServicePaneNamingTest(unittest.TestCase):
    """Un pane de service porte le nom de sa target, pour se lire d'un coup d'œil."""

    def test_a_created_pane_is_renamed_after_its_target(self):
        tab = TabRecord("w1:p1", None, {})
        client = FakeClient(panes={"w1:p1"}, split_result="w1:p7")
        views = [ServiceView(target=target("api"), state=IDLE, pane_id=None)]
        apply_action("start", views, tab, "/repo", client, "w1:t1")
        self.assertIn(("rename", "w1:p7", "api"), client.calls)

    def test_the_rename_happens_before_the_command_runs(self):
        """Sinon le titre du terminal, déjà posé par la commande, gagnerait."""
        tab = TabRecord("w1:p1", None, {})
        client = FakeClient(panes={"w1:p1"}, split_result="w1:p7")
        views = [ServiceView(target=target("api"), state=IDLE, pane_id=None)]
        apply_action("start", views, tab, "/repo", client, "w1:t1")
        kinds = [c[0] for c in client.calls]
        self.assertLess(kinds.index("rename"), kinds.index("run"))

    def test_a_failed_rename_does_not_stop_the_service(self):
        tab = TabRecord("w1:p1", None, {})
        client = FakeClient(panes={"w1:p1"}, split_result="w1:p7", fail={"rename"})
        views = [ServiceView(target=target("api"), state=IDLE, pane_id=None)]
        messages = apply_action("start", views, tab, "/repo", client, "w1:t1")
        self.assertIn(("run", "w1:p7", "run-it"), client.calls)
        self.assertEqual(messages, [])

    def test_restarting_in_an_existing_pane_does_not_rename(self):
        tab = TabRecord("w1:p1", "w1:p2", {"api": ServiceRecord("w1:p2")})
        client = FakeClient(panes={"w1:p1", "w1:p2"})
        views = [ServiceView(target=target("api"), state=STOPPED, pane_id="w1:p2")]
        apply_action("start", views, tab, "/repo", client, "w1:t1")
        self.assertNotIn("rename", [c[0] for c in client.calls])


class FooterLinesTest(unittest.TestCase):
    """Le pied de page se replie plutôt que de tronquer : une touche cachée
    est une touche qui n'existe pas pour l'utilisateur."""

    def test_a_wide_pane_keeps_one_line(self):
        lines = footer_lines(MODE_EDIT, 100)
        self.assertEqual(lines, [footer_text(MODE_EDIT)])

    def test_a_narrow_pane_wraps_without_losing_a_key(self):
        lines = footer_lines(MODE_EDIT, 34)
        self.assertGreater(len(lines), 1)
        joined = " ".join(lines)
        for key in ("space", "enter", "s stop", "r restart", "x close", "esc"):
            self.assertIn(key, joined)

    def test_no_line_exceeds_the_width(self):
        for width in (30, 34, 40, 55, 71):
            for line in footer_lines(MODE_EDIT, width):
                self.assertLessEqual(len(line), width, f"largeur {width}: {line!r}")

    def test_a_segment_is_never_split_across_lines(self):
        for line in footer_lines(MODE_EDIT, 30):
            self.assertFalse(line.startswith(" "))
            self.assertFalse(line.endswith(" "))

    def test_view_mode_still_fits_on_one_line(self):
        self.assertEqual(footer_lines(MODE_VIEW, 34), [footer_text(MODE_VIEW)])

    def test_an_absurdly_narrow_width_still_yields_every_segment(self):
        joined = " ".join(footer_lines(MODE_EDIT, 8))
        for key in ("space", "x close", "esc"):
            self.assertIn(key, joined)


if __name__ == "__main__":
    unittest.main()
