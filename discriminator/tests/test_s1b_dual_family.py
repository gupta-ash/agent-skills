from __future__ import annotations

import json
import io
from pathlib import Path

import pytest



import claude_reviewer  # noqa: E402
import envelope  # noqa: E402
import envelope_schema  # noqa: E402
import family_attestation  # noqa: E402
import request  # noqa: E402
import result  # noqa: E402
import transport  # noqa: E402


def _review(verdict: str = "approve") -> dict:
    return {
        "schema_version": "review-envelope/2",
        "verdict": verdict,
        "summary": "fixture",
        "findings": [],
        "next_steps": [],
        "artifact_class_echo": None,
    }


def _claude_wrapper(review: dict | None = None) -> str:
    review = _review() if review is None else review
    return json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": json.dumps(review),
            "structured_output": review,
            "modelUsage": {
                "claude-opus-5-5[1m]": {
                    "canonicalModel": "claude-opus-5-5",
                    "provider": "firstParty",
                }
            },
        }
    )


def test_reviewer_declaration_supports_both_opposite_family_routes() -> None:
    assert request.require_reviewer({request.REVIEWER_ENV: "codex"}) == "codex"
    assert request.require_reviewer({request.REVIEWER_ENV: " claude "}) == "claude"
    with pytest.raises(request.Refusal):
        request.require_reviewer({request.REVIEWER_ENV: "other"})


def test_caller_and_reviewer_must_be_declared_and_opposite() -> None:
    assert (
        request.require_cross_family(
            {
                request.CALLER_FAMILY_ENV: "codex",
                request.REVIEWER_ENV: "claude",
            }
        )
        == "claude"
    )
    with pytest.raises(request.Refusal):
        request.require_cross_family({request.REVIEWER_ENV: "claude"})
    with pytest.raises(request.Refusal):
        request.require_cross_family(
            {
                request.CALLER_FAMILY_ENV: "codex",
                request.REVIEWER_ENV: "codex",
            }
        )


def test_codex_family_attestation_accepts_openai_and_rejects_other_or_unknown() -> None:
    route = family_attestation.attest_codex(
        "gpt-5.6-sol", "discriminator/0.156.1 (Windows; x86_64)"
    )
    assert route.startswith("codex-app-server:")
    with pytest.raises(family_attestation.FamilyAttestationError):
        family_attestation.attest_codex("claude-opus-5-5", "discriminator/0.156.1")
    with pytest.raises(family_attestation.FamilyAttestationError):
        family_attestation.attest_codex(None, "discriminator/0.156.1")


@pytest.mark.parametrize("model", ["o1", "o3", "o4", "o3-mini", "gpt-5.6-sol"])
def test_codex_family_attestation_accepts_supported_openai_id_shapes(model: str) -> None:
    assert family_attestation.attest_codex(model, "discriminator/0.156.1")


def test_claude_result_is_attested_and_keeps_review_envelope_two() -> None:
    result = claude_reviewer.parse_result(_claude_wrapper())
    assert json.loads(result.model_text)["schema_version"] == "review-envelope/2"
    assert result.reported_model == "claude-opus-5-5"
    assert result.attested_route.startswith("claude-cli:")


@pytest.mark.parametrize(
    "model_usage",
    [
        {},
        {"unknown": {"provider": "firstParty"}},
        {"gpt": {"canonicalModel": "gpt-5.6-sol", "provider": "firstParty"}},
    ],
)
def test_claude_result_rejects_same_family_or_unverifiable_metadata(model_usage: dict) -> None:
    wrapper = json.loads(_claude_wrapper())
    wrapper["modelUsage"] = model_usage
    with pytest.raises(family_attestation.FamilyAttestationError):
        claude_reviewer.parse_result(json.dumps(wrapper))


def test_claude_command_has_no_codex_fallback_and_preserves_empty_tools_argument() -> None:
    command = claude_reviewer.build_command(
        "claude", envelope_schema.ENVELOPE_SCHEMA
    )
    assert command[0] == "claude"
    assert "codex" not in command
    assert command[command.index("--tools") + 1] == ""
    assert command[-1] == "--"


def test_codex_requested_model_provenance_preserves_explicit_setting() -> None:
    original = request.REVIEWER_MODEL
    try:
        request.REVIEWER_MODEL = "gpt-explicit-fixture"
        provenance = request._provenance(
            envelope,
            "disc-20990101T000001Z-a11ce0000001",
            "gpt-explicit-fixture",
            request.CHANNEL_RESULT_HANDLE,
        )
    finally:
        request.REVIEWER_MODEL = original
    assert provenance.requested_model == "gpt-explicit-fixture"


def test_attestation_error_publishes_tooling_failure_under_issued_handle(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = tmp_path / "artifact.md"
    source.write_text("The sky appears blue in daylight.", encoding="utf-8")
    store = tmp_path / "store"
    monkeypatch.setenv(request.CALLER_FAMILY_ENV, "codex")
    monkeypatch.setenv(request.REVIEWER_ENV, "claude")
    monkeypatch.setattr(result, "default_store_root", lambda: store)

    def reject(*args, **kwargs):
        raise family_attestation.FamilyAttestationError("fixture family mismatch")

    monkeypatch.setattr(claude_reviewer, "run_review", reject)
    out, err = io.StringIO(), io.StringIO()
    rc = request.main(["--contract", "none", "--source", str(source)], out=out, err=err)
    handle = out.getvalue().strip()
    assert rc == 1
    assert handle.startswith("disc-")
    document = result.collect(handle)
    assert document["status"] == "tooling-failure"
    assert document["provenance"]["requested_model"] == request.CLAUDE_MODEL_UNSPECIFIED


@pytest.mark.parametrize(
    ("verdict", "expected"),
    [("approve", 0), (None, 1), ("needs-attention", 2), ("fail", 4)],
)
def test_documented_exit_codes_remain_stable(verdict: str | None, expected: int) -> None:
    assert request.exit_code_for(verdict) == expected


@pytest.mark.parametrize("reviewer", ["codex", "claude"])
def test_public_executable_override_precedence(monkeypatch, tmp_path, reviewer):
    module = transport if reviewer == "codex" else claude_reviewer
    resolve = getattr(module, "resolve_" + reviewer + "_executable")
    explicit, override, on_path = [tmp_path / (name + "-" + reviewer) for name in ("explicit", "override", "path")]
    for filename in (explicit, override, on_path):
        filename.write_bytes(b"synthetic executable path, never launched")
    key = "DISCRIMINATOR_" + reviewer.upper()
    monkeypatch.setattr(module.os, "environ", {key: str(override)})
    monkeypatch.setattr(module.shutil, "which", lambda command: str(on_path) if command == reviewer else None)
    assert resolve(str(explicit)) == str(explicit)
    assert resolve() == str(override)
    monkeypatch.setattr(module.os, "environ", {"UNRELATED_" + reviewer.upper(): str(override)})
    assert resolve() == str(on_path)
    monkeypatch.setattr(module.shutil, "which", lambda command: None)
    with pytest.raises(transport.ServerSpawnError if reviewer == "codex" else claude_reviewer.ClaudeReviewerError):
        resolve()


