import os
import subprocess
import sys
import tempfile
import unittest
import unittest.mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from run_targets.config import (
    LOCAL_CONFIG_FILE,
    ORIGIN_LOCAL,
    ORIGIN_TEAM,
    TEAM_CONFIG_FILE,
    Target,
    is_safe_cwd,
    load_run_config,
    merge_run_configs,
    parse_run_config,
    resolve_repo_root,
)

TEAM = """
[[target]]
name = "api"
command = "yarn nx serve api"

[[target]]
name = "web"
command = "yarn nx serve web"
cwd = "apps/web"
env = { PORT = "3000" }
"""


def write(path: str, content: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(content)


class ParseRunConfigTest(unittest.TestCase):
    def test_reads_every_field(self):
        targets, warnings = parse_run_config(TEAM, ORIGIN_TEAM, TEAM_CONFIG_FILE)
        self.assertEqual(warnings, [])
        self.assertEqual([t.name for t in targets], ["api", "web"])
        self.assertEqual(targets[1].command, "yarn nx serve web")
        self.assertEqual(targets[1].cwd, "apps/web")
        self.assertEqual(targets[1].env, {"PORT": "3000"})
        self.assertEqual(targets[1].origin, ORIGIN_TEAM)

    def test_defaults_when_optional_fields_are_absent(self):
        targets, _ = parse_run_config(
            '[[target]]\nname = "api"\ncommand = "x"\n', ORIGIN_TEAM, TEAM_CONFIG_FILE
        )
        self.assertIsNone(targets[0].cwd)
        self.assertEqual(targets[0].env, {})

    def test_an_entry_without_a_name_is_dropped_with_a_warning(self):
        targets, warnings = parse_run_config(
            '[[target]]\ncommand = "x"\n', ORIGIN_TEAM, TEAM_CONFIG_FILE
        )
        self.assertEqual(targets, [])
        self.assertEqual(len(warnings), 1)
        self.assertIn(TEAM_CONFIG_FILE, warnings[0])

    def test_an_entry_without_a_command_is_dropped_with_a_warning(self):
        targets, warnings = parse_run_config(
            '[[target]]\nname = "api"\n', ORIGIN_TEAM, TEAM_CONFIG_FILE
        )
        self.assertEqual(targets, [])
        self.assertEqual(len(warnings), 1)

    def test_blank_name_or_command_is_dropped(self):
        targets, warnings = parse_run_config(
            '[[target]]\nname = "  "\ncommand = "x"\n', ORIGIN_TEAM, TEAM_CONFIG_FILE
        )
        self.assertEqual(targets, [])
        self.assertEqual(len(warnings), 1)

    def test_a_duplicate_name_inside_one_file_is_dropped(self):
        text = '[[target]]\nname = "api"\ncommand = "a"\n[[target]]\nname = "api"\ncommand = "b"\n'
        targets, warnings = parse_run_config(text, ORIGIN_TEAM, TEAM_CONFIG_FILE)
        self.assertEqual([t.command for t in targets], ["a"])
        self.assertEqual(len(warnings), 1)
        self.assertIn("api", warnings[0])

    def test_an_unsafe_cwd_is_dropped(self):
        for bad in ("/etc", "../escape", "apps/../../secrets"):
            targets, warnings = parse_run_config(
                f'[[target]]\nname = "a"\ncommand = "x"\ncwd = "{bad}"\n',
                ORIGIN_TEAM,
                TEAM_CONFIG_FILE,
            )
            self.assertEqual(targets, [], bad)
            self.assertEqual(len(warnings), 1, bad)

    def test_unknown_keys_are_ignored_silently(self):
        targets, warnings = parse_run_config(
            '[[target]]\nname = "a"\ncommand = "x"\nfuture = true\n',
            ORIGIN_TEAM,
            TEAM_CONFIG_FILE,
        )
        self.assertEqual(len(targets), 1)
        self.assertEqual(warnings, [])

    def test_non_string_env_values_are_dropped_from_env(self):
        targets, _ = parse_run_config(
            '[[target]]\nname = "a"\ncommand = "x"\nenv = { PORT = 3000, HOST = "h" }\n',
            ORIGIN_TEAM,
            TEAM_CONFIG_FILE,
        )
        self.assertEqual(targets[0].env, {"HOST": "h"})

    def test_invalid_toml_yields_no_targets_and_one_warning(self):
        targets, warnings = parse_run_config("[[target]\nname =", ORIGIN_TEAM, TEAM_CONFIG_FILE)
        self.assertEqual(targets, [])
        self.assertEqual(len(warnings), 1)
        self.assertIn(TEAM_CONFIG_FILE, warnings[0])

    def test_an_empty_file_is_not_an_error(self):
        self.assertEqual(parse_run_config("", ORIGIN_TEAM, TEAM_CONFIG_FILE), ([], []))


class IsSafeCwdTest(unittest.TestCase):
    def test_accepts_relative_paths(self):
        for value in (".", "apps/web", "ops/dev"):
            self.assertTrue(is_safe_cwd(value), value)

    def test_rejects_absolute_and_traversal_and_blank(self):
        for value in ("/etc", "~/x", "../up", "a/../../b", "", "   "):
            self.assertFalse(is_safe_cwd(value), value)


class MergeRunConfigsTest(unittest.TestCase):
    def _t(self, name, command, origin):
        return Target(name=name, command=command, cwd=None, env={}, origin=origin)

    def test_a_local_target_replaces_the_team_one_with_the_same_name(self):
        team = [self._t("api", "team-cmd", ORIGIN_TEAM)]
        local = [self._t("api", "local-cmd", ORIGIN_LOCAL)]
        merged = merge_run_configs(team, local)
        self.assertEqual([t.command for t in merged], ["local-cmd"])
        self.assertEqual(merged[0].origin, ORIGIN_LOCAL)

    def test_a_new_local_name_is_appended(self):
        team = [self._t("api", "a", ORIGIN_TEAM)]
        local = [self._t("scratch", "b", ORIGIN_LOCAL)]
        self.assertEqual([t.name for t in merge_run_configs(team, local)], ["api", "scratch"])

    def test_team_order_is_preserved_and_local_additions_follow(self):
        team = [self._t("a", "1", ORIGIN_TEAM), self._t("b", "2", ORIGIN_TEAM)]
        local = [self._t("b", "2b", ORIGIN_LOCAL), self._t("z", "3", ORIGIN_LOCAL)]
        merged = merge_run_configs(team, local)
        self.assertEqual([t.name for t in merged], ["a", "b", "z"])
        self.assertEqual([t.origin for t in merged], [ORIGIN_TEAM, ORIGIN_LOCAL, ORIGIN_LOCAL])

    def test_local_alone_is_the_whole_list(self):
        local = [self._t("scratch", "b", ORIGIN_LOCAL)]
        self.assertEqual([t.name for t in merge_run_configs([], local)], ["scratch"])

    def test_team_alone_is_the_whole_list(self):
        team = [self._t("api", "a", ORIGIN_TEAM)]
        self.assertEqual([t.name for t in merge_run_configs(team, [])], ["api"])

    def test_neither_yields_nothing(self):
        self.assertEqual(merge_run_configs([], []), [])


class LoadRunConfigTest(unittest.TestCase):
    def test_both_files_are_merged(self):
        with tempfile.TemporaryDirectory() as root:
            write(os.path.join(root, TEAM_CONFIG_FILE), TEAM)
            write(
                os.path.join(root, LOCAL_CONFIG_FILE),
                '[[target]]\nname = "api"\ncommand = "local"\n'
                '[[target]]\nname = "scratch"\ncommand = "s"\n',
            )
            targets, warnings = load_run_config(root)
            self.assertEqual([t.name for t in targets], ["api", "web", "scratch"])
            self.assertEqual(targets[0].command, "local")
            self.assertEqual(warnings, [])

    def test_the_local_file_alone_is_enough(self):
        with tempfile.TemporaryDirectory() as root:
            write(os.path.join(root, LOCAL_CONFIG_FILE), '[[target]]\nname = "s"\ncommand = "x"\n')
            targets, warnings = load_run_config(root)
            self.assertEqual([t.name for t in targets], ["s"])
            self.assertEqual(targets[0].origin, ORIGIN_LOCAL)
            self.assertEqual(warnings, [])

    def test_no_file_yields_no_targets_and_no_warning(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertEqual(load_run_config(root), ([], []))

    def test_an_invalid_local_file_keeps_the_team_targets(self):
        with tempfile.TemporaryDirectory() as root:
            write(os.path.join(root, TEAM_CONFIG_FILE), TEAM)
            write(os.path.join(root, LOCAL_CONFIG_FILE), "[[target]\nbroken")
            targets, warnings = load_run_config(root)
            self.assertEqual([t.name for t in targets], ["api", "web"])
            self.assertEqual(len(warnings), 1)
            self.assertIn(LOCAL_CONFIG_FILE, warnings[0])

    def test_an_invalid_team_file_keeps_the_local_targets(self):
        with tempfile.TemporaryDirectory() as root:
            write(os.path.join(root, TEAM_CONFIG_FILE), "[[target]\nbroken")
            write(os.path.join(root, LOCAL_CONFIG_FILE), '[[target]]\nname = "s"\ncommand = "x"\n')
            targets, warnings = load_run_config(root)
            self.assertEqual([t.name for t in targets], ["s"])
            self.assertEqual(len(warnings), 1)


class ResolveRepoRootTest(unittest.TestCase):
    def test_finds_the_repository_root(self):
        with tempfile.TemporaryDirectory() as root:
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            nested = os.path.join(root, "apps", "web")
            os.makedirs(nested)
            self.assertEqual(
                os.path.realpath(resolve_repo_root(nested)), os.path.realpath(root)
            )

    def test_returns_none_outside_a_repository(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertIsNone(resolve_repo_root(root))

    def test_a_missing_git_degrades_to_none(self):
        """Sans ce garde-fou, `git` absent remonte une FileNotFoundError avant
        même le test du code de retour, et le pane meurt sur une trace."""
        with unittest.mock.patch(
            "run_targets.config.subprocess.run", side_effect=FileNotFoundError("git")
        ):
            self.assertIsNone(resolve_repo_root("/anywhere"))


if __name__ == "__main__":
    unittest.main()
