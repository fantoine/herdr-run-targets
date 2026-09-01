import contextlib
import io
import json
import os
import sys
import tempfile
import tomllib
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from run_targets.state import (
    register_control_pane,
    ServiceRecord,
    TabRecord,
    load_state,
    prune_state,
    save_state,
    state_path,
)
from toggle import current_workspace_id, decide_toggle, tab_workspace_id, live_service_pane


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
    def test_a_live_control_pane_with_services_closes_only_the_pane(self):
        state = {
            "w1:t1": TabRecord(
                control_pane_id="w1:p1", services={"api": ServiceRecord("w1:p2")}
            )
        }
        self.assertEqual(
            decide_toggle(state, {"w1:p1": {}, "w1:p2": {}}), ("close", "w1:p1")
        )

    def test_a_tab_without_any_live_service_is_closed_whole(self):
        """Closing the only pane would leave a tab that neither "reopen" nor
        "close" would recognise: the next toggle would create another."""
        state = {"w1:t1": TabRecord(control_pane_id="w1:p1")}
        self.assertEqual(decide_toggle(state, {"w1:p1": {}}), ("close_tab", "w1:t1"))

    def test_a_tab_whose_services_all_died_is_closed_whole(self):
        state = {
            "w1:t1": TabRecord(
                control_pane_id="w1:p1", services={"api": ServiceRecord("w1:p2")}
            )
        }
        self.assertEqual(decide_toggle(state, {"w1:p1": {}}), ("close_tab", "w1:t1"))

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


class ToggleCloseTest(unittest.TestCase):
    """What the toggle created, it takes away -- the tab included."""

    def _run(self, state, panes):
        import toggle as toggle_module

        calls = []
        with patch.object(toggle_module.herdr, "list_panes", return_value=panes), \
             patch.object(
                 toggle_module.herdr, "pane_close",
                 lambda pane_id: calls.append(("pane", pane_id))), \
             patch.object(
                 toggle_module.herdr, "tab_close",
                 lambda tab_id: calls.append(("tab", tab_id))), \
             patch.object(toggle_module, "load_state", return_value=state), \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(toggle_module.main(), 0)
        return calls

    def test_a_tab_with_services_only_loses_its_dashboard_pane(self):
        state = {
            "w1:t1": TabRecord(
                control_pane_id="w1:p1", services={"api": ServiceRecord("w1:p2")}
            )
        }
        panes = [
            {"pane_id": "w1:p1", "tab_id": "w1:t1"},
            {"pane_id": "w1:p2", "tab_id": "w1:t1"},
        ]
        with state_dir():
            self.assertEqual(self._run(state, panes), [("pane", "w1:p1")])
            self.assertIn("w1:t1", state)

    def test_a_tab_without_services_is_closed_and_forgotten(self):
        state = {"w1:t1": TabRecord(control_pane_id="w1:p1")}
        panes = [{"pane_id": "w1:p1", "tab_id": "w1:t1"}]
        with state_dir():
            self.assertEqual(self._run(state, panes), [("tab", "w1:t1")])
            self.assertNotIn("w1:t1", state)
            self.assertEqual(load_state(), {})


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
    """Reopening must pass a source pane to the split, or Herdr has nothing to cut."""

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

    def test_the_reopen_call_carries_the_target_panes_cwd(self):
        import toggle as toggle_module

        captured = {}

        def fake_result(args):
            captured["args"] = list(args)
            return {}

        state = {"w1:t1": TabRecord(services={"api": ServiceRecord("w1:p2")})}
        panes = [
            {"pane_id": "w1:p2", "tab_id": "w1:t1", "cwd": "/private/tmp/run-targets-demo"}
        ]
        with patch.object(toggle_module.herdr, "list_panes", return_value=panes), \
             patch.object(toggle_module.herdr, "herdr_result", fake_result), \
             patch.object(toggle_module, "load_state", return_value=state), \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(toggle_module.main(), 0)
        self.assertIn("--cwd", captured["args"])
        self.assertIn("/private/tmp/run-targets-demo", captured["args"])


