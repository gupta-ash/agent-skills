#!/usr/bin/env python
"""Regression test for the per-vault env mechanism (VAULT_<NAME>_LOCATION).

The environment and path strings are synthetic; paths are compared as values and never opened.

Run: python obsidian/tests/test_vault_resolver.py
"""
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OBS = HERE.parent / "scripts"
sys.path.insert(0, str(OBS))
import _vault_resolve as vr  # noqa: E402

failures = []


def check(name, cond):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}")
    if not cond:
        failures.append(name)


def _raises(exc, fn, *a):
    try:
        fn(*a)
        return False
    except exc:
        return True


# --- _vault_resolve (env-driven; fail-loud) ---
for k in list(os.environ):
    if k.startswith("VAULT_") and k.endswith("_LOCATION"):
        del os.environ[k]

check("default: zero vaults refuses", _raises(SystemExit, vr.get_default_vault))
check("resolve: unset refuses", _raises(SystemExit, vr.resolve_vault, "Notes"))
os.environ["VAULT_NOTES_LOCATION"] = "/synthetic/vaults/notes"
check("resolve: named vault", vr.resolve_vault("Notes") == Path("/synthetic/vaults/notes"))
check("default: sole vault", vr.get_default_vault() == Path("/synthetic/vaults/notes"))
os.environ["VAULT_REFERENCE_LOCATION"] = "/synthetic/vaults/reference"
check("default: multiple vaults refuse", _raises(SystemExit, vr.get_default_vault))
del os.environ["VAULT_NOTES_LOCATION"]
check("default: remaining sole vault", vr.get_default_vault() == Path("/synthetic/vaults/reference"))
check("arg: path-like passthrough", vr.resolve_vault_arg("/synthetic/explicit") == Path("/synthetic/explicit"))
check("arg: bare name resolves env", vr.resolve_vault_arg("Reference") == Path("/synthetic/vaults/reference"))
check("arg: unknown name refuses", _raises(SystemExit, vr.resolve_vault_arg, "Unknown"))


if failures:
    print(f"\n_selftest_vault: FAIL ({len(failures)} failed)")
    sys.exit(1)
print("\n_selftest_vault: PASS")
