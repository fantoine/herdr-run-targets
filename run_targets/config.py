"""Reading a repository's target files, and merging team with local."""

from __future__ import annotations

import os
import subprocess
import sys

# The version guard comes before `tomllib`: that module only exists from 3.11
# on, so importing it any higher would kill the plugin with an unreadable
# `ModuleNotFoundError` on exactly the versions this message is meant to greet.
# The manifest invokes a bare `python3`; nothing guarantees it is a 3.11.
if sys.version_info < (3, 11):
    sys.stderr.write("run-targets requires Python 3.11 or newer.\n")
    raise SystemExit(1)

import tomllib  # noqa: E402  (deliberately after the version guard)
from dataclasses import dataclass, field  # noqa: E402
from typing import Sequence  # noqa: E402

TEAM_CONFIG_FILE = ".herdr-run.toml"
LOCAL_CONFIG_FILE = ".herdr-run.local.toml"
ORIGIN_TEAM = "team"
ORIGIN_LOCAL = "local"


@dataclass(frozen=True)
class Target:
    """A service declared by the repository."""

    name: str
    command: str
    cwd: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    origin: str = ORIGIN_TEAM


def is_safe_cwd(value: str) -> bool:
    """Reject anything that could take a service outside the repository root."""
    if not value.strip():
        return False
    if os.path.isabs(value) or value.startswith("~"):
        return False
    return ".." not in value.replace("\\", "/").split("/")


def parse_run_config(
    text: str, origin: str, source: str
) -> tuple[list[Target], list[str]]:
    """Parse one target file.

    Returns the valid targets and the warnings. A faulty entry is dropped on its
    own: a typo in one service must not cost the user all the others. Unknown
    keys pass silently, so a file written for a newer version stays readable.
    """
    warnings: list[str] = []
    try:
        document = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        return [], [f"{source}: invalid TOML ({error})"]

    raw_targets = document.get("target")
    if not isinstance(raw_targets, list):
        return [], warnings

    targets: list[Target] = []
    seen: set[str] = set()
    for index, entry in enumerate(raw_targets):
        if not isinstance(entry, dict):
            warnings.append(f"{source}: entry {index} is not a table; skipped")
            continue

        name = entry.get("name")
        command = entry.get("command")
        if not isinstance(name, str) or not name.strip():
            warnings.append(f"{source}: entry {index} has no name; skipped")
            continue
        if not isinstance(command, str) or not command.strip():
            warnings.append(f"{source}: target {name!r} has no command; skipped")
            continue
        name = name.strip()
        if name in seen:
            warnings.append(f"{source}: duplicate target {name!r}; skipped")
            continue

        cwd = entry.get("cwd")
        if cwd is not None:
            if not isinstance(cwd, str) or not is_safe_cwd(cwd):
                warnings.append(f"{source}: target {name!r} has an unsafe cwd; skipped")
                continue

        raw_env = entry.get("env")
        env = (
            {k: v for k, v in raw_env.items() if isinstance(k, str) and isinstance(v, str)}
            if isinstance(raw_env, dict)
            else {}
        )

        seen.add(name)
        targets.append(
            Target(name=name, command=command.strip(), cwd=cwd, env=env, origin=origin)
        )

    return targets, warnings


def merge_run_configs(
    team: Sequence[Target], local: Sequence[Target]
) -> list[Target]:
    """Layer the local targets over the team ones, by `name`.

    A shared name is replaced whole rather than merged field by field: the
    target on screen is then exactly the one you read in a single file, with no
    mental recomposition of two sources.
    """
    by_name = {target.name: target for target in local}
    merged = [by_name.pop(target.name, target) for target in team]
    merged.extend(target for target in local if target.name in by_name)
    return merged


def _read(path: str) -> str | None:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read()
    except FileNotFoundError:
        return None
    except OSError:
        return None


def load_run_config(repo_root: str) -> tuple[list[Target], list[str]]:
    """Load both files from the root and merge them.

    Each file is parsed independently: broken TOML in the local scratch file
    must not cost you the repository's targets.
    """
    warnings: list[str] = []
    groups: list[list[Target]] = []
    for filename, origin in ((TEAM_CONFIG_FILE, ORIGIN_TEAM), (LOCAL_CONFIG_FILE, ORIGIN_LOCAL)):
        text = _read(os.path.join(repo_root, filename))
        if text is None:
            groups.append([])
            continue
        targets, file_warnings = parse_run_config(text, origin, filename)
        groups.append(targets)
        warnings.extend(file_warnings)
    return merge_run_configs(groups[0], groups[1]), warnings


def resolve_repo_root(cwd: str) -> str | None:
    """The root of the repository containing `cwd`, or None outside one.

    A missing `git` is treated as "no repository": the caller already knows how
    to say that plainly, where a `FileNotFoundError` bubbling up would kill the
    pane on a traceback.
    """
    try:
        result = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "--show-toplevel"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        return None
    if result.returncode != 0:
        return None
    root = result.stdout.decode("utf-8", "replace").strip()
    return root or None
