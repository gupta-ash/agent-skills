"""Shared key file utility for OpenClaw search provider skills.

Reads API keys from a JSON key file with rotation support.
Key file format:
  {
    "active": 0,
    "keys": ["key1", "key2", ...],
    "rotated_at": "2026-03-15T06:30:00Z" | null
  }

Usage in a provider skill:
  from keyfile import load_keys, rotate_key

  keys = load_keys("/example/keys/brave.json", env_fallback="BRAVE_API_KEY")
  # keys = ["active_key", "key2", ...]  (active key is first)
  # On quota error, rotate:
  rotate_key("/example/keys/brave.json")

Concurrency: atomic writes via tempfile + rename (safe on Linux ext4/btrfs).
"""

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional


DEFAULT_KEY_DIR = str(Path.home() / ".config" / "websearch" / "keys")


def load_keys(key_file: str, env_fallback: Optional[str] = None) -> List[str]:
    """Load API keys from key file, with env var fallback.

    Returns a list of keys ordered by priority: active key first, then remaining
    keys in round-robin order. If key file doesn't exist or is invalid, falls
    back to the env var.

    Args:
        key_file: Path to the provider's JSON key file.
        env_fallback: Environment variable name to use as fallback (e.g., "BRAVE_API_KEY").

    Returns:
        List of API key strings, active key first. Empty list if no keys found.
    """
    keys_from_file = _read_key_file(key_file)
    if keys_from_file:
        return keys_from_file

    # Fallback to env var
    if env_fallback:
        env_val = os.environ.get(env_fallback, "").strip()
        if env_val:
            # Support comma-separated keys in env (Brave pattern)
            return [k.strip() for k in env_val.split(",") if k.strip()]

    return []


def rotate_key(key_file: str) -> bool:
    """Advance active key index by 1 (wrapping around). Atomic write.

    Returns True if rotation succeeded, False if file doesn't exist or
    there's only one key (nothing to rotate to).
    """
    try:
        with open(key_file, "r") as f:
            state = json.load(f)
    except (OSError, json.JSONDecodeError):
        return False

    keys = state.get("keys", [])
    if len(keys) <= 1:
        return False  # nothing to rotate to

    current = state.get("active", 0)
    new_index = (current + 1) % len(keys)
    state["active"] = new_index
    state["rotated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    return _atomic_write(key_file, state)


def _read_key_file(key_file: str) -> List[str]:
    """Read key file and return keys ordered with active key first.

    Returns empty list if file doesn't exist, is unreadable, or has no keys.
    """
    try:
        with open(key_file, "r") as f:
            state = json.load(f)
    except (OSError, json.JSONDecodeError):
        return []

    keys = state.get("keys", [])
    if not keys or not isinstance(keys, list):
        return []

    # Filter out empty strings
    keys = [k for k in keys if isinstance(k, str) and k.strip()]
    if not keys:
        return []

    active = state.get("active", 0)
    if not isinstance(active, int) or active < 0:
        active = 0
    active = active % len(keys)

    # Return keys in rotation order: active first, then wrap around
    ordered = keys[active:] + keys[:active]
    return ordered


def _atomic_write(path: str, data: dict) -> bool:
    """Write JSON to file atomically using tempfile + rename."""
    try:
        dir_path = os.path.dirname(path)
        fd, tmp_path = tempfile.mkstemp(dir=dir_path, suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(data, f, indent=2)
            os.rename(tmp_path, path)
            return True
        except Exception:
            # Clean up temp file on failure
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            return False
    except Exception:
        return False
