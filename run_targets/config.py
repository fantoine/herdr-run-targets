"""Lecture des fichiers de targets d'un dépôt, et fusion équipe / local."""

from __future__ import annotations

import os
import subprocess
import sys

# Le garde-fou de version passe avant `tomllib` : ce module n'existe qu'à partir
# de 3.11, donc l'importer plus haut ferait échouer le plugin sur un
# `ModuleNotFoundError` illisible, exactement sur les versions que ce message
# est censé accueillir. Le manifeste invoque `python3` nu ; rien ne garantit
# que ce soit une 3.11.
if sys.version_info < (3, 11):
    sys.stderr.write("run-targets requires Python 3.11 or newer.\n")
    raise SystemExit(1)

import tomllib  # noqa: E402  (après le garde-fou de version, volontairement)
from dataclasses import dataclass, field  # noqa: E402
from typing import Sequence  # noqa: E402

TEAM_CONFIG_FILE = ".herdr-run.toml"
LOCAL_CONFIG_FILE = ".herdr-run.local.toml"
ORIGIN_TEAM = "team"
ORIGIN_LOCAL = "local"


@dataclass(frozen=True)
class Target:
    """Un service déclaré par le dépôt."""

    name: str
    command: str
    cwd: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    origin: str = ORIGIN_TEAM


def is_safe_cwd(value: str) -> bool:
    """Rejette ce qui pourrait faire sortir un service de la racine du dépôt."""
    if not value.strip():
        return False
    if os.path.isabs(value) or value.startswith("~"):
        return False
    return ".." not in value.replace("\\", "/").split("/")


def parse_run_config(
    text: str, origin: str, source: str
) -> tuple[list[Target], list[str]]:
    """Parse un fichier de targets.

    Renvoie les targets valides et les avertissements. Une entrée fautive est
    écartée seule : une faute de frappe sur un service ne doit pas priver
    l'utilisateur de tous les autres. Les clés inconnues passent en silence,
    pour qu'un fichier écrit pour une version plus récente reste lisible.
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
    """Superpose les targets locales aux targets d'équipe, par `name`.

    Un même nom est remplacé en entier plutôt que fusionné champ par champ :
    la target affichée est ainsi exactement celle qu'on lit dans un seul
    fichier, sans avoir à recomposer mentalement deux sources.
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
    """Charge les deux fichiers de la racine et les fusionne.

    Chaque fichier est parsé indépendamment : un TOML cassé dans le fichier
    local de test ne doit pas faire perdre les targets du dépôt.
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
    """La racine du dépôt contenant `cwd`, ou None hors dépôt.

    Un `git` absent est traité comme « pas de dépôt » : l'appelant sait déjà
    dire la chose clairement, là où une `FileNotFoundError` remontée ferait
    mourir le pane sur une trace d'exécution.
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
