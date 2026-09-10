import os
import sys
import tempfile
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
    focus_target,
    observe,
    plan_action,
    resolve_selection,
    restart_blocked_message,
    skip_message,
)
from run_targets.config import Target
from run_targets.settings import FOCUS_FIRST, FOCUS_LAST, FOCUS_STAY, Settings
from run_targets.state import ServiceRecord, WorkspaceRecord
from run_targets.tui import (
    footer_lines,
    use_terminal_colors,
    MODE_EDIT,
    MODE_VIEW,
    empty_text,
    footer_text,
    format_row,
    header_text,
    visible_lines,
)


def no_sleep():
    """Neutralise the stop wait: no test should ever really sleep."""
    return patch("run_targets.services._sleep", lambda seconds: None)


def service(tab_id="w1:t7", pane_id="w1:p7", stop_requested=False):
    return ServiceRecord(tab_id=tab_id, pane_id=pane_id, stop_requested=stop_requested)


class DeriveStateTest(unittest.TestCase):
    def test_no_record_is_idle(self):
        self.assertEqual(derive_state(None, False, False), IDLE)

    def test_a_record_whose_pane_vanished_is_gone(self):
        self.assertEqual(derive_state(service(), False, False), GONE)

    def test_a_foreground_process_is_running(self):
        self.assertEqual(derive_state(service(), True, True), RUNNING)

    def test_stopped_when_the_plugin_asked_for_it(self):
        self.assertEqual(derive_state(service(stop_requested=True), True, False), STOPPED)

    def test_exited_when_nobody_asked(self):
        self.assertEqual(derive_state(service(), True, False), EXITED)

    def test_a_running_process_is_running_even_if_a_stop_was_requested(self):
        """The process has not answered the ctrl+C yet: it is still running."""
        self.assertEqual(derive_state(service(stop_requested=True), True, True), RUNNING)


class PlanActionTest(unittest.TestCase):
    """The spec's idempotence table, one assertion per cell."""

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


class FocusTargetTest(unittest.TestCase):
    def test_the_default_leaves_the_focus_alone(self):
        self.assertIsNone(focus_target(["w1:t7", "w1:t8"], FOCUS_STAY))

    def test_first_mode_takes_the_first_tab_of_the_batch(self):
        self.assertEqual(focus_target(["w1:t7", "w1:t8"], FOCUS_FIRST), "w1:t7")

    def test_last_mode_takes_the_last(self):
        self.assertEqual(focus_target(["w1:t7", "w1:t8"], FOCUS_LAST), "w1:t8")

    def test_nothing_created_means_nothing_to_focus(self):
        self.assertIsNone(focus_target([], FOCUS_LAST))


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
        """Two skip messages coexist; they must be told apart by something other
        than the service name."""
        self.assertEqual(
            skip_message("db", "close", IDLE), "db: already idle, close skipped"
        )

    def test_a_blocked_restart_says_the_service_is_still_running(self):
        self.assertEqual(
            restart_blocked_message("api"),
            "api: still running after stop, restart skipped",
        )


class FakeClient:
    """A stand-in for the `herdr` module, recording what it is asked to do."""

    def __init__(self, panes=None, foreground=None, created=None, fail=None):
        self.panes = set(panes or ())
        self.foreground = foreground or {}
        # Tabs handed out by `tab_create`, in order.
        self.created = list(created or [("w1:t7", "w1:p7"), ("w1:t8", "w1:p8")])
        self.fail = fail or set()
        self.calls = []

    def live_pane_ids(self):
        return set(self.panes)

    def process_info(self, pane_id):
        self.calls.append(("poll", pane_id))
        return {"pane_id": pane_id}

    def has_foreground_command(self, info):
        return self.foreground.get(info["pane_id"], False)

    def tab_create(self, workspace_id, label, cwd=None, env=None):
        self.calls.append(("tab_create", workspace_id, label, cwd, env))
        if "tab_create" in self.fail:
            raise RuntimeError("tab create refused")
        tab_id, pane_id = self.created.pop(0)
        self.panes.add(pane_id)
        return tab_id, pane_id

    def tab_focus(self, tab_id):
        self.calls.append(("tab_focus", tab_id))
        if "tab_focus" in self.fail:
            raise RuntimeError("focus refused")

    def tab_close(self, tab_id):
        self.calls.append(("tab_close", tab_id))

    def pane_run(self, pane_id, command):
        self.calls.append(("run", pane_id, command))
        if "run" in self.fail:
            raise RuntimeError("run refused")

    def pane_send_keys(self, pane_id, *keys):
        self.calls.append(("keys", pane_id, keys))

    def pane_close(self, pane_id):
        self.calls.append(("pane_close", pane_id))


