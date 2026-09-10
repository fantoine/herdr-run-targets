import json
import os
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from run_targets.herdr import (
    describe_herdr_failure,
    has_foreground_command,
    herdr_bin,
    herdr_call,
    live_pane_ids,
    tab_create,
    tab_create_args,
    tab_focus,
)


class HerdrBinTest(unittest.TestCase):
    def test_prefers_the_injected_binary(self):
        with patch.dict(os.environ, {"HERDR_BIN_PATH": "/opt/herdr"}, clear=True):
            self.assertEqual(herdr_bin(), "/opt/herdr")

    def test_falls_back_to_the_path(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(herdr_bin(), "herdr")


class DescribeHerdrFailureTest(unittest.TestCase):
    def test_prefers_herdrs_own_error_message(self):
        stdout = json.dumps(
            {"error": {"code": "pane_not_found", "message": "pane w9:p9 not found"}}
        )
        message = describe_herdr_failure(["pane", "get"], 1, stdout, "")
        self.assertIn("pane w9:p9 not found", message)

    def test_falls_back_to_stderr(self):
        message = describe_herdr_failure(["pane", "get"], 1, "not json", "boom")
        self.assertIn("boom", message)

    def test_names_the_command_and_code_when_nothing_else_is_available(self):
        message = describe_herdr_failure(["pane", "get"], 3, "", "")
        self.assertIn("pane get", message)
        self.assertIn("3", message)

    def test_json_without_an_error_key_falls_through(self):
        message = describe_herdr_failure(["pane", "get"], 1, json.dumps({"ok": 1}), "")
        self.assertIn("pane get", message)

    def test_a_non_string_error_message_falls_through(self):
        stdout = json.dumps({"error": {"message": 42}})
        message = describe_herdr_failure(["pane", "get"], 1, stdout, "")
        self.assertNotIn("42", message)
        self.assertIn("pane get", message)


class TabCreateArgsTest(unittest.TestCase):
    """A tab creation's command line, without running Herdr."""

    def test_minimal_creation(self):
        self.assertEqual(
            tab_create_args("w1", "api", None, None),
            ["tab", "create", "--workspace", "w1", "--label", "api", "--no-focus"],
        )

    def test_cwd_and_env_are_appended(self):
        args = tab_create_args("w1", "run:web", "/repo/apps/web", {"PORT": "3000"})
        self.assertEqual(
            args,
            [
                "tab", "create",
                "--workspace", "w1",
                "--label", "run:web",
                "--no-focus",
                "--cwd", "/repo/apps/web",
                "--env", "PORT=3000",
            ],
        )

    def test_every_env_pair_gets_its_own_flag(self):
        args = tab_create_args("w1", "api", None, {"A": "1", "B": "2"})
        self.assertEqual(args.count("--env"), 2)

    def test_the_tab_is_created_without_stealing_the_focus(self):
        """Focus is a separate, configurable step, applied once the batch is up."""
        self.assertIn("--no-focus", tab_create_args("w1", "api", None, None))


class HasForegroundCommandTest(unittest.TestCase):
    def test_true_when_a_process_other_than_the_shell_runs(self):
        info = {"shell_pid": 42, "foreground_processes": [{"pid": 77, "name": "node"}]}
        self.assertTrue(has_foreground_command(info))

    def test_false_when_only_the_shell_is_in_the_foreground(self):
        info = {"shell_pid": 42, "foreground_processes": [{"pid": 42, "name": "zsh"}]}
        self.assertFalse(has_foreground_command(info))

    def test_false_on_incomplete_payloads(self):
        self.assertFalse(has_foreground_command({}))
        self.assertFalse(has_foreground_command({"shell_pid": 42}))
        self.assertFalse(has_foreground_command({"shell_pid": 42, "foreground_processes": []}))
        self.assertFalse(has_foreground_command({"foreground_processes": [{"pid": 7}]}))

    def test_false_when_a_process_carries_no_pid(self):
        info = {"shell_pid": 42, "foreground_processes": [{"name": "node"}]}
        self.assertFalse(has_foreground_command(info))


class HerdrCallTest(unittest.TestCase):
    def test_an_unreachable_binary_raises_runtime_error_not_oserror(self):
        with patch.dict(os.environ, {"HERDR_BIN_PATH": "/nonexistent/herdr"}, clear=True):
            with self.assertRaises(RuntimeError):
                herdr_call(["pane", "list"])

    def test_empty_stdout_on_success_is_not_a_failure(self):
        completed = subprocess.CompletedProcess(args=[], returncode=0, stdout=b"", stderr=b"")
        with patch("run_targets.herdr.subprocess.run", return_value=completed):
            self.assertEqual(herdr_call(["pane", "run", "w1:p1", "x"]), "")


class TabCreateTest(unittest.TestCase):
    RESULT = {"tab": {"tab_id": "w1:t7"}, "root_pane": {"pane_id": "w1:p7"}}

    def test_returns_both_ids_from_the_response(self):
        with patch("run_targets.herdr.herdr_result", return_value=self.RESULT):
            self.assertEqual(tab_create("w1", "api"), ("w1:t7", "w1:p7"))

    def test_the_ids_are_read_never_derived(self):
        """`tab create` answered w10:t2 / w10:p2 on Herdr 0.8.2, but nothing
        promises a pane id follows its tab's number."""
        payload = {"tab": {"tab_id": "w1:t7"}, "root_pane": {"pane_id": "w9:p42"}}
        with patch("run_targets.herdr.herdr_result", return_value=payload):
            self.assertEqual(tab_create("w1", "api"), ("w1:t7", "w9:p42"))

    def test_raises_when_the_tab_id_is_missing_or_unusable(self):
        for payload in (
            {},
            {"tab": {}, "root_pane": {"pane_id": "w1:p7"}},
            {"tab": {"tab_id": ""}, "root_pane": {"pane_id": "w1:p7"}},
            {"tab": "w1:t7", "root_pane": {"pane_id": "w1:p7"}},
        ):
            with patch("run_targets.herdr.herdr_result", return_value=payload):
                with self.assertRaises(RuntimeError):
                    tab_create("w1", "api")

    def test_raises_when_the_root_pane_id_is_missing_or_unusable(self):
        for payload in (
            {"tab": {"tab_id": "w1:t7"}},
            {"tab": {"tab_id": "w1:t7"}, "root_pane": {}},
            {"tab": {"tab_id": "w1:t7"}, "root_pane": {"pane_id": 7}},
        ):
            with patch("run_targets.herdr.herdr_result", return_value=payload):
                with self.assertRaises(RuntimeError):
                    tab_create("w1", "api")


class TabFocusTest(unittest.TestCase):
    def test_it_goes_through_herdr_call_with_no_json_contract(self):
        with patch("run_targets.herdr.herdr_call", return_value="") as call:
            tab_focus("w1:t7")
        self.assertEqual(call.call_args.args[0], ["tab", "focus", "w1:t7"])


class LivePaneIdsTest(unittest.TestCase):
    """Global, not per tab: each service now lives in a tab of its own."""

    def test_collects_panes_from_every_tab(self):
        panes = [
            {"pane_id": "w1:p1", "tab_id": "w1:t1"},
            {"pane_id": "w1:p2", "tab_id": "w1:t2"},
        ]
        with patch("run_targets.herdr.list_panes", return_value=panes):
            self.assertEqual(live_pane_ids(), {"w1:p1", "w1:p2"})

    def test_entries_without_a_usable_pane_id_are_dropped(self):
        panes = [{"tab_id": "w1:t1"}, {"pane_id": 7}, {"pane_id": "w1:p1"}]
        with patch("run_targets.herdr.list_panes", return_value=panes):
            self.assertEqual(live_pane_ids(), {"w1:p1"})


if __name__ == "__main__":
    unittest.main()