class ToggleCreateCwdTest(unittest.TestCase):
    """Creation has no pane to inherit from: the directory comes from the action context."""

    def _run_create(self):
        import toggle as toggle_module

        captured = {}

        def fake_result(args):
            captured["args"] = list(args)
            return {}

        with patch.object(toggle_module.herdr, "list_panes", return_value=[]), \
             patch.object(toggle_module.herdr, "herdr_result", fake_result), \
             patch.object(toggle_module, "load_state", return_value={}), \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(toggle_module.main(), 0)
        return captured["args"]

    def test_carries_the_workspace_cwd_from_the_action_context(self):
        context = '{"workspace_id": "w5", "workspace_cwd": "/private/tmp/run-targets-demo"}'
        with patch.dict(os.environ, {"HERDR_PLUGIN_CONTEXT_JSON": context}, clear=False):
            args = self._run_create()
        self.assertIn("--cwd", args)
        self.assertIn("/private/tmp/run-targets-demo", args)

    def test_omits_cwd_without_the_context_variable(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("HERDR_PLUGIN_CONTEXT_JSON", None)
            args = self._run_create()
        self.assertNotIn("--cwd", args)

    def test_omits_cwd_with_invalid_json(self):
        with patch.dict(os.environ, {"HERDR_PLUGIN_CONTEXT_JSON": "not json"}, clear=False):
            args = self._run_create()
        self.assertNotIn("--cwd", args)

    def test_omits_cwd_without_a_usable_workspace_cwd(self):
        context = '{"workspace_id": "w5"}'
        with patch.dict(os.environ, {"HERDR_PLUGIN_CONTEXT_JSON": context}, clear=False):
            args = self._run_create()
        self.assertNotIn("--cwd", args)


class DashboardStartupTest(unittest.TestCase):
    """The dashboard pane must never die on a raw traceback:
    Herdr tears it down at once and the user reads nothing."""

    def test_a_missing_git_is_reported_instead_of_raising(self):
        import dashboard as dashboard_module

        environment = {"HERDR_TAB_ID": "w1:t1", "HERDR_PANE_ID": "w1:p1"}
        stderr = io.StringIO()
        with patch.dict(os.environ, environment, clear=False), \
             patch("run_targets.config.subprocess.run", side_effect=FileNotFoundError("git")), \
             contextlib.redirect_stderr(stderr):
            self.assertEqual(dashboard_module.main(), 1)
        self.assertIn("is not inside a git repository", stderr.getvalue())


MANIFEST_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "herdr-plugin.toml"
)


class ManifestCommandsTest(unittest.TestCase):
    """A script referenced by name alone depends on the process's current
    directory -- exactly what killed the pane once `--cwd` pointed at the
    user's repository rather than the plugin's. Every token that looks like a
    script must therefore be absolute or go through $HERDR_PLUGIN_ROOT.
    """

    def test_no_command_references_a_bare_script_filename(self):
        with open(MANIFEST_PATH, "rb") as handle:
            manifest = tomllib.load(handle)
        entries = manifest.get("actions", []) + manifest.get("panes", [])
        self.assertTrue(entries, "manifest has no actions or panes to check")
        for entry in entries:
            command = entry.get("command", [])
            for token in command:
                if token.endswith(".py") or token.endswith(".sh"):
                    self.assertTrue(
                        token.startswith("/") or "$HERDR_PLUGIN_ROOT" in token,
                        f"{entry.get('id')!r} command token {token!r} is a bare script path",
                    )


class TabLabelTest(unittest.TestCase):
    """The tab is only named when the plugin created it itself."""

    def test_the_create_call_marks_the_tab_as_ours(self):
        import toggle as toggle_module

        captured = {}

        def fake_result(args):
            captured["args"] = list(args)
            return {}

        with patch.object(toggle_module.herdr, "list_panes", return_value=[]), \
             patch.object(toggle_module.herdr, "herdr_result", fake_result), \
             patch.object(toggle_module, "load_state", return_value={}), \
             patch.dict(os.environ, {}, clear=True):
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(toggle_module.main(), 0)
        self.assertIn("--env", captured["args"])
        self.assertIn(f"{toggle_module.TAB_OWNED_ENV}=1", captured["args"])

    def test_the_reopen_call_does_not_mark_the_tab(self):
        import toggle as toggle_module

        captured = {}

        def fake_result(args):
            captured["args"] = list(args)
            return {}

        state = {"w1:t1": TabRecord(services={"api": ServiceRecord("w1:p2")})}
        panes = [{"pane_id": "w1:p2", "tab_id": "w1:t1", "cwd": "/repo"}]
        with patch.object(toggle_module.herdr, "list_panes", return_value=panes), \
             patch.object(toggle_module.herdr, "herdr_result", fake_result), \
             patch.object(toggle_module, "load_state", return_value=state), \
             patch.dict(os.environ, {}, clear=True):
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(toggle_module.main(), 0)
        self.assertNotIn(f"{toggle_module.TAB_OWNED_ENV}=1", captured["args"])