class DyingClient(FakeClient):
    """A pane whose foreground frees up after `alive_polls` polls.

    `alive_polls=None` models the service that ignores the ctrl+C.
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


def view(state=IDLE, name="api", tab_id=None, pane_id=None):
    return ServiceView(target=target(name), state=state, tab_id=tab_id, pane_id=pane_id)


def act(action, views, record, client, workspace_id="w1", settings=None, repo_root="/repo"):
    return apply_action(
        action, views, record, repo_root, workspace_id, settings or Settings(), client
    )


class ObserveTest(unittest.TestCase):
    def test_an_untracked_target_is_idle(self):
        record = WorkspaceRecord()
        views = observe(record, [target()], FakeClient(panes={"w1:p1"}))
        self.assertEqual(views[0].state, IDLE)
        self.assertIsNone(views[0].pane_id)
        self.assertIsNone(views[0].tab_id)

    def test_a_tracked_pane_with_a_foreground_process_is_running(self):
        record = WorkspaceRecord("w1:p1", {"api": service("w1:t7", "w1:p7")})
        client = FakeClient(panes={"w1:p1", "w1:p7"}, foreground={"w1:p7": True})
        views = observe(record, [target()], client)
        self.assertEqual(views[0].state, RUNNING)
        self.assertEqual(views[0].pane_id, "w1:p7")
        self.assertEqual(views[0].tab_id, "w1:t7")

    def test_a_tracked_pane_that_vanished_is_gone(self):
        """Closing a service's tab by hand is the common way this happens."""
        record = WorkspaceRecord("w1:p1", {"api": service("w1:t7", "w1:p7")})
        views = observe(record, [target()], FakeClient(panes={"w1:p1"}))
        self.assertEqual(views[0].state, GONE)

    def test_observation_is_not_scoped_to_a_tab(self):
        """Each service lives in a tab of its own, so a pane list filtered by tab
        would report every one of them as gone."""
        record = WorkspaceRecord("w1:p1", {"api": service("w9:t3", "w9:p4")})
        client = FakeClient(panes={"w9:p4"}, foreground={"w9:p4": True})
        views = observe(record, [target()], client)
        self.assertEqual(views[0].state, RUNNING)


