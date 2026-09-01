import json
import os
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from run_targets.herdr import (
    pane_rename,
    tab_rename,
    describe_herdr_failure,
    has_foreground_command,
    herdr_bin,
    herdr_call,
    pane_split,
    panes_in_tab,
    split_args,
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


class SplitArgsTest(unittest.TestCase):
    """La ligne de commande d'un split, sans lancer Herdr."""

    def test_minimal_split(self):
        self.assertEqual(
            split_args("w1:p1", "down", None, None, None),
            ["pane", "split", "w1:p1", "--direction", "down", "--no-focus"],
        )

    def test_ratio_cwd_and_env_are_appended(self):
        args = split_args("w1:p1", "right", 0.25, "/repo", {"PORT": "3000"})
        self.assertEqual(
            args,
            [
                "pane", "split", "w1:p1",
                "--direction", "right",
                "--no-focus",
                "--ratio", "0.25",
                "--cwd", "/repo",
                "--env", "PORT=3000",
            ],
        )

    def test_every_env_pair_gets_its_own_flag(self):
        args = split_args("w1:p1", "down", None, None, {"A": "1", "B": "2"})
        self.assertEqual(args.count("--env"), 2)


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


class PaneSplitTest(unittest.TestCase):
    def test_returns_the_id_from_the_response(self):
        with patch("run_targets.herdr.herdr_result", return_value={"pane": {"pane_id": "w4:p7"}}):
            self.assertEqual(pane_split("w4:p5", "right"), "w4:p7")

    def test_the_returned_id_is_not_derived_from_the_source(self):
        """Splitter w4:p5 rend w4:p7 sur Herdr 0.8.2 : les ids ne se suivent pas."""
        with patch("run_targets.herdr.herdr_result", return_value={"pane": {"pane_id": "w9:p42"}}):
            self.assertEqual(pane_split("w4:p5", "down"), "w9:p42")

    def test_raises_when_the_response_carries_no_pane(self):
        with patch("run_targets.herdr.herdr_result", return_value={}):
            with self.assertRaises(RuntimeError):
                pane_split("w4:p5", "right")

    def test_raises_when_the_pane_id_is_missing_or_not_a_string(self):
        for payload in ({"pane": {}}, {"pane": {"pane_id": 7}}, {"pane": {"pane_id": ""}}):
            with patch("run_targets.herdr.herdr_result", return_value=payload):
                with self.assertRaises(RuntimeError):
                    pane_split("w4:p5", "right")

    def test_raises_when_pane_is_not_an_object(self):
        with patch("run_targets.herdr.herdr_result", return_value={"pane": "w4:p7"}):
            with self.assertRaises(RuntimeError):
                pane_split("w4:p5", "right")


class PanesInTabTest(unittest.TestCase):
    def test_keeps_only_the_panes_of_that_tab(self):
        panes = [
            {"pane_id": "w1:p1", "tab_id": "w1:t1"},
            {"pane_id": "w1:p2", "tab_id": "w1:t2"},
        ]
        with patch("run_targets.herdr.list_panes", return_value=panes):
            self.assertEqual(sorted(panes_in_tab("w1:t1")), ["w1:p1"])

    def test_entries_without_a_usable_pane_id_are_dropped(self):
        panes = [
            {"tab_id": "w1:t1"},
            {"pane_id": 7, "tab_id": "w1:t1"},
            {"pane_id": "w1:p1", "tab_id": "w1:t1"},
        ]
        with patch("run_targets.herdr.list_panes", return_value=panes):
            self.assertEqual(sorted(panes_in_tab("w1:t1")), ["w1:p1"])


class RenameArgsTest(unittest.TestCase):
    """Les deux renommages passent par `herdr_call`, sans contrat JSON."""

    def test_pane_rename_sends_the_label(self):
        with patch("run_targets.herdr.herdr_call", return_value="") as call:
            pane_rename("w1:p2", "api")
        self.assertEqual(call.call_args.args[0], ["pane", "rename", "w1:p2", "api"])

    def test_tab_rename_sends_the_label(self):
        with patch("run_targets.herdr.herdr_call", return_value="") as call:
            tab_rename("w1:t1", "run")
        self.assertEqual(call.call_args.args[0], ["tab", "rename", "w1:t1", "run"])


if __name__ == "__main__":
    unittest.main()