def pane(pane_id, workspace_id, tab_id="w1:t1"):
    return {"pane_id": pane_id, "workspace_id": workspace_id, "tab_id": tab_id}


class TabWorkspaceIdTest(unittest.TestCase):
    """A tab's workspace is inferred from its live panes: no field to add to the
    journal, and existing journals stay readable."""

    def test_reads_it_from_the_control_pane(self):
        record = TabRecord(control_pane_id="w2:p1")
        self.assertEqual(tab_workspace_id(record, {"w2:p1": pane("w2:p1", "w2")}), "w2")

    def test_falls_back_to_a_service_pane(self):
        record = TabRecord(control_pane_id="w2:p9", services={"api": ServiceRecord("w2:p3")})
        live = {"w2:p3": pane("w2:p3", "w2")}
        self.assertEqual(tab_workspace_id(record, live), "w2")

    def test_returns_none_when_nothing_of_ours_is_alive(self):
        record = TabRecord(control_pane_id="w2:p1")
        self.assertIsNone(tab_workspace_id(record, {}))


class DecideToggleScopeTest(unittest.TestCase):
    """The toggle must act on the current workspace only: without this filter it
    closes the dashboard of another worktree."""

    def test_ignores_a_dashboard_in_another_workspace(self):
        state = {"w2:t1": TabRecord(control_pane_id="w2:p1")}
        live = {"w2:p1": pane("w2:p1", "w2", "w2:t1")}
        self.assertEqual(decide_toggle(state, live, "w9"), ("create", None))

    def test_closes_the_dashboard_of_the_current_workspace(self):
        state = {"w2:t1": TabRecord(control_pane_id="w2:p1")}
        live = {"w2:p1": pane("w2:p1", "w2", "w2:t1")}
        self.assertEqual(decide_toggle(state, live, "w2"), ("close_tab", "w2:t1"))

    def test_picks_the_current_workspace_among_several_tracked(self):
        state = {
            "w2:t1": TabRecord(control_pane_id="w2:p1"),
            "w9:t1": TabRecord(control_pane_id="w9:p1"),
        }
        live = {
            "w2:p1": pane("w2:p1", "w2", "w2:t1"),
            "w9:p1": pane("w9:p1", "w9", "w9:t1"),
        }
        self.assertEqual(decide_toggle(state, live, "w9"), ("close_tab", "w9:t1"))

    def test_reopen_is_scoped_too(self):
        state = {"w2:t1": TabRecord(services={"api": ServiceRecord("w2:p3")})}
        live = {"w2:p3": pane("w2:p3", "w2", "w2:t1")}
        self.assertEqual(decide_toggle(state, live, "w9"), ("create", None))
        self.assertEqual(decide_toggle(state, live, "w2"), ("reopen", "w2:t1"))

    def test_an_unknown_workspace_falls_back_to_the_old_behaviour(self):
        """Acting on a tracked tab beats doing nothing at all."""
        state = {"w2:t1": TabRecord(control_pane_id="w2:p1")}
        live = {"w2:p1": pane("w2:p1", "w2", "w2:t1")}
        self.assertEqual(decide_toggle(state, live, None), ("close_tab", "w2:t1"))