class ApplyActionTest(unittest.TestCase):
    def test_starting_an_idle_target_creates_a_tab_then_runs(self):
        record = WorkspaceRecord("w1:p1", {})
        client = FakeClient(panes={"w1:p1"}, created=[("w1:t7", "w1:p7")])
        act("start", [view(IDLE)], record, client)
        self.assertEqual(
            client.calls,
            [
                ("tab_create", "w1", "api", "/repo", None),
                ("run", "w1:p7", "run-it"),
            ],
        )
        self.assertEqual(record.services["api"].tab_id, "w1:t7")
        self.assertEqual(record.services["api"].pane_id, "w1:p7")
        self.assertFalse(record.services["api"].stop_requested)

    def test_starting_a_stopped_target_reuses_its_pane(self):
        record = WorkspaceRecord(
            "w1:p1", {"api": service("w1:t7", "w1:p7", stop_requested=True)}
        )
        client = FakeClient(panes={"w1:p1", "w1:p7"})
        act("start", [view(STOPPED, tab_id="w1:t7", pane_id="w1:p7")], record, client)
        self.assertEqual(client.calls, [("run", "w1:p7", "run-it")])
        self.assertFalse(record.services["api"].stop_requested)

    def test_stopping_sends_ctrl_c_and_records_the_request(self):
        record = WorkspaceRecord("w1:p1", {"api": service("w1:t7", "w1:p7")})
        client = FakeClient(panes={"w1:p1", "w1:p7"}, foreground={"w1:p7": True})
        act("stop", [view(RUNNING, tab_id="w1:t7", pane_id="w1:p7")], record, client)
        self.assertEqual(client.calls, [("keys", "w1:p7", ("ctrl+c",))])
        self.assertTrue(record.services["api"].stop_requested)

    def test_closing_closes_the_tab_and_forgets_the_service(self):
        record = WorkspaceRecord("w1:p1", {"api": service("w1:t7", "w1:p7")})
        client = FakeClient(panes={"w1:p1", "w1:p7"})
        act("close", [view(STOPPED, tab_id="w1:t7", pane_id="w1:p7")], record, client)
        self.assertEqual(client.calls, [("tab_close", "w1:t7")])
        self.assertNotIn("api", record.services)

    def test_closing_never_closes_a_lone_pane(self):
        """`x` removes the service, which now means its whole tab."""
        record = WorkspaceRecord("w1:p1", {"api": service("w1:t7", "w1:p7")})
        client = FakeClient(panes={"w1:p1", "w1:p7"})
        act("close", [view(RUNNING, tab_id="w1:t7", pane_id="w1:p7")], record, client)
        self.assertNotIn("pane_close", [call[0] for call in client.calls])

    def test_closing_a_gone_service_only_forgets_it(self):
        record = WorkspaceRecord("w1:p1", {"api": service("w1:t7", "w1:p7")})
        client = FakeClient(panes={"w1:p1"})
        act("close", [view(GONE, tab_id="w1:t7", pane_id="w1:p7")], record, client)
        self.assertEqual(client.calls, [])
        self.assertNotIn("api", record.services)

    def test_a_skipped_action_is_reported_and_touches_nothing(self):
        record = WorkspaceRecord(
            "w1:p1", {"api": service("w1:t7", "w1:p7", stop_requested=True)}
        )
        client = FakeClient(panes={"w1:p1", "w1:p7"})
        messages = act(
            "stop", [view(STOPPED, tab_id="w1:t7", pane_id="w1:p7")], record, client
        )
        self.assertEqual(client.calls, [])
        self.assertEqual(len(messages), 1)
        self.assertIn("skipped", messages[0])

    def test_one_failing_target_does_not_stop_the_others(self):
        record = WorkspaceRecord("w1:p1", {})
        client = FakeClient(panes={"w1:p1"}, fail={"run"})
        messages = act(
            "start", [view(IDLE, "api"), view(IDLE, "web")], record, client
        )
        self.assertEqual(
            len([call for call in client.calls if call[0] == "tab_create"]), 2
        )
        self.assertEqual(len(messages), 2)
        self.assertTrue(all("refused" in message for message in messages))

    def test_a_target_cwd_is_resolved_against_the_repository_root(self):
        record = WorkspaceRecord("w1:p1", {})
        client = FakeClient(panes={"w1:p1"})
        views = [
            ServiceView(
                target=Target("web", "serve", cwd="apps/web", env={}, origin="team"),
                state=IDLE,
                tab_id=None,
                pane_id=None,
            )
        ]
        act("start", views, record, client)
        created = [call for call in client.calls if call[0] == "tab_create"][0]
        self.assertEqual(created[3], os.path.join("/repo", "apps/web"))

    def test_a_target_env_reaches_the_new_tab(self):
        record = WorkspaceRecord("w1:p1", {})
        client = FakeClient(panes={"w1:p1"})
        views = [
            ServiceView(
                target=Target("web", "serve", cwd=None, env={"PORT": "3000"}, origin="team"),
                state=IDLE,
                tab_id=None,
                pane_id=None,
            )
        ]
        act("start", views, record, client)
        created = [call for call in client.calls if call[0] == "tab_create"][0]
        self.assertEqual(created[4], {"PORT": "3000"})

    def test_a_failed_start_still_tracks_the_tab_it_created(self):
        """Without this, a retry would open one more tab on every failure."""
        record = WorkspaceRecord("w1:p1", {})
        client = FakeClient(panes={"w1:p1"}, created=[("w1:t7", "w1:p7")], fail={"run"})
        messages = act("start", [view(IDLE)], record, client)
        self.assertEqual(record.services["api"].tab_id, "w1:t7")
        self.assertEqual(record.services["api"].pane_id, "w1:p7")
        self.assertEqual(len(messages), 1)

    def test_a_refused_tab_records_nothing(self):
        record = WorkspaceRecord("w1:p1", {})
        client = FakeClient(panes={"w1:p1"}, fail={"tab_create"})
        messages = act("start", [view(IDLE)], record, client)
        self.assertEqual(messages, ["api: tab create refused"])
        self.assertNotIn("api", record.services)

    def test_restarting_waits_for_the_process_to_die_before_starting(self):
        """Running the command without waiting would have it swallowed by the
        dying process's standard input: the service would stay stopped."""
        record = WorkspaceRecord("w1:p1", {"api": service("w1:t7", "w1:p7")})
        client = DyingClient(alive_polls=1, panes={"w1:p1", "w1:p7"})
        with no_sleep():
            messages = act(
                "restart", [view(RUNNING, tab_id="w1:t7", pane_id="w1:p7")], record, client
            )
        self.assertEqual(
            client.calls,
            [
                ("keys", "w1:p7", ("ctrl+c",)),
                ("poll", "w1:p7"),
                ("poll", "w1:p7"),
                ("run", "w1:p7", "run-it"),
            ],
        )
        self.assertEqual(messages, [])
        self.assertFalse(record.services["api"].stop_requested)

    def test_a_process_that_never_dies_is_not_restarted_but_reported(self):
        record = WorkspaceRecord("w1:p1", {"api": service("w1:t7", "w1:p7")})
        client = DyingClient(alive_polls=None, panes={"w1:p1", "w1:p7"})
        with no_sleep():
            messages = act(
                "restart", [view(RUNNING, tab_id="w1:t7", pane_id="w1:p7")], record, client
            )
        self.assertEqual([call for call in client.calls if call[0] == "run"], [])
        self.assertEqual(messages, ["api: still running after stop, restart skipped"])


