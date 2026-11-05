"""Claude CLI backend for Codex-authored discriminator reviews."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from family_attestation import FamilyAttestationError, attest_claude


CLAUDE_OVERRIDE_ENV = "DISCRIMINATOR_CLAUDE"
DEFAULT_TIMEOUT_SECONDS = 2 * 60 * 60
MAX_ATTEMPTS = 2


class ClaudeReviewerError(RuntimeError):
    """Claude CLI could not produce an attested structured review."""


@dataclass(frozen=True)
class ClaudeReviewResult:
    model_text: str
    reported_model: str
    attested_route: str


def resolve_claude_executable(explicit: str | None = None) -> str:
    candidate = explicit or os.environ.get(CLAUDE_OVERRIDE_ENV) or shutil.which("claude")
    if not candidate:
        raise ClaudeReviewerError(
            "claude executable unresolved: pass claude_path, set "
            f"{CLAUDE_OVERRIDE_ENV}, or put claude on PATH"
        )
    return str(candidate)


def build_command(claude_path: str, schema: Mapping[str, Any]) -> list[str]:
    # Claude CLI 2.1.280 rejects the canonical draft declaration because its bundled
    # validator does not register that meta-schema. `$schema` only selects the validator
    # dialect; it does not constrain the instance. Omit that one annotation on the CLI
    # boundary, then let `envelope.deliver` validate the result against the unchanged
    # canonical schema before publication.
    claude_schema = dict(schema)
    claude_schema.pop("$schema", None)
    schema_json = json.dumps(claude_schema, ensure_ascii=False, separators=(",", ":"))
    return [
        claude_path,
        "-p",
        "--bare",
        "--tools",
        "",
        "--output-format",
        "json",
        "--json-schema",
        schema_json,
        "--no-session-persistence",
        "--",
    ]


def parse_result(stdout: str) -> ClaudeReviewResult:
    try:
        wrapper = json.loads(stdout)
    except (TypeError, ValueError) as exc:
        raise ClaudeReviewerError(f"Claude CLI output was not JSON: {exc}") from exc
    if not isinstance(wrapper, dict):
        raise ClaudeReviewerError("Claude CLI output was not a JSON object")
    if wrapper.get("is_error") is not False or wrapper.get("subtype") != "success":
        raise ClaudeReviewerError(
            "Claude CLI did not report a successful result: "
            f"subtype={wrapper.get('subtype')!r}, is_error={wrapper.get('is_error')!r}"
        )

    structured = wrapper.get("structured_output")
    if not isinstance(structured, dict):
        raise ClaudeReviewerError("Claude CLI result carried no structured_output object")

    attestation = attest_claude(wrapper.get("modelUsage"))
    return ClaudeReviewResult(
        model_text=json.dumps(structured, ensure_ascii=False, separators=(",", ":")),
        reported_model=attestation.reported_model,
        attested_route=attestation.route,
    )


def _creationflags() -> int:
    return int(getattr(subprocess, "CREATE_NO_WINDOW", 0))


def run_review(
    packet_text: str,
    schema: Mapping[str, Any],
    session_cwd: str | Path,
    *,
    claude_path: str | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    attempts: int = MAX_ATTEMPTS,
    popen_factory: Callable[..., subprocess.Popen] = subprocess.Popen,
) -> ClaudeReviewResult:
    """Run at most two Claude attempts; never substitute another reviewer."""
    if attempts != MAX_ATTEMPTS:
        raise ValueError(f"attempts must remain {MAX_ATTEMPTS}")
    executable = resolve_claude_executable(claude_path)
    command = build_command(executable, schema)
    failures: list[str] = []

    for attempt in range(1, attempts + 1):
        process = None
        try:
            process = popen_factory(
                command,
                cwd=str(session_cwd),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=_creationflags(),
            )
            stdout, stderr = process.communicate(packet_text, timeout=timeout_seconds)
            if process.returncode != 0:
                diagnostic = stderr.strip() or stdout.strip() or "no diagnostic output"
                failures.append(
                    f"attempt {attempt}: Claude CLI exited {process.returncode}: {diagnostic}"
                )
                continue
            return parse_result(stdout)
        except subprocess.TimeoutExpired:
            if process is not None:
                from supervisor import kill_process_tree

                kill = kill_process_tree(process.pid)
                failures.append(f"attempt {attempt}: timeout; {kill.detail}")
            else:
                failures.append(f"attempt {attempt}: timeout before a process was returned")
        except FamilyAttestationError:
            raise
        except (OSError, ClaudeReviewerError) as exc:
            failures.append(f"attempt {attempt}: {type(exc).__name__}: {exc}")

    raise ClaudeReviewerError("; ".join(failures) or "Claude reviewer produced no result")
