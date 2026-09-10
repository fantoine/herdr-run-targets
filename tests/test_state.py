import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

import tomllib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from run_targets.settings import PLACEMENT_OVERLAY, PLACEMENT_POPUP, Settings
from run_targets.state import (
    ServiceRecord,
    WorkspaceRecord,
    load_state,
    prune_state,
    register_control_pane,
    save_state,
    state_path,
)
from toggle import current_workspace_id, decide_toggle, open_args


@contextlib.contextmanager
def state_dir():
    with tempfile.TemporaryDirectory() as directory, \
         patch.dict(os.environ, {"HERDR_PLUGIN_STATE_DIR": directory}, clear=False):
        yield directory


def service(tab_id="w1:t7", pane_id="w1:p7", stop_requested=False):
    return ServiceRecord(tab_id=tab_id, pane_id=pane_id, stop_requested=stop_requested)


def write_state(payload: str) -> None:
    os.makedirs(os.path.dirname(state_path()), exist_ok=True)
    with open(state_path(), "w", encoding="utf-8") as handle:
        handle.write(payload)


class StateRoundTripTest(unittest.TestCase):
    def test_an_absent_file_loads_as_empty(self):
        with state_dir():
            self.assertEqual(load_state(), {})

    def test_saving_then_loading_preserves_everything(self):
        with state_dir():
            state = {
                "w1": WorkspaceRecord(
                    control_pane_id="w1:p1",
                    services={
                        "api": service("w1:t7", "w1:p7"),
                        "web": service("w1:t8", "w1:p8", stop_requested=True),
                    },
                )
            }
            save_state(state)
            loaded = load_state()
            self.assertEqual(loaded["w1"].control_pane_id, "w1:p1")
            self.assertEqual(loaded["w1"].services["api"].tab_id, "w1:t7")
            self.assertEqual(loaded["w1"].services["api"].pane_id, "w1:p7")
            self.assertFalse(loaded["w1"].services["api"].stop_requested)
            self.assertTrue(loaded["w1"].services["web"].stop_requested)

    def test_saving_leaves_no_temporary_file_behind(self):
        with state_dir() as directory:
            save_state({"w1": WorkspaceRecord()})
            self.assertEqual(sorted(os.listdir(directory)), [os.path.basename(state_path())])

    def test_a_corrupt_file_loads_as_empty_with_a_warning(self):
        with state_dir():
            write_state("{not json")
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                self.assertEqual(load_state(), {})
            self.assertIn("state", stderr.getvalue().lower())

    def test_a_non_object_payload_loads_as_empty(self):
        with state_dir():
            write_state("[1, 2]")
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(load_state(), {})

    def test_malformed_entries_are_skipped_not_fatal(self):
        with state_dir():
            write_state(
                '{"w1": "nonsense",'
                ' "w2": {"control_pane_id": "w2:p1",'
                ' "services": {"a": {"tab_id": "w2:t7", "pane_id": "w2:p7"}}}}'
            )
            with contextlib.redirect_stderr(io.StringIO()):
                loaded = load_state()
            self.assertNotIn("w1", loaded)
            self.assertEqual(loaded["w2"].services["a"].pane_id, "w2:p7")


class LegacyJournalTest(unittest.TestCase):
    """A 0.1.0 journal is dropped, not migrated.

    Its keys are tab ids and its services carry no tab, so nothing in it can
    say which tab to close. Acting on the wrong tab costs more than forgetting
    a few panes.
    """

    def test_a_tab_keyed_entry_is_dropped_with_a_warning(self):
        with state_dir():
            write_state(
                '{"w1:t1": {"control_pane_id": "w1:p1", "last_service_pane_id": "w1:p3",'
                ' "services": {"api": {"pane_id": "w1:p3"}}}}'
            )
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                self.assertEqual(load_state(), {})
            self.assertIn("w1:t1", stderr.getvalue())

    def test_a_service_without_a_tab_id_drops_its_workspace(self):
        with state_dir():
            write_state('{"w1": {"services": {"api": {"pane_id": "w1:p3"}}}}')
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(load_state(), {})

    def test_a_sound_entry_beside_a_legacy_one_survives(self):
        with state_dir():
            write_state(
                '{"w1:t1": {"services": {"api": {"pane_id": "w1:p3"}}},'
                ' "w2": {"services": {"api": {"tab_id": "w2:t7", "pane_id": "w2:p7"}}}}'
            )
            with contextlib.redirect_stderr(io.StringIO()):
                loaded = load_state()
            self.assertEqual(sorted(loaded), ["w2"])