class ServiceTabNamingTest(unittest.TestCase):
    """A service's tab carries its target's name, to be read from the tab bar."""

    def test_the_label_is_the_target_name_by_default(self):
        record = WorkspaceRecord("w1:p1", {})
        client = FakeClient(panes={"w1:p1"})
        act("start", [view(IDLE, "api")], record, client)
        created = [call for call in client.calls if call[0] == "tab_create"][0]
        self.assertEqual(created[2], "api")

    def test_the_configured_prefix_and_suffix_are_applied(self):
        record = WorkspaceRecord("w1:p1", {})
        client = FakeClient(panes={"w1:p1"})
        act(
            "start",
            [view(IDLE, "api")],
            record,
            client,
            settings=Settings(label_prefix="run:", label_suffix="!"),
        )
        created = [call for call in client.calls if call[0] == "tab_create"][0]
        self.assertEqual(created[2], "run:api!")

    def test_the_label_is_set_at_creation_not_renamed_afterwards(self):
        """One call instead of two, and the tab never flashes a generic name."""
        record = WorkspaceRecord("w1:p1", {})
        client = FakeClient(panes={"w1:p1"})
        act("start", [view(IDLE)], record, client)
        kinds = [call[0] for call in client.calls]
        self.assertEqual(kinds, ["tab_create", "run"])

    def test_restarting_in_an_existing_tab_creates_nothing(self):
        record = WorkspaceRecord("w1:p1", {"api": service("w1:t7", "w1:p7")})
        client = FakeClient(panes={"w1:p1", "w1:p7"})
        act("start", [view(STOPPED, tab_id="w1:t7", pane_id="w1:p7")], record, client)
        self.assertNotIn("tab_create", [call[0] for call in client.calls])


