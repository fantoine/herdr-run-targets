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


if __name__ == "__main__":
    unittest.main()