class PruneStateTest(unittest.TestCase):
    def test_drops_workspaces_that_no_longer_exist(self):
        state = {"w1": WorkspaceRecord(), "w2": WorkspaceRecord()}
        self.assertEqual(sorted(prune_state(state, {"w2"})), ["w2"])

    def test_keeps_everything_when_all_workspaces_are_live(self):
        self.assertEqual(sorted(prune_state({"w1": WorkspaceRecord()}, {"w1", "w9"})), ["w1"])

    def test_an_empty_live_set_empties_the_state(self):
        self.assertEqual(prune_state({"w1": WorkspaceRecord()}, set()), {})


class DecideToggleTest(unittest.TestCase):
    """Two branches: the dashboard owns no tab, so closing it leaves nothing
    orphaned and reopening it never has to find a tab again."""

    def test_a_live_dashboard_pane_is_closed(self):
        state = {"w1": WorkspaceRecord(control_pane_id="w1:p6")}
        self.assertEqual(decide_toggle(state, {"w1:p6": {}}, "w1"), ("close", "w1:p6"))

    def test_a_dead_dashboard_pane_creates(self):
        state = {"w1": WorkspaceRecord(control_pane_id="w1:p6")}
        self.assertEqual(decide_toggle(state, {}, "w1"), ("create", None))

    def test_running_services_do_not_keep_the_dashboard_from_closing(self):
        """Closing the dashboard never touches a service: its tab stays open."""
        state = {
            "w1": WorkspaceRecord(
                control_pane_id="w1:p6", services={"api": service("w1:t7", "w1:p7")}
            )
        }
        live = {"w1:p6": {}, "w1:p7": {}}
        self.assertEqual(decide_toggle(state, live, "w1"), ("close", "w1:p6"))

    def test_an_empty_state_creates(self):
        self.assertEqual(decide_toggle({}, {}, "w1"), ("create", None))

    def test_a_workspace_with_services_but_no_dashboard_creates(self):
        state = {"w1": WorkspaceRecord(services={"api": service("w1:t7", "w1:p7")})}
        self.assertEqual(decide_toggle(state, {"w1:p7": {}}, "w1"), ("create", None))


class DecideToggleScopeTest(unittest.TestCase):
    """The toggle must act on the current workspace only: without that scope it
    closed the dashboard of another worktree."""

    def test_ignores_a_dashboard_in_another_workspace(self):
        state = {"w2": WorkspaceRecord(control_pane_id="w2:p6")}
        self.assertEqual(decide_toggle(state, {"w2:p6": {}}, "w9"), ("create", None))

    def test_closes_the_dashboard_of_the_current_workspace(self):
        state = {"w2": WorkspaceRecord(control_pane_id="w2:p6")}
        self.assertEqual(decide_toggle(state, {"w2:p6": {}}, "w2"), ("close", "w2:p6"))

    def test_picks_the_current_workspace_among_several_tracked(self):
        state = {
            "w2": WorkspaceRecord(control_pane_id="w2:p6"),
            "w9": WorkspaceRecord(control_pane_id="w9:p6"),
        }
        live = {"w2:p6": {}, "w9:p6": {}}
        self.assertEqual(decide_toggle(state, live, "w9"), ("close", "w9:p6"))

    def test_an_unknown_workspace_creates_rather_than_guessing(self):
        """Without an id to match, closing would be a coin toss on someone
        else's dashboard; opening one more is recoverable."""
        state = {"w2": WorkspaceRecord(control_pane_id="w2:p6")}
        self.assertEqual(decide_toggle(state, {"w2:p6": {}}, None), ("create", None))