class FocusAfterLaunchTest(unittest.TestCase):
    def test_the_default_focuses_nothing(self):
        record = WorkspaceRecord("w1:p1", {})
        client = FakeClient(panes={"w1:p1"})
        act("start", [view(IDLE, "api"), view(IDLE, "web")], record, client)
        self.assertNotIn("tab_focus", [call[0] for call in client.calls])

    def test_first_mode_focuses_the_first_tab_of_the_batch(self):
        record = WorkspaceRecord("w1:p1", {})
        client = FakeClient(panes={"w1:p1"})
        act(
            "start",
            [view(IDLE, "api"), view(IDLE, "web")],
            record,
            client,
            settings=Settings(focus_mode=FOCUS_FIRST),
        )
        self.assertEqual(client.calls[-1], ("tab_focus", "w1:t7"))

    def test_last_mode_focuses_the_last(self):
        record = WorkspaceRecord("w1:p1", {})
        client = FakeClient(panes={"w1:p1"})
        act(
            "start",
            [view(IDLE, "api"), view(IDLE, "web")],
            record,
            client,
            settings=Settings(focus_mode=FOCUS_LAST),
        )
        self.assertEqual(client.calls[-1], ("tab_focus", "w1:t8"))

    def test_the_focus_comes_after_every_tab_exists(self):
        """Focusing between two creations would leave the batch's tail opening
        behind a tab the user is already reading."""
        record = WorkspaceRecord("w1:p1", {})
        client = FakeClient(panes={"w1:p1"})
        act(
            "start",
            [view(IDLE, "api"), view(IDLE, "web")],
            record,
            client,
            settings=Settings(focus_mode=FOCUS_FIRST),
        )
        kinds = [call[0] for call in client.calls]
        self.assertEqual(kinds.count("tab_create"), 2)
        self.assertEqual(kinds.index("tab_focus"), len(kinds) - 1)

    def test_a_failed_focus_is_reported_and_nothing_is_undone(self):
        record = WorkspaceRecord("w1:p1", {})
        client = FakeClient(panes={"w1:p1"}, fail={"tab_focus"})
        messages = act(
            "start",
            [view(IDLE)],
            record,
            client,
            settings=Settings(focus_mode=FOCUS_LAST),
        )
        self.assertIn("api", record.services)
        self.assertEqual(len(messages), 1)
        self.assertIn("focus", messages[0])

    def test_reusing_an_existing_tab_does_not_move_the_focus(self):
        record = WorkspaceRecord("w1:p1", {"api": service("w1:t7", "w1:p7")})
        client = FakeClient(panes={"w1:p1", "w1:p7"})
        act(
            "start",
            [view(STOPPED, tab_id="w1:t7", pane_id="w1:p7")],
            record,
            client,
            settings=Settings(focus_mode=FOCUS_LAST),
        )
        self.assertNotIn("tab_focus", [call[0] for call in client.calls])