class CurrentWorkspaceIdTest(unittest.TestCase):
    def test_prefers_the_environment_variable(self):
        with patch.dict(os.environ, {"HERDR_WORKSPACE_ID": "w7"}, clear=True):
            self.assertEqual(current_workspace_id(), "w7")

    def test_falls_back_to_the_action_context(self):
        payload = json.dumps({"workspace_id": "w8"})
        with patch.dict(os.environ, {"HERDR_PLUGIN_CONTEXT_JSON": payload}, clear=True):
            self.assertEqual(current_workspace_id(), "w8")

    def test_returns_none_without_either(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(current_workspace_id())

    def test_invalid_context_json_is_not_fatal(self):
        with patch.dict(os.environ, {"HERDR_PLUGIN_CONTEXT_JSON": "{broken"}, clear=True):
            self.assertIsNone(current_workspace_id())


class RegisterControlPaneTest(unittest.TestCase):
    """Two dashboards starting together must not erase each other.

    Each read, modified, then rewrote the whole journal; the second one
    overwrote the first one's entry. Seen for real: a workspace's entry
    vanished, and its toggle recognised nothing any more.
    """

    def test_registering_keeps_the_other_tabs(self):
        with state_dir():
            save_state({"w1:t1": TabRecord(control_pane_id="w1:p1")})
            register_control_pane("w2:t1", "w2:p1")
            loaded = load_state()
            self.assertEqual(sorted(loaded), ["w1:t1", "w2:t1"])
            self.assertEqual(loaded["w1:t1"].control_pane_id, "w1:p1")
            self.assertEqual(loaded["w2:t1"].control_pane_id, "w2:p1")

    def test_registering_preserves_the_services_of_its_own_tab(self):
        with state_dir():
            save_state({"w1:t1": TabRecord(control_pane_id="w1:pOld",
                                           services={"api": ServiceRecord("w1:p9")})})
            register_control_pane("w1:t1", "w1:pNew")
            record = load_state()["w1:t1"]
            self.assertEqual(record.control_pane_id, "w1:pNew")
            self.assertEqual(record.services["api"].pane_id, "w1:p9")

    def test_registering_into_an_empty_journal_creates_the_entry(self):
        with state_dir():
            register_control_pane("w1:t1", "w1:p1")
            self.assertEqual(load_state()["w1:t1"].control_pane_id, "w1:p1")


class ToggleCreateWorkspaceTest(unittest.TestCase):
    """The created tab must be born in the workspace it was invoked from, not in
    the focused one."""

    def test_the_create_call_carries_the_workspace(self):
        import toggle as toggle_module

        captured = {}

        def fake_result(args):
            captured["args"] = list(args)
            return {}

        panes = [{"pane_id": "w7:p1", "workspace_id": "w7", "tab_id": "w7:t1"}]
        with patch.object(toggle_module.herdr, "list_panes", return_value=panes), \
             patch.object(toggle_module.herdr, "herdr_result", fake_result), \
             patch.object(toggle_module, "load_state", return_value={}), \
             patch.dict(os.environ, {"HERDR_WORKSPACE_ID": "w7"}, clear=True):
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(toggle_module.main(), 0)
        self.assertIn("--workspace", captured["args"])
        self.assertIn("w7", captured["args"])

    def test_no_workspace_flag_when_the_workspace_no_longer_exists(self):
        """Closing a workspace's last tab closes the workspace: the current id
        can therefore be stale by the time we recreate."""
        import toggle as toggle_module

        captured = {}

        def fake_result(args):
            captured["args"] = list(args)
            return {}

        # No live pane in w7: the workspace is gone.
        panes = [{"pane_id": "w9:p1", "workspace_id": "w9", "tab_id": "w9:t1"}]
        with patch.object(toggle_module.herdr, "list_panes", return_value=panes), \
             patch.object(toggle_module.herdr, "herdr_result", fake_result), \
             patch.object(toggle_module, "load_state", return_value={}), \
             patch.dict(os.environ, {"HERDR_WORKSPACE_ID": "w7"}, clear=True):
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(toggle_module.main(), 0)
        self.assertNotIn("--workspace", captured["args"])

    def test_no_workspace_flag_when_the_workspace_is_unknown(self):
        import toggle as toggle_module

        captured = {}

        def fake_result(args):
            captured["args"] = list(args)
            return {}

        with patch.object(toggle_module.herdr, "list_panes", return_value=[]), \
             patch.object(toggle_module.herdr, "herdr_result", fake_result), \
             patch.object(toggle_module, "load_state", return_value={}), \
             patch.dict(os.environ, {}, clear=True):
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(toggle_module.main(), 0)
        self.assertNotIn("--workspace", captured["args"])


if __name__ == "__main__":
    unittest.main()
