"""Collect source tests only after creating the declared disposable runtime closure."""
from __future__ import annotations

import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sys
import tempfile

import pytest


@pytest.fixture(scope="session", autouse=True)
def exported_skill():
    return _skill_root


_saved_environment = {}
_temporary = None
_skill_root = None


def pytest_configure(config):
    global _temporary, _skill_root
    repository = Path(__file__).resolve().parents[2]
    declaration = json.loads((repository / "deploy" / "exports.json").read_text(encoding="utf-8"))
    entries = [entry for entry in declaration["entries"]
               if entry.get("source", "").startswith("discriminator/")]
    if len(entries) != 29:
        raise pytest.UsageError("Discriminator requires all 29 declared runtime exports before collection")
    _temporary = tempfile.TemporaryDirectory(prefix="discriminator-source-tests-")
    candidate = Path(_temporary.name) / "candidate"
    destinations = set()
    for entry in entries:
        relative = PurePosixPath(entry["destination"])
        if relative.is_absolute() or ".." in relative.parts or relative in destinations:
            raise pytest.UsageError("Invalid discriminator export destination")
        destinations.add(relative)
        destination = candidate.joinpath(*relative.parts)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(repository / entry["source"], destination)
    home = candidate / "harness"
    _skill_root = home / ".agents" / "skills" / "discriminator"
    runtime_modules = {p.stem for p in _skill_root.glob("*.py")}
    if runtime_modules.intersection(sys.modules):
        raise pytest.UsageError("Discriminator modules were imported before isolated HOME setup")
    for key, value in {"HOME": str(home), "USERPROFILE": str(home),
                       "PYTHONDONTWRITEBYTECODE": "1"}.items():
        _saved_environment[key] = os.environ.get(key)
        os.environ[key] = value
    sys.path.insert(0, str(_skill_root))


def pytest_unconfigure(config):
    if _skill_root is not None and str(_skill_root) in sys.path:
        sys.path.remove(str(_skill_root))
    for key, value in _saved_environment.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    if _temporary is not None:
        _temporary.cleanup()