class FormatRowTest(unittest.TestCase):
    def _view(self, state=RUNNING, origin="team", name="api"):
        return ServiceView(
            target=Target(name, "cmd", cwd=None, env={}, origin=origin),
            state=state,
            tab_id="w1:t7",
            pane_id="w1:p7",
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
        """30 columns is the table's minimum width; past that, the origin marker
        was the first thing to disappear."""
        row = format_row(
            self._view(origin="local", name="a-very-long-name"),
            checked=True,
            cursor=True,
            mode=MODE_EDIT,
        )
        self.assertLessEqual(len(row), 29)
        self.assertTrue(row.endswith("*"), row)
        self.assertIn("running", row)

    def test_a_long_name_is_truncated_rather_than_pushing_the_columns(self):
        row = format_row(
            self._view(name="abcdefghijklmnop"), checked=False, cursor=False, mode=MODE_VIEW
        )
        self.assertIn("abcdefghijk", row)
        self.assertNotIn("abcdefghijkl", row)

    def test_a_name_that_fills_the_column_keeps_a_space_before_the_state(self):
        """`community-sdk-playground` printed `community-sdidle` before this."""
        row = format_row(
            self._view(name="community-sdk-playground"),
            checked=False,
            cursor=False,
            mode=MODE_VIEW,
        )
        self.assertIn("community-s running", row)


class FooterTextTest(unittest.TestCase):
    def test_view_mode_advertises_edit_and_quit(self):
        self.assertEqual(footer_text(MODE_VIEW), "VIEW  e edit  q close")

    def test_edit_mode_advertises_the_actions(self):
        self.assertEqual(
            footer_text(MODE_EDIT),
            "EDIT  space select  enter start  s stop  r restart  x close  esc cancel",
        )


class HeaderAndEmptyTextTest(unittest.TestCase):
    """A wrong repository must not read as an empty one."""

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


class NoPanes:
    def live_pane_ids(self):
        return set()

    def process_info(self, pane_id):
        return {}

    def has_foreground_command(self, info):
        return False


class Broken:
    def live_pane_ids(self):
        raise RuntimeError("herdr pane list failed: socket closed")


class DashboardMessagesTest(unittest.TestCase):
    """An action's feedback must survive the refresh that follows it."""

    def test_refresh_does_not_erase_the_action_feedback(self):
        from run_targets.tui import Dashboard

        with tempfile.TemporaryDirectory() as root:
            # A duplicate inside a single file produces a warning on every read:
            # that is the condition that used to make the message vanish.
            with open(os.path.join(root, ".herdr-run.toml"), "w", encoding="utf-8") as handle:
                handle.write(
                    '[[target]]\nname = "api"\ncommand = "a"\n'
                    '[[target]]\nname = "api"\ncommand = "b"\n'
                )
            dashboard = Dashboard(workspace_id="w1", repo_root=root, warnings=[])
            dashboard.messages = ["api: already stopped, stop skipped"]
            with patch("run_targets.tui.herdr", NoPanes()), \
                 patch.dict(os.environ, {"HERDR_PLUGIN_CONFIG_DIR": root}, clear=False):
                dashboard.refresh()
            self.assertEqual(dashboard.messages, ["api: already stopped, stop skipped"])
            self.assertTrue(dashboard.warnings)

    def test_a_settings_warning_reaches_the_footer(self):
        """A rejected focus_mode is silent otherwise, and would read as accepted."""
        from run_targets.tui import Dashboard

        with tempfile.TemporaryDirectory() as root:
            with open(os.path.join(root, "config.toml"), "w", encoding="utf-8") as handle:
                handle.write('[dashboard]\nfocus_mode = "middle"\n')
            dashboard = Dashboard(workspace_id="w1", repo_root=root, warnings=[])
            with patch("run_targets.tui.herdr", NoPanes()), \
                 patch.dict(os.environ, {"HERDR_PLUGIN_CONFIG_DIR": root}, clear=False):
                dashboard.refresh()
            self.assertTrue(any("middle" in warning for warning in dashboard.warnings))
            self.assertEqual(dashboard.settings.focus_mode, FOCUS_STAY)

    def test_settings_are_re_read_on_every_refresh(self):
        from run_targets.tui import Dashboard

        with tempfile.TemporaryDirectory() as root:
            dashboard = Dashboard(workspace_id="w1", repo_root=root, warnings=[])
            with patch("run_targets.tui.herdr", NoPanes()), \
                 patch.dict(os.environ, {"HERDR_PLUGIN_CONFIG_DIR": root}, clear=False):
                dashboard.refresh()
                self.assertEqual(dashboard.settings.label_prefix, "")
                with open(os.path.join(root, "config.toml"), "w", encoding="utf-8") as handle:
                    handle.write('[tabs]\nlabel_prefix = "run:"\n')
                dashboard.refresh()
                self.assertEqual(dashboard.settings.label_prefix, "run:")

    def test_action_messages_expire_so_warnings_come_back(self):
        """Without expiry, a single "skipped" would hide the configuration
        warnings forever, and those are permanent."""
        from run_targets.tui import MESSAGE_SECONDS, Dashboard

        dashboard = Dashboard(workspace_id="w1", repo_root="/repo", warnings=["boom"])
        with patch("run_targets.tui.time.monotonic", return_value=100.0):
            dashboard.set_messages(["api: already stopped, stop skipped"])
        dashboard.expire_messages(100.0 + MESSAGE_SECONDS - 0.1)
        self.assertTrue(dashboard.messages)
        dashboard.expire_messages(100.0 + MESSAGE_SECONDS)
        self.assertEqual(dashboard.messages, [])
        self.assertEqual(dashboard.warnings, ["boom"])


class DashboardTickTest(unittest.TestCase):
    """A failing Herdr call must not take the pane down with it."""

    def test_a_failing_refresh_keeps_the_previous_views_and_says_so(self):
        from run_targets.tui import Dashboard

        with tempfile.TemporaryDirectory() as root:
            dashboard = Dashboard(workspace_id="w1", repo_root=root, warnings=[])
            previous = [view(RUNNING, tab_id="w1:t7", pane_id="w1:p7")]
            dashboard.views = previous
            with patch("run_targets.tui.herdr", Broken()), \
                 patch.dict(os.environ, {"HERDR_PLUGIN_STATE_DIR": root}, clear=False):
                dashboard.tick()
            self.assertEqual(dashboard.views, previous)
            self.assertEqual(
                dashboard.messages, ["herdr pane list failed: socket closed"]
            )

    def test_the_refresh_that_follows_an_action_is_guarded_too(self):
        """`act` is the most exposed moment -- the plugin has just chained
        several Herdr calls -- and its re-read must go through the same guarded
        route as the periodic loop."""
        from run_targets.tui import Dashboard

        with tempfile.TemporaryDirectory() as root:
            dashboard = Dashboard(workspace_id="w1", repo_root=root, warnings=[])
            previous = [view(RUNNING, tab_id="w1:t7", pane_id="w1:p7")]
            dashboard.views = previous
            with patch("run_targets.tui.herdr", Broken()), \
                 patch.dict(os.environ, {"HERDR_PLUGIN_STATE_DIR": root}, clear=False):
                dashboard.act("start")
            self.assertEqual(dashboard.views, previous)
            # The error takes the place of the action's feedback: knowing the
            # display is no longer trustworthy outranks a "skipped".
            self.assertEqual(
                dashboard.messages, ["herdr pane list failed: socket closed"]
            )


class TerminalColorsTest(unittest.TestCase):
    """The pane must not paint its own background over the terminal's theme."""

    def test_it_asks_curses_for_the_default_colours(self):
        import curses

        with patch.object(curses, "start_color") as start, \
             patch.object(curses, "use_default_colors") as default:
            use_terminal_colors()
        start.assert_called_once()
        default.assert_called_once()

    def test_a_terminal_without_colours_is_not_fatal(self):
        import curses

        with patch.object(curses, "start_color", side_effect=curses.error("no colour")):
            use_terminal_colors()


class FooterLinesTest(unittest.TestCase):
    """The footer wraps rather than truncating: a hidden key is a key that does
    not exist for the user."""

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
                self.assertLessEqual(len(line), width, f"width {width}: {line!r}")

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
