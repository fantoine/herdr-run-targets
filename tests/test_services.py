import os
import sys
import unittest

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
    skip_message,
)
from run_targets.config import Target
from run_targets.state import ServiceRecord, TabRecord
from run_targets.tui import MODE_EDIT, MODE_VIEW, footer_text, format_row


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
        message = skip_message("db", "stop", STOPPED)
        self.assertIn("db", message)
        self.assertIn("stopped", message)


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

    def pane_close(self, pane_id):
        self.calls.append(("close", pane_id))


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
        self.assertEqual(client.calls[0], ("split", "w1:p1", "right"))
        self.assertEqual(client.calls[1], ("run", "w1:p7", "run-it"))
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

    def test_restarting_stops_then_starts_in_the_same_pane(self):
        tab = TabRecord("w1:p1", "w1:p2", {"api": ServiceRecord("w1:p2")})
        client = FakeClient(panes={"w1:p1", "w1:p2"}, foreground={"w1:p2": True})
        views = [ServiceView(target=target(), state=RUNNING, pane_id="w1:p2")]
        apply_action("restart", views, tab, "/repo", client, "w1:t1")
        self.assertEqual(client.calls, [("keys", "w1:p2", ("ctrl+c",)), ("run", "w1:p2", "run-it")])
        self.assertFalse(tab.services["api"].stop_requested)


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

    def test_a_local_target_is_labelled(self):
        row = format_row(self._view(origin="local"), checked=False, cursor=False, mode=MODE_VIEW)
        self.assertIn("local", row)

    def test_a_team_target_carries_no_origin_label(self):
        row = format_row(self._view(origin="team"), checked=False, cursor=False, mode=MODE_VIEW)
        self.assertNotIn("team", row)


class FooterTextTest(unittest.TestCase):
    def test_view_mode_advertises_edit_and_quit(self):
        text = footer_text(MODE_VIEW)
        self.assertIn("e", text)
        self.assertIn("q", text)

    def test_edit_mode_advertises_the_actions(self):
        text = footer_text(MODE_EDIT)
        for key in ("space", "enter", "s", "r", "x", "esc"):
            self.assertIn(key, text)


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


if __name__ == "__main__":
    unittest.main()
