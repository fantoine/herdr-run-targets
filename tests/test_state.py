import contextlib
import io
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from run_targets.state import (
    ServiceRecord,
    TabRecord,
    load_state,
    prune_state,
    save_state,
    state_path,
)
from toggle import decide_toggle, live_service_pane


@contextlib.contextmanager
def state_dir():
    with tempfile.TemporaryDirectory() as directory:
        with patch.dict(os.environ, {"HERDR_PLUGIN_STATE_DIR": directory}, clear=False):
            yield directory


class StateRoundTripTest(unittest.TestCase):
    def test_an_absent_file_loads_as_empty(self):
        with state_dir():
            self.assertEqual(load_state(), {})

    def test_saving_then_loading_preserves_everything(self):
        with state_dir():
            state = {
                "w1:t1": TabRecord(
                    control_pane_id="w1:p1",
                    last_service_pane_id="w1:p3",
                    services={
                        "api": ServiceRecord(pane_id="w1:p2"),
                        "web": ServiceRecord(pane_id="w1:p3", stop_requested=True),
                    },
                )
            }
            save_state(state)
            loaded = load_state()
            self.assertEqual(loaded["w1:t1"].control_pane_id, "w1:p1")
            self.assertEqual(loaded["w1:t1"].last_service_pane_id, "w1:p3")
            self.assertEqual(loaded["w1:t1"].services["api"].pane_id, "w1:p2")
            self.assertFalse(loaded["w1:t1"].services["api"].stop_requested)
            self.assertTrue(loaded["w1:t1"].services["web"].stop_requested)

    def test_saving_leaves_no_temporary_file_behind(self):
        with state_dir() as directory:
            save_state({"w1:t1": TabRecord(None, None, {})})
            self.assertEqual(sorted(os.listdir(directory)), [os.path.basename(state_path())])

    def test_a_corrupt_file_loads_as_empty_with_a_warning(self):
        with state_dir():
            os.makedirs(os.path.dirname(state_path()), exist_ok=True)
            with open(state_path(), "w", encoding="utf-8") as handle:
                handle.write("{not json")
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                self.assertEqual(load_state(), {})
            self.assertIn("state", stderr.getvalue().lower())

    def test_a_non_object_payload_loads_as_empty(self):
        with state_dir():
            os.makedirs(os.path.dirname(state_path()), exist_ok=True)
            with open(state_path(), "w", encoding="utf-8") as handle:
                handle.write("[1, 2]")
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(load_state(), {})

    def test_malformed_entries_are_skipped_not_fatal(self):
        with state_dir():
            os.makedirs(os.path.dirname(state_path()), exist_ok=True)
            with open(state_path(), "w", encoding="utf-8") as handle:
                handle.write(
                    '{"w1:t1": "nonsense",'
                    ' "w1:t2": {"control_pane_id": "w1:p1", "services": {"a": {"pane_id": "w1:p2"}}}}'
                )
            with contextlib.redirect_stderr(io.StringIO()):
                loaded = load_state()
            self.assertNotIn("w1:t1", loaded)
            self.assertEqual(loaded["w1:t2"].services["a"].pane_id, "w1:p2")


class PruneStateTest(unittest.TestCase):
    def test_drops_tabs_that_no_longer_exist(self):
        state = {
            "w1:t1": TabRecord(None, None, {}),
            "w1:t2": TabRecord(None, None, {}),
        }
        self.assertEqual(sorted(prune_state(state, {"w1:t2"})), ["w1:t2"])

    def test_keeps_everything_when_all_tabs_are_live(self):
        state = {"w1:t1": TabRecord(None, None, {})}
        self.assertEqual(sorted(prune_state(state, {"w1:t1", "w1:t9"})), ["w1:t1"])

    def test_an_empty_live_set_empties_the_state(self):
        self.assertEqual(prune_state({"w1:t1": TabRecord(None, None, {})}, set()), {})


class DecideToggleTest(unittest.TestCase):
    def test_a_live_control_pane_is_closed(self):
        state = {"w1:t1": TabRecord(control_pane_id="w1:p1")}
        self.assertEqual(decide_toggle(state, {"w1:p1": {}}), ("close", "w1:p1"))

    def test_a_tab_with_services_but_no_control_pane_is_reopened(self):
        state = {"w1:t1": TabRecord(control_pane_id=None, services={"api": ServiceRecord("w1:p2")})}
        self.assertEqual(decide_toggle(state, {"w1:p2": {}}), ("reopen", "w1:t1"))

    def test_a_dead_control_pane_with_live_services_is_reopened(self):
        state = {
            "w1:t1": TabRecord(control_pane_id="w1:p1", services={"api": ServiceRecord("w1:p2")})
        }
        self.assertEqual(decide_toggle(state, {"w1:p2": {}}), ("reopen", "w1:t1"))

    def test_an_empty_state_creates(self):
        self.assertEqual(decide_toggle({}, {}), ("create", None))

    def test_a_tab_whose_panes_all_vanished_creates(self):
        state = {"w1:t1": TabRecord(control_pane_id="w1:p1", services={"api": ServiceRecord("w1:p2")})}
        self.assertEqual(decide_toggle(state, {}), ("create", None))


class LiveServicePaneTest(unittest.TestCase):
    def test_returns_the_live_service_pane(self):
        record = TabRecord(services={"api": ServiceRecord("w1:p2")})
        self.assertEqual(live_service_pane(record, {"w1:p2": {}}), "w1:p2")

    def test_skips_services_whose_pane_is_gone(self):
        record = TabRecord(
            services={"api": ServiceRecord("w1:p2"), "web": ServiceRecord("w1:p3")}
        )
        self.assertEqual(live_service_pane(record, {"w1:p3": {}}), "w1:p3")

    def test_returns_none_without_any_live_service(self):
        record = TabRecord(services={"api": ServiceRecord("w1:p2")})
        self.assertIsNone(live_service_pane(record, {}))

    def test_returns_none_without_any_service(self):
        self.assertIsNone(live_service_pane(TabRecord(), {"w1:p2": {}}))


class ToggleReopenArgsTest(unittest.TestCase):
    """La réouverture doit passer un pane source au split, sinon Herdr n'a rien à couper."""

    def test_the_reopen_call_carries_the_live_service_pane(self):
        import toggle as toggle_module

        captured = {}

        def fake_result(args):
            captured["args"] = list(args)
            return {}

        state = {"w1:t1": TabRecord(services={"api": ServiceRecord("w1:p2")})}
        panes = [{"pane_id": "w1:p2", "tab_id": "w1:t1"}]
        with patch.object(toggle_module.herdr, "list_panes", return_value=panes), \
             patch.object(toggle_module.herdr, "herdr_result", fake_result), \
             patch.object(toggle_module, "load_state", return_value=state), \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(toggle_module.main(), 0)
        self.assertIn("--target-pane", captured["args"])
        self.assertIn("w1:p2", captured["args"])
        self.assertIn("--placement", captured["args"])
        self.assertIn("split", captured["args"])


if __name__ == "__main__":
    unittest.main()
