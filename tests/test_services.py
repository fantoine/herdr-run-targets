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
    derive_state,
    next_split_target,
    plan_action,
    resolve_selection,
    skip_message,
)
from run_targets.state import ServiceRecord, TabRecord


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


if __name__ == "__main__":
    unittest.main()
