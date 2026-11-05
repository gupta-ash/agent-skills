"""Offline consumer regressions using full executeReview and real finite Python helpers.

The provider/auth boundary and deterministic tokenizer are explicit test doubles.
These checks do not establish SDK token accuracy, live auth or installed acceptance.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys

import pytest


_COMPACT_KEYS = {
    "status", "verdict", "exitCode", "counts", "must_fix_high_count", "loop_should_continue",
    "caller_family", "reviewer_provider", "reviewer_model", "reviewer_api", "summary",
    "result_path", "receipt_path",
}


def _document(findings=()):
    return {"schema_version": "review-envelope/2", "verdict": "approve",
            "summary": "Synthetic offline adapter response, not a live review.",
            "findings": list(findings), "next_steps": [], "artifact_class_echo": "code-fleet"}


def _blocking_finding():
    return {"severity": "high", "category": "stability", "title": "Synthetic consumer defect",
            "body": "A concrete synthetic input produces the wrong result.",
            "file": None, "line_start": None, "line_end": None,
            "basis": "judgment", "evidence_quote": None,
            "recommendation": "Correct the erroneous synthetic transition."}


@pytest.fixture
def adapter(exported_skill, tmp_path):
    # Reuse the real manifest-derived fixture, but mutations must never affect another case.
    home = tmp_path / "home"
    shutil.copytree(exported_skill.parents[2], home)
    skill = home / ".agents/skills/discriminator"
    extension = home / ".omp/shared/extensions/discriminator/index.ts"
    declaration = home / ".agents/agent-skills/runtime.json"
    declaration.parent.mkdir(parents=True)
    declaration.write_text(json.dumps({"machine": socket.gethostname(), "tokens": {
        "__USERPROFILE__": str(home), "__OMP_PYTHON__": str(Path(sys.executable).resolve())}}), encoding="utf-8")
    source = tmp_path / "adapter-source.txt"
    source.write_text("Synthetic source-only consumer artifact.\n", encoding="utf-8")
    environment = dict(os.environ)
    for key in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "TEMP", "TMP", "TMPDIR"):
        directory = home if key in ("HOME", "USERPROFILE") else tmp_path / key.lower()
        directory.mkdir(exist_ok=True)
        environment[key] = str(directory)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    return {"root": tmp_path, "home": home, "skill": skill, "extension": extension, "declaration": declaration,
            "helper": skill / "native_review.py", "source": source, "environment": environment}


def _invoke(adapter, **options):
    bun = shutil.which("bun")
    assert bun is not None, "Bun is required for the full offline adapter regressions"
    config = {"extension": str(adapter["extension"]), "helper": str(adapter["helper"]),
              "declaration": str(adapter["declaration"]),
              "cwd": str(adapter["root"]), "source": adapter["source"].name,
              "scenario": "normal", "review": _document(), **options}
    runner = Path(__file__).with_name("native_adapter_runner.ts")
    completed = subprocess.run([bun, str(runner)], input=json.dumps(config),
                               cwd=adapter["root"], env=adapter["environment"],
                               text=True, capture_output=True, timeout=90)
    assert completed.returncode == 0, completed.stderr
    report = json.loads(completed.stdout)
    assert report["source_only"] is True
    assert report["tokenizer"] == {"kind": "deterministic-test-double"}
    return report


def _compact(invocation):
    result = invocation["result"]
    compact = result["details"]
    assert set(compact) == _COMPACT_KEYS
    return compact


def _receipt(adapter, invocation):
    compact = _compact(invocation)
    receipt = invocation["receipt"]
    assert receipt is not None
    assert Path(compact["receipt_path"]).is_relative_to(adapter["root"])
    assert receipt["adapter_invocations"] == invocation["provider_calls"]
    return receipt


def _delivery(adapter, invocation):
    compact = _compact(invocation)
    receipt = _receipt(adapter, invocation)
    assert compact["result_path"] is not None
    filename = Path(compact["result_path"])
    assert filename.is_relative_to(adapter["root"])
    assert receipt["result"] == {"path": str(filename),
                                 "sha256": hashlib.sha256(filename.read_bytes()).hexdigest()}
    delivery = invocation["delivery"]
    assert (delivery["status"], delivery["verdict"]) == (compact["status"], compact["verdict"])
    return delivery


def _no_review(invocation, status="tooling-failure"):
    compact = _compact(invocation)
    assert (compact["status"], compact["exitCode"], compact["verdict"]) == (status, 1, None)
    assert compact["loop_should_continue"] is True
    return compact


def _alter_handshake(adapter, statement):
    """Fault only the copied helper descriptor; actual preparation/grading stay real."""
    filename = adapter["helper"]
    source = filename.read_text(encoding="utf-8")
    marker = 'if __name__ == "__main__":'
    assert source.count(marker) == 1
    override = ("_real_handshake = handshake\n\n"
                "def handshake(value):\n"
                "    response = _real_handshake(value)\n"
                f"    {statement}\n"
                "    return response\n\n\n")
    filename.write_text(source.replace(marker, override + marker), encoding="utf-8")


def test_full_adapter_rejects_directory_python_before_helper(adapter):
    declaration = json.loads(adapter["declaration"].read_text(encoding="utf-8"))
    declaration["tokens"]["__OMP_PYTHON__"] = str(adapter["home"])
    adapter["declaration"].write_text(json.dumps(declaration), encoding="utf-8")
    report = _invoke(adapter)
    invocation, = report["invocations"]
    compact = _no_review(invocation)
    # Exercise the real filesystem file-type guard through the complete adapter,
    # not just the separately extracted installedInputs resolver.
    assert "MACHINE_PYTHON_EXECUTABLE_MISSING" in compact["summary"]
    assert report["provider_calls"] == 0 and invocation["auth_calls"] == 0
    assert invocation["tokenizer_checks"] == [] and invocation["provider_contexts"] == []
    assert invocation["handshake"] is None and invocation["prepared"] is None
    assert compact["result_path"] is None and compact["receipt_path"] is None


@pytest.mark.parametrize("fault,reason", [
    ("missing", "PREPARATION_OR_RETENTION_FAILED"),
    ("missing-command", "MACHINE_PYTHON_DECLARATION_MISSING"),
    ("relative-python", "MACHINE_PYTHON_DECLARATION_MISSING"),
    ("wrong-machine", "RUNTIME_MACHINE_MISMATCH"),
    ("wrong-profile", "RUNTIME_PROFILE_MISMATCH"),
])
def test_full_adapter_requires_target_local_runtime_without_fallback(adapter, fault, reason):
    declaration = adapter["declaration"]
    value = json.loads(declaration.read_text(encoding="utf-8"))
    ambient = adapter["root"] / "unrelated-home"
    ambient.mkdir()
    ambient_runtime = ambient / ".agents/agent-skills/runtime.json"
    ambient_runtime.parent.mkdir(parents=True)
    ambient_runtime.write_text(json.dumps({"machine": socket.gethostname(), "tokens": {
        "__USERPROFILE__": str(ambient), "__OMP_PYTHON__": str(Path(sys.executable).resolve())}}), encoding="utf-8")
    adapter["environment"].update({"HOME": str(ambient), "USERPROFILE": str(ambient)})
    for filename in (adapter["home"] / ".omp/agent/mcp.json",
                     adapter["home"] / ".omp/profiles/legacy-profile/agent/mcp.json",
                     adapter["root"] / "config/machines" / (socket.gethostname() + ".json")):
        filename.parent.mkdir(parents=True, exist_ok=True)
        filename.write_text(json.dumps({"tokens": value["tokens"],
            "mcpServers": {"omp-relay": {"command": sys.executable}}}), encoding="utf-8")
    if fault == "missing":
        declaration.unlink()
    else:
        if fault == "missing-command":
            value["tokens"].pop("__OMP_PYTHON__")
        elif fault == "relative-python":
            value["tokens"]["__OMP_PYTHON__"] = "python"
        elif fault == "wrong-machine":
            value["machine"] = socket.gethostname() + "-different"
        else:
            value["tokens"]["__USERPROFILE__"] = str(ambient)
        declaration.write_text(json.dumps(value), encoding="utf-8")
    report = _invoke(adapter)
    invocation, = report["invocations"]
    compact = _no_review(invocation)
    assert reason in compact["summary"]
    assert report["provider_calls"] == 0 and invocation["auth_calls"] == 0
    assert invocation["tokenizer_checks"] == [] and invocation["provider_contexts"] == []
    assert invocation["handshake"] is None and invocation["prepared"] is None
    assert compact["result_path"] is None and compact["receipt_path"] is None


@pytest.mark.parametrize("caller_family,blocking", [("gpt", False), ("claude", False), ("gpt", True)])
def test_full_adapter_publishes_real_canonical_grading(adapter, caller_family, blocking):
    findings = [_blocking_finding()] if blocking else []
    report = _invoke(adapter, caller_family=caller_family, review=_document(findings))
    assert report["provider_calls"] == 1
    invocation, = report["invocations"]
    compact = _compact(invocation)
    assert (compact["status"], compact["exitCode"], compact["verdict"]) == (
        "reviewed", 4 if blocking else 0, "fail" if blocking else "approve")
    assert compact["caller_family"] == caller_family
    assert invocation["auth_calls"] == 1
    assert len(invocation["tokenizer_checks"]) == 1
    receipt = _receipt(adapter, invocation)
    assert receipt["publication_allowed"] is True
    assert receipt["cancelled"] is False and receipt["local_settled"] is True
    assert receipt["sources"]["extension"]["sha256"] == report["extension_sha256"]
    assert receipt["sources"]["helper"]["sha256"] == invocation["code_hashes"]["native_review.py"]
    assert receipt["sources"]["pythonDeclaration"] == {"path": str(adapter["declaration"]),
        "sha256": hashlib.sha256(adapter["declaration"].read_bytes()).hexdigest()}
    assert invocation["handshake"]["status"] == "compatible"
    assert invocation["handshake"]["dependencies"] == invocation["code_hashes"]
    prepared = invocation["prepared"]
    assert prepared["source_text"] == adapter["source"].read_text(encoding="utf-8")
    context, = invocation["provider_contexts"]
    assert context["tools"] == []
    assert len(context["messages"]) == 1
    assert context["messages"][0]["role"] == "user"
    assert context["messages"][0]["content"] == prepared["packet_text"]
    delivery = _delivery(adapter, invocation)
    assert delivery["review"]["verdict"] == "approve"
    assert delivery["review"]["findings"] == findings
    assert compact["must_fix_high_count"] == (1 if blocking else 0)
    assert compact["loop_should_continue"] is blocking


def test_loaded_adapter_accepts_compatible_helper_byte_change_between_calls(adapter):
    report = _invoke(adapter, invocations=2,
                     between_invocations_append="\n# Compatible synthetic helper source revision.\n")
    first, second = report["invocations"]
    assert report["provider_calls"] == 2
    for invocation in (first, second):
        compact = _compact(invocation)
        assert (compact["exitCode"], compact["verdict"]) == (0, "approve")
        assert invocation["provider_calls"] == 1
        assert invocation["handshake"]["dependencies"] == invocation["code_hashes"]
        receipt = _receipt(adapter, invocation)
        assert receipt["sources"]["helper"]["sha256"] == invocation["code_hashes"]["native_review.py"]
        assert receipt["sources"]["extension"]["sha256"] == report["extension_sha256"]
        _delivery(adapter, invocation)
    assert first["code_hashes"]["native_review.py"] != second["code_hashes"]["native_review.py"]
    assert first["prepared"]["binding"]["invocation_id"] != second["prepared"]["binding"]["invocation_id"]


def test_compatible_helper_can_advertise_additional_capability(adapter):
    _alter_handshake(adapter, 'response["contract"]["capabilities"].append("synthetic-future-capability")')
    report = _invoke(adapter)
    invocation, = report["invocations"]
    assert report["provider_calls"] == 1
    assert (_compact(invocation)["exitCode"], _compact(invocation)["verdict"]) == (0, "approve")
    assert "synthetic-future-capability" in invocation["handshake"]["contract"]["capabilities"]
    _delivery(adapter, invocation)


@pytest.mark.parametrize("statement,reason", [
    ('response.pop("contract")', "HELPER_CONTRACT_MISSING"),
    ('response["contract"]["schemas"] = []', "HELPER_CONTRACT_MALFORMED"),
    ('response["contract"]["capabilities"] = [42]', "HELPER_CONTRACT_MALFORMED"),
    ('response["contract"]["protocol"] = "ash-discriminator-native/2"', "HELPER_CONTRACT_INCOMPATIBLE"),
    ('response["contract"]["schemas"]["result"] = 2', "HELPER_CONTRACT_INCOMPATIBLE"),
    ('response["contract"]["schemas"]["prepared"] = True', "HELPER_CONTRACT_INCOMPATIBLE"),
    ('response["contract"]["capabilities"].remove("host-failure-reason")', "HELPER_CONTRACT_INCOMPATIBLE"),
    ('response["dependencies"].pop("packet.py")', "HELPER_CONTRACT_DEPENDENCIES_INVALID"),
    ('response["dependencies"]["transport.py"] = "0" * 64', "HELPER_CONTRACT_DEPENDENCIES_INVALID"),
    ('response["dependencies"]["packet.py"] = "0" * 64', "STALE_RESOURCE"),
    ('response = _failure(value, "handshake", "SYNTHETIC_HELPER_REFUSAL")', "HELPER_CONTRACT_REJECTED"),
], ids=["missing-contract", "malformed-schemas", "malformed-capabilities", "protocol-version",
        "schema-version", "boolean-schema-version", "missing-capability", "missing-code",
        "extra-code", "stale-code", "canonical-helper-refusal"])
def test_invalid_helper_handshake_refuses_before_reviewer(adapter, statement, reason):
    _alter_handshake(adapter, statement)
    report = _invoke(adapter)
    invocation, = report["invocations"]
    compact = _no_review(invocation)
    assert report["provider_calls"] == 0
    assert invocation["auth_calls"] == 0
    assert invocation["tokenizer_checks"] == []
    assert reason in compact["summary"]
    assert _receipt(adapter, invocation)["publication_allowed"] is False
    assert invocation["prepared"] is None
    assert compact["result_path"] is None


@pytest.mark.parametrize("scenario,provider_calls", [("change-before-inference", 0), ("change-during-stream", 1)])
def test_helper_byte_change_during_invocation_never_publishes_approval(adapter, scenario, provider_calls):
    report = _invoke(adapter, scenario=scenario)
    invocation, = report["invocations"]
    compact = _no_review(invocation)
    assert report["provider_calls"] == provider_calls
    assert "LOADED_SOURCE_CHANGED" in compact["summary"]
    receipt = _receipt(adapter, invocation)
    assert receipt["publication_allowed"] is False
    assert receipt["sources"]["helper"]["sha256"] == invocation["code_hashes"]["native_review.py"]
    assert invocation["helper_sha256_after"] != invocation["code_hashes"]["native_review.py"]
    # A changed executable helper must not be launched just to manufacture a refusal.
    assert "finish-output.json" not in invocation["evidence_files"]
    assert compact["result_path"] is None


@pytest.mark.parametrize("scenario,provider_calls", [
    ("change-declaration-before-inference", 0), ("change-declaration-during-stream", 1)])
def test_public_runtime_declaration_remains_bound_until_publication(adapter, scenario, provider_calls):
    original = hashlib.sha256(adapter["declaration"].read_bytes()).hexdigest()
    report = _invoke(adapter, scenario=scenario)
    invocation, = report["invocations"]
    compact = _no_review(invocation)
    assert report["provider_calls"] == provider_calls
    assert "LOADED_SOURCE_CHANGED" in compact["summary"]
    receipt = _receipt(adapter, invocation)
    assert receipt["publication_allowed"] is False
    assert receipt["sources"]["pythonDeclaration"] == {"path": str(adapter["declaration"]), "sha256": original}
    assert hashlib.sha256(adapter["declaration"].read_bytes()).hexdigest() != original
    assert "finish-output.json" not in invocation["evidence_files"]
    assert compact["result_path"] is None


def test_packet_too_large_retains_observed_budget_and_actual_public_reason(adapter):
    report = _invoke(adapter, context_window=10_000, max_tokens=100, synthetic_tokens=10_001)
    invocation, = report["invocations"]
    compact = _no_review(invocation, "too-large")
    assert report["provider_calls"] == 0
    assert invocation["auth_calls"] == 0
    assert "PACKET_TOO_LARGE" in compact["summary"]
    assert "TERMINAL_NOT_OBJECT" not in compact["summary"]
    receipt = _receipt(adapter, invocation)
    assert receipt["reason"] == "PACKET_TOO_LARGE"
    assert receipt["terminal"] is None
    assert receipt["publication_allowed"] is False
    budget = receipt["input_budget"]
    assert budget == {"contextWindow": 10_000, "maxTokens": 100, "budget": 9900,
                      "tokens": 10_001, "fits": False, "exact": False}
    check, = invocation["tokenizer_checks"]
    assert check["budget"] == budget["budget"]
    assert check["checked"]["tokens"] == budget["tokens"]
    delivery = _delivery(adapter, invocation)
    assert delivery["verdict"] is None
    assert "PACKET_TOO_LARGE" in json.dumps(delivery)
    assert "TERMINAL_NOT_OBJECT" not in json.dumps(delivery)
    assert delivery["status"] == "too-large" and delivery["fault"] == "caller-or-environment"
    body = delivery["tool_findings"][0]["body"]
    assert json.JSONDecoder().raw_decode(body[body.index("{"):])[0] == budget
    for value in ("tokens=10001", "budget=9900", "exact=false"):
        assert value in compact["summary"]


@pytest.mark.parametrize("observed", [
    {"tokens": 10_001, "fits": True, "exact": True},
    {"fits": False, "exact": True},
])
def test_invalid_tokenizer_observation_is_retained_but_never_used_for_inference(adapter, observed):
    report = _invoke(adapter, context_window=10_000, max_tokens=100, budget_result=observed)
    invocation, = report["invocations"]
    _no_review(invocation)
    assert report["provider_calls"] == 0 and invocation["auth_calls"] == 0
    receipt = _receipt(adapter, invocation)
    assert receipt["reason"] == "UNOBSERVED_INPUT_BUDGET"
    assert receipt["input_budget"] == {"contextWindow": 10_000, "maxTokens": 100, "budget": 9900, **observed}
    assert _delivery(adapter, invocation)["verdict"] is None


@pytest.mark.parametrize("statement", ['response["dependencies"].pop("packet.py")',
                                       'response["dependencies"]["packet.py"] = "0" * 64'])
def test_preparation_must_match_handshaken_code_before_inference(adapter, statement):
    filename = adapter["helper"]
    source = filename.read_text(encoding="utf-8")
    override = ("_real_prepare = prepare\n\n"
                "def prepare(value):\n"
                "    response = _real_prepare(value)\n"
                f"    {statement}\n"
                "    return response\n\n\n")
    filename.write_text(source.replace('if __name__ == "__main__":', override + 'if __name__ == "__main__":'), encoding="utf-8")
    report = _invoke(adapter)
    invocation, = report["invocations"]
    _no_review(invocation)
    assert report["provider_calls"] == 0 and invocation["auth_calls"] == 0
    assert _receipt(adapter, invocation)["reason"] == "PREPARE_HANDSHAKE_CODE_MISMATCH"


@pytest.mark.parametrize("scenario,provider_calls", [("cancel-before-call", 0), ("cancel-during-stream", 1)])
def test_cancellation_cannot_publish_a_late_approval(adapter, scenario, provider_calls):
    report = _invoke(adapter, scenario=scenario)
    invocation, = report["invocations"]
    compact = _no_review(invocation)
    assert report["provider_calls"] == provider_calls
    receipt = _receipt(adapter, invocation)
    assert receipt["reason"] == "CALLER_CANCELLED"
    assert receipt["cancelled"] is True
    assert receipt["request_abort_requested"] is True
    assert receipt["publication_allowed"] is False
    if provider_calls:
        assert receipt["local_settled"] is True
        assert invocation["provider_contexts"]
        assert _delivery(adapter, invocation)["verdict"] is None
