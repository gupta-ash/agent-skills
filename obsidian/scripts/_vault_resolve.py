#!/usr/bin/env python
"""Shared Obsidian vault resolution for the obsidian CLIs.

A vault is addressed by name; its filesystem path comes from
``VAULT_<NAME_UPPER>_LOCATION`` in the invoking process's environment.
Unset names and ambiguous defaults fail without guessing a path.
"""

from __future__ import annotations

import os
from pathlib import Path


def vault_env_key(name: str) -> str:
    """Return the environment key for a vault name."""
    return "VAULT_" + name.upper() + "_LOCATION"


def resolve_vault(name: str) -> Path:
    """Resolve a vault by NAME via VAULT_<NAME>_LOCATION. Raise (loud) if unset."""
    key = vault_env_key(name)
    val = os.environ.get(key)
    if not val:
        raise SystemExit(f"Vault '{name}' is not configured: set {key} or pass --vault <absolute-path>.")
    return Path(val)


def configured_vaults() -> dict:
    """{VAULTNAME_UPPER: path} for every VAULT_*_LOCATION currently set in the env."""
    prefix, suffix = "VAULT_", "_LOCATION"
    found = {}
    for k, v in os.environ.items():
        if k.startswith(prefix) and k.endswith(suffix) and v:
            found[k[len(prefix):-len(suffix)]] = v
    return found


def get_default_vault() -> Path:
    """Use the sole configured vault; zero or multiple vaults require explicit selection."""
    configured = configured_vaults()
    if len(configured) == 1:
        return Path(next(iter(configured.values())))
    if not configured:
        raise SystemExit(
            "No Obsidian vault configured: set VAULT_<NAME>_LOCATION or pass --vault <absolute-path>."
        )
    raise SystemExit(
        "Multiple vaults configured; pass "
        f"--vault <Name-or-path> explicitly. Configured: {sorted(configured)}."
    )


def resolve_vault_arg(value):
    """Resolve a --vault CLI value.

    * falsy                         -> get_default_vault()
    * contains a path separator (/ \\ :) -> literal Path
    * otherwise a bare vault NAME   -> resolve_vault(name) via VAULT_<NAME>_LOCATION

    A bare name is ALWAYS treated as a vault name (never as a cwd-relative dir), so
    a stray same-named directory in the cwd cannot shadow the configured vault —
    that would reintroduce the silent wrong-path class this resolver exists to kill.
    """
    if not value:
        return get_default_vault()
    if any(sep in value for sep in ("/", "\\", ":")):
        return Path(value)
    return resolve_vault(value)