class OpenArgsTest(unittest.TestCase):
    """Herdr answers `invalid_params` if the pane is aimed: "overlay and popup
    plugin panes target the active pane"."""

    def test_the_default_placement_is_an_overlay(self):
        with patch.dict(os.environ, {}, clear=True):
            args = open_args(Settings(), None)
        self.assertIn("--placement", args)
        self.assertIn(PLACEMENT_OVERLAY, args)

    def test_neither_workspace_nor_target_pane_is_passed(self):
        with patch.dict(os.environ, {"HERDR_WORKSPACE_ID": "w7"}, clear=True):
            for settings in (Settings(), Settings(placement=PLACEMENT_POPUP)):
                args = open_args(settings, "w7")
                self.assertNotIn("--workspace", args)
                self.assertNotIn("--target-pane", args)

    def test_an_overlay_is_never_sized(self):
        """Herdr: "width and height are only supported when placement is popup"."""
        settings = Settings(popup_width="60%", popup_height="20")
        with patch.dict(os.environ, {}, clear=True):
            args = open_args(settings, "w7")
        self.assertNotIn("--width", args)
        self.assertNotIn("--height", args)

    def test_a_popup_carries_its_dimensions(self):
        settings = Settings(placement=PLACEMENT_POPUP, popup_width="60%", popup_height="20")
        with patch.dict(os.environ, {}, clear=True):
            args = open_args(settings, "w7")
        self.assertEqual(args[args.index("--width") + 1], "60%")
        self.assertEqual(args[args.index("--height") + 1], "20")

    def test_a_popup_without_dimensions_lets_herdr_choose(self):
        with patch.dict(os.environ, {}, clear=True):
            args = open_args(Settings(placement=PLACEMENT_POPUP), "w7")
        self.assertNotIn("--width", args)
        self.assertNotIn("--height", args)

    def test_a_popup_is_handed_the_workspace_it_belongs_to(self):
        """A popup belongs to no pane, so Herdr injects no HERDR_WORKSPACE_ID:
        without this the dashboard would not know whose tabs it manages."""
        with patch.dict(os.environ, {}, clear=True):
            args = open_args(Settings(placement=PLACEMENT_POPUP), "w7")
        self.assertIn("--env", args)
        self.assertIn("HERDR_WORKSPACE_ID=w7", args)

    def test_an_overlay_needs_no_injected_workspace(self):
        with patch.dict(os.environ, {}, clear=True):
            args = open_args(Settings(), "w7")
        self.assertNotIn("HERDR_WORKSPACE_ID=w7", args)

    def test_it_carries_the_workspace_cwd_from_the_action_context(self):
        context = '{"workspace_id": "w5", "workspace_cwd": "/private/tmp/run-targets-demo"}'
        with patch.dict(os.environ, {"HERDR_PLUGIN_CONTEXT_JSON": context}, clear=True):
            args = open_args(Settings(), "w5")
        self.assertIn("--cwd", args)
        self.assertIn("/private/tmp/run-targets-demo", args)

    def test_it_omits_cwd_without_the_context_variable(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertNotIn("--cwd", open_args(Settings(), None))

    def test_it_omits_cwd_with_invalid_json(self):
        with patch.dict(os.environ, {"HERDR_PLUGIN_CONTEXT_JSON": "not json"}, clear=True):
            self.assertNotIn("--cwd", open_args(Settings(), None))

    def test_it_omits_cwd_without_a_usable_workspace_cwd(self):
        with patch.dict(os.environ, {"HERDR_PLUGIN_CONTEXT_JSON": '{"workspace_id": "w5"}'}, clear=True):
            self.assertNotIn("--cwd", open_args(Settings(), "w5"))


class ToggleMainTest(unittest.TestCase):
    def _run(self, state, panes, environment):
        """`main` reads the real settings file, so the environment must point at
        an empty config directory -- otherwise the developer's own popup
        settings decide what this test sees."""
        import toggle as toggle_module

        calls = []
        directory = tempfile.mkdtemp()
        environment = {**environment, "HERDR_PLUGIN_CONFIG_DIR": directory}
        with patch.object(toggle_module.herdr, "list_panes", return_value=panes), \
             patch.object(
                 toggle_module.herdr, "pane_close",
                 lambda pane_id: calls.append(("pane_close", pane_id))), \
             patch.object(
                 toggle_module.herdr, "herdr_result",
                 lambda args: calls.append(("open", list(args))) or {}), \
             patch.object(toggle_module, "load_state", return_value=state), \
             patch.dict(os.environ, environment, clear=True), \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(toggle_module.main(), 0)
        return calls

    def test_a_live_dashboard_loses_only_its_pane(self):
        state = {
            "w1": WorkspaceRecord(
                control_pane_id="w1:p6", services={"api": service("w1:t7", "w1:p7")}
            )
        }
        panes = [
            {"pane_id": "w1:p6", "workspace_id": "w1"},
            {"pane_id": "w1:p7", "workspace_id": "w1"},
        ]
        calls = self._run(state, panes, {"HERDR_WORKSPACE_ID": "w1"})
        self.assertEqual(calls, [("pane_close", "w1:p6")])

    def test_nothing_tracked_opens_the_overlay(self):
        calls = self._run({}, [], {"HERDR_WORKSPACE_ID": "w1"})
        self.assertEqual(len(calls), 1)
        kind, args = calls[0]
        self.assertEqual(kind, "open")
        self.assertIn("overlay", args)


class DashboardStartupTest(unittest.TestCase):
    """The dashboard pane must never die on a raw traceback: Herdr tears it down
    at once and the user reads nothing."""

    def test_a_missing_git_is_reported_instead_of_raising(self):
        import dashboard as dashboard_module

        environment = {"HERDR_WORKSPACE_ID": "w1", "HERDR_PANE_ID": "w1:p6"}
        stderr = io.StringIO()
        with patch.dict(os.environ, environment, clear=False), \
             patch("run_targets.config.subprocess.run", side_effect=FileNotFoundError("git")), \
             contextlib.redirect_stderr(stderr):
            self.assertEqual(dashboard_module.main(), 1)
        self.assertIn("is not inside a git repository", stderr.getvalue())

    def test_a_missing_workspace_id_is_reported(self):
        """The dashboard keys its journal by workspace, so it cannot start
        without knowing which one it sits in."""
        import dashboard as dashboard_module

        stderr = io.StringIO()
        with patch.dict(os.environ, {"HERDR_PANE_ID": "w1:p6"}, clear=True), \
             contextlib.redirect_stderr(stderr):
            self.assertEqual(dashboard_module.main(), 1)
        self.assertIn("workspace", stderr.getvalue())

    def test_a_missing_pane_id_is_not_fatal(self):
        """A popup belongs to no pane, so Herdr injects no HERDR_PANE_ID. Only
        the toggle's close branch depends on it."""
        import dashboard as dashboard_module

        stderr = io.StringIO()
        with patch.dict(os.environ, {"HERDR_WORKSPACE_ID": "w1"}, clear=True), \
             patch("run_targets.config.subprocess.run", side_effect=FileNotFoundError("git")), \
             contextlib.redirect_stderr(stderr):
            self.assertEqual(dashboard_module.main(), 1)
        # It got past the environment check and failed on the repository, which
        # is the next test in line.
        self.assertIn("is not inside a git repository", stderr.getvalue())


MANIFEST_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "herdr-plugin.toml"
)


class ManifestTest(unittest.TestCase):
    def test_no_command_references_a_bare_script_filename(self):
        """A script referenced by name alone depends on the process's current
        directory -- exactly what killed the pane once `--cwd` pointed at the
        user's repository rather than the plugin's."""
        with open(MANIFEST_PATH, "rb") as handle:
            manifest = tomllib.load(handle)
        entries = manifest.get("actions", []) + manifest.get("panes", [])
        self.assertTrue(entries, "manifest has no actions or panes to check")
        for entry in entries:
            for token in entry.get("command", []):
                if token.endswith((".py", ".sh")):
                    self.assertTrue(
                        token.startswith("/") or "$HERDR_PLUGIN_ROOT" in token,
                        f"{entry.get('id')!r} command token {token!r} is a bare script path",
                    )

    def test_the_dashboard_pane_is_declared_as_an_overlay(self):
        with open(MANIFEST_PATH, "rb") as handle:
            manifest = tomllib.load(handle)
        panes = {pane["id"]: pane for pane in manifest.get("panes", [])}
        self.assertEqual(panes["dashboard"]["placement"], "overlay")


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

    def test_registering_keeps_the_other_workspaces(self):
        with state_dir():
            save_state({"w1": WorkspaceRecord(control_pane_id="w1:p6")})
            register_control_pane("w2", "w2:p6")
            loaded = load_state()
            self.assertEqual(sorted(loaded), ["w1", "w2"])
            self.assertEqual(loaded["w1"].control_pane_id, "w1:p6")
            self.assertEqual(loaded["w2"].control_pane_id, "w2:p6")

    def test_registering_preserves_the_services_of_its_own_workspace(self):
        with state_dir():
            save_state(
                {"w1": WorkspaceRecord(
                    control_pane_id="w1:pOld", services={"api": service("w1:t7", "w1:p7")}
                )}
            )
            register_control_pane("w1", "w1:pNew")
            record = load_state()["w1"]
            self.assertEqual(record.control_pane_id, "w1:pNew")
            self.assertEqual(record.services["api"].pane_id, "w1:p7")

    def test_registering_into_an_empty_journal_creates_the_entry(self):
        with state_dir():
            register_control_pane("w1", "w1:p6")
            self.assertEqual(load_state()["w1"].control_pane_id, "w1:p6")


if __name__ == "__main__":
    unittest.main()
