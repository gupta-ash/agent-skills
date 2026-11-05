"""Consumer-visible native qualification and canonical grading boundaries."""
from copy import deepcopy
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
import uuid

import pytest

import claude_reviewer
import envelope_schema
import native_review


def test_public_export_closure_preserves_locked_assets(exported_skill):
    expected = {"SKILL.md", "agents/discriminator.toml"}
    expected.update(name + ".py" for name in (
        "request", "native_review", "packet", "envelope", "envelope_schema", "result",
        "supervisor", "telemetry", "family_attestation", "transport", "claude_reviewer"))
    expected.update("contracts/" + name + ".md" for name in native_review.request.CONTRACT_NAMES)
    expected.update("assets/" + name for name in (
        "rubric.txt", "rubric.lock.json", "preamble_envelope.txt", "preamble_finding.txt"))
    assert len(expected) == 27
    assert {filename.relative_to(exported_skill).as_posix() for filename in exported_skill.rglob("*")
            if filename.is_file()} == expected
    home = exported_skill.parents[2]
    assert (home / ".claude/agents/discriminator.md").is_file()
    assert (home / ".omp/shared/extensions/discriminator/index.ts").is_file()
    assert native_review.result_store.default_store_root() == home / ".discriminator/runs"
    lock = json.loads((exported_skill / "assets/rubric.lock.json").read_text(encoding="utf-8"))
    assert "source_path" not in lock
    assert lock["generator_version"] == "packet-extractor/1"
    assert lock["source_digest"] == "3fff8d86d31ebd15548a28c1692b8ce65925a6e5e11464f9c02d1c3bc313ffa1"
    pinned = {
        "rubric.txt": (9277, "d6191925bd5983a72039969e86280cd6b7d008c62fa68752de3776baf6dd8d82",
                       "56e0996d805e741e1fb1c36f4460e1016087b0b2a87fd7c8e022594f2f128d1b"),
        "preamble_envelope.txt": (649, "fc350f0f4ed5208db93abc33d2afb0f624f0e2a0dbac05c631e484c6d67662ab",
                                 "0c0423813ffd881a2a22d2742342fd781093923efc30609ac8c4a2fe8976ab80"),
        "preamble_finding.txt": (569, "94d8b74641446e857c08677d753fdae20c575c8a94df17e5bb270538c54f6c97",
                                "0c0423813ffd881a2a22d2742342fd781093923efc30609ac8c4a2fe8976ab80"),
    }
    assert set(lock["assets"]) == set(pinned)
    for name, (size, digest, section_digest) in pinned.items():
        asset = (exported_skill / "assets" / name).read_bytes()
        entry = lock["assets"][name]
        assert len(asset) == entry["bytes"] == size
        assert hashlib.sha256(asset).hexdigest() == entry["digest"] == digest
        assert entry["section_digest"] == section_digest
        assert not {"source_path", "section_source_path"}.intersection(entry)


def document(artifact_class="code-fleet", verdict="approve", findings=(), summary="Consumer review"):
    return {"schema_version": "review-envelope/2", "verdict": verdict,
            "summary": summary, "findings": list(findings), "next_steps": [],
            "artifact_class_echo": None if artifact_class == "none" else artifact_class}


def finding(severity, category):
    return {"severity": severity, "category": category, "title": "Consumer defect",
            "body": "A concrete input produces a wrong consumer-visible result.",
            "file": None, "line_start": None, "line_end": None,
            "basis": "judgment", "evidence_quote": None,
            "recommendation": "Correct the erroneous transition."}


@pytest.fixture
def prepared(tmp_path):
    source = tmp_path / "source.py"
    source.write_bytes(b"def is_even(n):\n    return n % 2 == 0\n")
    value = native_review.prepare({"invocation_id": str(uuid.uuid4()), "tool_call_id": "consumer-call",
                                   "caller_family": "gpt", "contract": "code-fleet", "source": str(source)})
    assert value["status"] == "prepared", value
    return value


def operation(prepared, raw):
    route = prepared["reviewer_route"]
    return {"prepared": prepared, "raw_text": raw, "observation": {
        "binding": deepcopy(prepared["binding"]), "requested": deepcopy(route), "resolved": deepcopy(route),
        "terminal": {**route, "stopReason": "stop", "toolCallCount": 0},
        "adapter_invocations": 1, "cancelled": False, "local_settled": True,
        "publication_allowed": True, "reason": None, "upstream_identity": None}}


def finish(prepared, doc):
    return native_review.finish(operation(prepared, json.dumps(doc, allow_nan=False)))


def handshake_request():
    return {"protocol": "ash-discriminator-native/1",
            "schemas": {"prepared": 1, "observation": 1, "result": 1},
            "capabilities": ["canonical-grading", "image-input-refs", "host-failure-reason", "publication-refusal"]}


def test_handshake_reports_exact_code_closure_without_preparation(exported_skill, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Handshake must not prepare resources or dispatch a reviewer")

    monkeypatch.setattr(native_review, "prepare", forbidden)
    monkeypatch.setattr(native_review, "finish", forbidden)
    monkeypatch.setattr(native_review.packet, "load_assets", forbidden)
    monkeypatch.setattr(native_review.request, "_launch", forbidden)
    result = native_review.handshake(handshake_request())
    assert set(result) == {"status", "contract", "dependencies", "evidence_root", "python"}
    assert result["status"] == "compatible"
    assert result["contract"] == handshake_request()
    names = ("native_review.py", "request.py", "packet.py", "envelope.py", "envelope_schema.py", "result.py")
    assert result["dependencies"] == {
        name: hashlib.sha256((exported_skill / name).read_bytes()).hexdigest() for name in names}
    assert result["evidence_root"] == str(native_review.result_store.default_store_root())
    assert result["python"] == {"executable": sys.executable, "version": list(sys.version_info[:3])}


def test_handshake_accepts_supported_requirements_and_advertises_future_capabilities(monkeypatch):
    monkeypatch.setattr(native_review, "_NATIVE_CAPABILITIES", native_review._NATIVE_CAPABILITIES + ("future-capability",))
    request = handshake_request()
    request["capabilities"] = ["host-failure-reason", "canonical-grading"]
    result = native_review.handshake(request)
    assert result["status"] == "compatible"
    assert result["contract"]["capabilities"] == handshake_request()["capabilities"] + ["future-capability"]


@pytest.mark.parametrize("change,reason", [
    ({"protocol": None}, "HANDSHAKE_PROTOCOL_NOT_TEXT"),
    ({"protocol": ""}, "HANDSHAKE_PROTOCOL_EMPTY"),
    ({"protocol": "ash-discriminator-native/2"}, "INCOMPATIBLE_NATIVE_PROTOCOL"),
    ({"schemas": []}, "HANDSHAKE_SCHEMAS_NOT_OBJECT"),
    ({"schemas": {"prepared": 1, "observation": 1}}, "HANDSHAKE_SCHEMAS_INVALID_FIELDS"),
    ({"schemas": {"prepared": 1, "observation": 1, "result": 1, "future": 1}}, "HANDSHAKE_SCHEMAS_INVALID_FIELDS"),
    ({"schemas": {"prepared": True, "observation": 1, "result": 1}}, "HANDSHAKE_SCHEMA_VERSION_NOT_INTEGER"),
    ({"schemas": {"prepared": 1, "observation": 1.0, "result": 1}}, "HANDSHAKE_SCHEMA_VERSION_NOT_INTEGER"),
    ({"schemas": {"prepared": 1, "observation": 1, "result": "1"}}, "HANDSHAKE_SCHEMA_VERSION_NOT_INTEGER"),
    ({"schemas": {"prepared": 1, "observation": 1, "result": 2}}, "INCOMPATIBLE_NATIVE_SCHEMAS"),
    ({"capabilities": "canonical-grading"}, "HANDSHAKE_CAPABILITIES_MUST_BE_NONEMPTY_ARRAY"),
    ({"capabilities": []}, "HANDSHAKE_CAPABILITIES_MUST_BE_NONEMPTY_ARRAY"),
    ({"capabilities": [None]}, "HANDSHAKE_CAPABILITY_NOT_TEXT"),
    ({"capabilities": [{}]}, "HANDSHAKE_CAPABILITY_NOT_TEXT"),
    ({"capabilities": [""]}, "HANDSHAKE_CAPABILITY_EMPTY"),
    ({"capabilities": ["\ud800"]}, "HANDSHAKE_CAPABILITY_NOT_UTF8"),
    ({"capabilities": ["canonical-grading", "canonical-grading"]}, "HANDSHAKE_DUPLICATE_CAPABILITY"),
    ({"capabilities": ["unsupported-capability"]}, "UNSUPPORTED_NATIVE_CAPABILITY"),
    ({"extra": True}, "HANDSHAKE_INPUT_INVALID_FIELDS"),
])
def test_handshake_refuses_malformed_or_incompatible_requirements(change, reason):
    request = handshake_request()
    request.update(change)
    result = native_review.handshake(request)
    assert result["status"] == "refused"
    assert reason in result["reason"]
    assert (result["compact"]["status"], result["compact"]["exitCode"], result["compact"]["verdict"]) == ("tooling-failure", 1, None)
    assert reason in result["compact"]["summary"]
    assert reason in result["delivery"]["tool_findings"][0]["body"]
    assert result["delivery"]["review"] is None
    assert "contract" not in result and "dependencies" not in result


@pytest.mark.parametrize("value", [None, [], {}, {"protocol": "ash-discriminator-native/1"}])
def test_handshake_refuses_missing_request_shape(value):
    result = native_review.handshake(value)
    assert result["status"] == "refused"
    assert "HANDSHAKE_INPUT_" in result["reason"]
    assert (result["compact"]["exitCode"], result["delivery"]["verdict"]) == (1, None)


@pytest.mark.parametrize("raw,reason", [
    (None, None),
    ('{"protocol":"ash-discriminator-native/1","protocol":"ash-discriminator-native/1","schemas":{},"capabilities":[]}', "duplicate"),
    ('{"protocol":"ash-discriminator-native/1","schemas":{"prepared":NaN,"observation":1,"result":1},"capabilities":[]}', "NaN"),
])
def test_handshake_cli_uses_strict_existing_json_transport(exported_skill, tmp_path, raw, reason):
    input_path, output_path = tmp_path / "input.json", tmp_path / "output.json"
    input_path.write_text(json.dumps(handshake_request()) if raw is None else raw, encoding="utf-8")
    completed = subprocess.run([sys.executable, "-B", str(exported_skill / "native_review.py"),
                                "handshake", "--input", str(input_path), "--output", str(output_path)],
                               capture_output=True, text=True, check=False)
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == ""
    result = json.loads(output_path.read_text(encoding="utf-8"))
    if reason is None:
        assert result["status"] == "compatible"
        assert result["contract"] == handshake_request()
    else:
        assert result["status"] == "refused"
        assert reason.casefold() in result["reason"].casefold()
        assert (result["compact"]["exitCode"], result["delivery"]["verdict"]) == (1, None)


@pytest.mark.parametrize("name", ["native_review.py", "request.py", "packet.py", "envelope.py", "envelope_schema.py", "result.py"])
@pytest.mark.parametrize("when", ["after-import", "during-handshake"])
def test_handshake_rejects_code_changed_in_helper_process(exported_skill, tmp_path, name, when):
    runtime = tmp_path / "isolated-runtime"
    shutil.copytree(exported_skill, runtime)
    script = '''import json, pathlib, sys
sys.path.insert(0, sys.argv[1])
import native_review
request = json.loads(sys.argv[2])
before = native_review.handshake(request)
target = pathlib.Path(sys.argv[1]) / sys.argv[3]
def change():
    target.write_bytes(target.read_bytes() + b"\\n# Synthetic code change after imports.\\n")
if sys.argv[4] == "after-import":
    change()
else:
    original = native_review._host_metadata
    def metadata():
        native_review._host_metadata = original
        value = original()
        change()
        return value
    native_review._host_metadata = metadata
after = native_review.handshake(request)
print(json.dumps({"before": before, "after": after}))
'''
    completed = subprocess.run([sys.executable, "-I", "-B", "-c", script, str(runtime),
                                json.dumps(handshake_request()), name, when],
                               capture_output=True, text=True, check=False)
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result["before"]["status"] == "compatible"
    refused = result["after"]
    assert refused["status"] == "refused"
    assert "LOADED_CODE_CHANGED: " + name in refused["reason"]
    assert (refused["compact"]["exitCode"], refused["delivery"]["verdict"]) == (1, None)


@pytest.mark.parametrize("fault", ["snapshot-unavailable", "external-module"])
def test_handshake_retains_loaded_code_security_gates(monkeypatch, tmp_path, fault):
    if fault == "snapshot-unavailable":
        monkeypatch.setattr(native_review, "_CODE_LOAD_ERROR", "synthetic unreadable code")
        reason = "CODE_SNAPSHOT_UNAVAILABLE"
    else:
        monkeypatch.setattr(native_review.request, "__file__", str(tmp_path / "request.py"))
        reason = "EXTERNAL_CANONICAL_MODULE: request.py"
    result = native_review.handshake(handshake_request())
    assert result["status"] == "refused"
    assert reason in result["reason"]
    assert (result["compact"]["exitCode"], result["delivery"]["verdict"]) == (1, None)


@pytest.mark.parametrize("verdict,severity,category,code,delivered,loop", [
    ("approve", None, None, 0, "approve", False),
    ("approve", "high", "stability", 4, "fail", True),
    ("approve", "high", "usability", 2, "needs-attention", False),
    ("approve", "medium", "stability", 2, "needs-attention", False),
    ("approve", "critical", "usability", 4, "fail", True),
    ("needs-attention", None, None, 2, "needs-attention", False),
    ("fail", None, None, 4, "fail", False),
])
def test_canonical_consequence(prepared, verdict, severity, category, code, delivered, loop):
    rows = [] if severity is None else [finding(severity, category)]
    result = finish(prepared, document(verdict=verdict, findings=rows))
    compact = result["compact"]
    assert compact["status"] == "reviewed"
    assert (compact["exitCode"], compact["verdict"], compact["loop_should_continue"]) == (code, delivered, loop)
    assert result["delivery"]["review"]["findings"] == rows


_VALID_ANCHORS = [
    {"file": None, "line_start": None, "line_end": None},
    {"file": "source.py", "line_start": None, "line_end": None},
    {"file": "source.py", "line_start": 1, "line_end": 2},
]
_INVALID_ANCHORS = [
    {"file": "", "line_start": None, "line_end": None},
    {"file": None, "line_start": 1, "line_end": 2},
    {"file": "source.py", "line_start": None, "line_end": 2},
    {"file": "source.py", "line_start": 1, "line_end": None},
    {"file": "source.py", "line_start": 0, "line_end": 2},
    {"file": "source.py", "line_start": 1, "line_end": 0},
    {"file": "source.py", "line_start": -1, "line_end": 2},
    {"file": "source.py", "line_start": 1.5, "line_end": 2},
    {"file": "source.py", "line_start": True, "line_end": 2},
]


def anchored_document(anchor):
    row = {**finding("high", "stability"), **anchor}
    return document(findings=[row])


def test_claude_schema_strict_compilation_preserves_anchor_states():
    """Compile the actual CLI schema offline, with a failing strictTypes control."""
    bun = shutil.which("bun")
    assert bun is not None, "Bun is required for discriminator schema compilation tests"
    command = claude_reviewer.build_command("claude", envelope_schema.ENVELOPE_SCHEMA)
    schema = json.loads(command[command.index("--json-schema") + 1])
    runner = r'''
const fs = require('node:fs');
const imported = require(process.env.DISCRIMINATOR_TEST_AJV || 'ajv');
const Ajv = imported.default || imported;
const ajv = new Ajv({strict: true, strictTypes: true, allErrors: true});
let rejected = null;
try { ajv.compile({type: 'null', minLength: 1}); }
catch (error) { rejected = String(error); }
if (!rejected || !rejected.includes('strictTypes')) {
  throw new Error('Ajv 8 with active strictTypes is required for this boundary test');
}
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const validate = ajv.compile(input.schema);
let valid = 0, invalid = 0;
for (const row of input.cases) {
  const accepted = validate(row.document);
  if (accepted !== row.expected) {
    throw new Error(JSON.stringify({row, errors: validate.errors}));
  }
  if (accepted) valid++; else invalid++;
}
console.log(JSON.stringify({valid, invalid}));
'''
    cases = ([{"document": anchored_document(anchor), "expected": True} for anchor in _VALID_ANCHORS]
             + [{"document": anchored_document(anchor), "expected": False} for anchor in _INVALID_ANCHORS])
    completed = subprocess.run([bun, "--eval", runner], input=json.dumps({"schema": schema, "cases": cases}),
                               text=True, capture_output=True, timeout=30)
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == {"valid": len(_VALID_ANCHORS), "invalid": len(_INVALID_ANCHORS)}


@pytest.mark.parametrize("anchor", _VALID_ANCHORS)
def test_valid_anchor_keeps_canonical_blocking_floor(prepared, anchor):
    doc = anchored_document(anchor)
    result = finish(prepared, doc)
    assert (result["compact"]["exitCode"], result["compact"]["verdict"]) == (4, "fail")
    assert result["delivery"]["review"]["findings"] == doc["findings"]


@pytest.mark.parametrize("anchor", _INVALID_ANCHORS + [
    {"file": "source.py", "line_start": 2, "line_end": 1},
    {"file": None, "line_start": None, "line_end": None, "evidence_quote": ""},
])
def test_invalid_anchor_still_refuses_review(prepared, anchor):
    result = finish(prepared, anchored_document(anchor))
    assert (result["compact"]["exitCode"], result["compact"]["verdict"]) == (1, None)
    assert result["compact"]["status"] == "tooling-failure"


def test_later_approval_cannot_erase_blocker(prepared):
    earlier = document(findings=[finding("high", "stability")])
    raw = json.dumps(earlier) + "\n" + json.dumps(document())
    result = native_review.finish(operation(prepared, raw))
    assert (result["compact"]["exitCode"], result["compact"]["verdict"]) == (4, "fail")
    assert result["compact"]["must_fix_high_count"] == 1


@pytest.mark.parametrize("fault", ["fenced", "duplicate", "NaN", "Infinity", "overflow", "extra", "missing", "class", "truncated", "empty-summary", "empty-output", "malformed"])
def test_bad_model_document_is_no_review(prepared, fault):
    doc = document()
    raw = json.dumps(doc)
    if fault == "fenced": raw = "```json\n" + raw + "\n```"
    elif fault == "duplicate": raw = '{"verdict":"approve",' + raw[1:]
    elif fault in ("NaN", "Infinity", "overflow"): raw = raw.replace('"Consumer review"', "1e999" if fault == "overflow" else fault)
    elif fault == "extra": doc["extra"] = True; raw = json.dumps(doc)
    elif fault == "missing": del doc["next_steps"]; raw = json.dumps(doc)
    elif fault == "class": doc["artifact_class_echo"] = None; raw = json.dumps(doc)
    elif fault == "truncated": raw = raw[:-1]
    elif fault == "empty-summary": doc["summary"] = ""; raw = json.dumps(doc)
    elif fault == "empty-output": raw = ""
    else: raw = "{broken}"
    value = operation(prepared, raw)
    result = native_review.finish(value)
    assert result["compact"]["status"] == "tooling-failure"
    assert (result["compact"]["exitCode"], result["compact"]["verdict"]) == (1, None)


@pytest.mark.parametrize("field,value", [
    ("cancelled", True), ("local_settled", False), ("publication_allowed", False),
    ("adapter_invocations", 0), ("adapter_invocations", 2),
    ("reason", "Failed raw output retention"),
])
def test_execution_failure_cannot_approve(prepared, field, value):
    invocation = operation(prepared, json.dumps(document()))
    invocation["observation"][field] = value
    result = native_review.finish(invocation)
    assert (result["compact"]["exitCode"], result["compact"]["verdict"]) == (1, None)


@pytest.mark.parametrize("raw", ["", json.dumps(document())])
@pytest.mark.parametrize("exact", [False, True])
def test_host_packet_capacity_reason_survives_without_terminal(prepared, raw, exact):
    invocation = operation(prepared, raw)
    budget = {"contextWindow": 2000, "maxTokens": 1000, "fits": False,
              "tokens": 1001, "budget": 1000, "exact": exact}
    invocation["observation"].update({"terminal": None, "adapter_invocations": 0,
                                      "publication_allowed": False, "local_settled": False,
                                      "reason": "PACKET_TOO_LARGE", "input_budget": budget})
    result = native_review.finish(invocation)
    compact, delivery = result["compact"], result["delivery"]
    assert (compact["status"], compact["exitCode"], compact["verdict"]) == ("too-large", 1, None)
    assert "PACKET_TOO_LARGE" in compact["summary"]
    assert "PACKET_TOO_LARGE" in delivery["tool_findings"][0]["body"]
    assert "TERMINAL_NOT_OBJECT" not in compact["summary"]
    assert all(compact[key] is None for key in ("reviewer_provider", "reviewer_model", "reviewer_api"))
    assert delivery["verdict"] is None and delivery["review"] is None
    assert delivery["status"] == "too-large" and delivery["fault"] == "caller-or-environment"
    body = delivery["tool_findings"][0]["body"]
    assert json.JSONDecoder().raw_decode(body[body.index("{"):])[0] == budget
    for value in ("tokens=1001", "budget=1000", "exact=" + str(exact).lower()):
        assert value in compact["summary"]


@pytest.mark.parametrize("change", [
    {"tokens": True}, {"exact": None}, {"fits": True}, {"tokens": 1000},
    {"budget": 1001}, {"contextWindow": 2**53},
])
def test_capacity_refusal_does_not_publish_unvalidated_budget(prepared, change):
    invocation = operation(prepared, "")
    budget = {"contextWindow": 2000, "maxTokens": 1000, "fits": False,
              "tokens": 1001, "budget": 1000, "exact": True, **change}
    invocation["observation"].update({"terminal": None, "adapter_invocations": 0,
                                      "publication_allowed": False, "reason": "PACKET_TOO_LARGE",
                                      "input_budget": budget})
    result = native_review.finish(invocation)
    assert (result["compact"]["status"], result["compact"]["exitCode"], result["compact"]["verdict"]) == ("tooling-failure", 1, None)
    assert result["delivery"]["fault"] == "unknown-review-state"


@pytest.mark.parametrize("change", [{"adapter_invocations": 1}, {"terminal": {}}, {"publication_allowed": True}])
def test_capacity_refusal_cannot_reclassify_possible_reviewer_execution(prepared, change):
    invocation = operation(prepared, "")
    invocation["observation"].update({"terminal": None, "adapter_invocations": 0, "publication_allowed": False,
                                      "reason": "PACKET_TOO_LARGE", "input_budget": {
                                          "contextWindow": 2000, "maxTokens": 1000, "fits": False,
                                          "tokens": 1001, "budget": 1000, "exact": True}, **change})
    result = native_review.finish(invocation)
    assert (result["compact"]["status"], result["compact"]["exitCode"], result["compact"]["verdict"]) == ("tooling-failure", 1, None)
    assert "PACKET_TOO_LARGE_NOT_PRE_INFERENCE" in result["compact"]["summary"]


def test_cancellation_precedes_host_failure_and_absent_terminal(prepared):
    invocation = operation(prepared, json.dumps(document()))
    invocation["observation"].update({"cancelled": True, "reason": "PACKET_TOO_LARGE", "terminal": None})
    result = native_review.finish(invocation)
    assert (result["compact"]["exitCode"], result["compact"]["verdict"]) == (1, None)
    assert "INVOCATION_CANCELLED" in result["compact"]["summary"]
    assert "PACKET_TOO_LARGE" not in result["compact"]["summary"]


@pytest.mark.parametrize("fault,reason", [
    ("dependencies", "STALE_OR_FORGED_DEPENDENCIES"),
    ("packet", "PACKET_DIGEST_MISMATCH"),
    ("observation-binding", "STALE_OR_MISMATCHED_HOST_BINDING"),
])
def test_host_failure_cannot_mask_invalid_prepared_or_observation_binding(prepared, fault, reason):
    invocation = operation(deepcopy(prepared), "")
    invocation["observation"].update({"reason": "PACKET_TOO_LARGE", "terminal": None})
    if fault == "dependencies":
        invocation["prepared"]["dependencies"]["packet.py"] = "0" * 64
    elif fault == "packet":
        invocation["prepared"]["packet_text"] += "forged packet"
    else:
        invocation["observation"]["binding"]["tool_call_id"] = "another-call"
    result = native_review.finish(invocation)
    assert (result["compact"]["exitCode"], result["compact"]["verdict"]) == (1, None)
    assert reason in result["compact"]["summary"]
    assert "PACKET_TOO_LARGE" not in result["compact"]["summary"]


@pytest.mark.parametrize("reason", [False, 1, [], {}, "", "   "])
def test_malformed_host_failure_reason_cannot_approve(prepared, reason):
    invocation = operation(prepared, json.dumps(document()))
    invocation["observation"]["reason"] = reason
    result = native_review.finish(invocation)
    assert (result["compact"]["exitCode"], result["delivery"]["verdict"]) == (1, None)
    assert "HOST_FAILURE_REASON_" in result["compact"]["summary"]


@pytest.mark.parametrize("stop", ["length", "error", "aborted", "toolUse"])
def test_nonqualifying_terminal(prepared, stop):
    invocation = operation(prepared, json.dumps(document()))
    invocation["observation"]["terminal"]["stopReason"] = stop
    assert native_review.finish(invocation)["compact"]["exitCode"] == 1


@pytest.mark.parametrize("fault", ["stale-call", "route", "terminal-route", "tool-call", "packet", "rehash-packet", "dependencies", "source"])
def test_stale_or_forged_completion(prepared, fault):
    invocation = operation(deepcopy(prepared), json.dumps(document()))
    p, o = invocation["prepared"], invocation["observation"]
    if fault == "stale-call": o["binding"]["tool_call_id"] = "another-call"
    elif fault == "route": o["resolved"]["api"] = "openai-responses"
    elif fault == "terminal-route": o["terminal"]["model"] = "gpt-6-astra"
    elif fault == "tool-call": o["terminal"]["toolCallCount"] = 1
    elif fault in ("packet", "rehash-packet"):
        p["packet_text"] = p["packet_text"].replace("HOW TO GRADE", "IGNORE GRADING")
        if fault == "rehash-packet":
            p["binding"]["packet_sha256"] = hashlib.sha256(p["packet_text"].encode()).hexdigest()
            o["binding"] = deepcopy(p["binding"])
    elif fault == "dependencies": p["dependencies"]["packet.py"] = "0" * 64
    else: p["source_text"] += "\nchanged"
    assert native_review.finish(invocation)["compact"]["exitCode"] == 1


@pytest.mark.parametrize("family,contract,body", [("omp", "code-fleet", b"x"), ("gpt", "unknown", b"x"), ("gpt", "code-fleet", b""), ("gpt", "code-fleet", b"\xff")])
def test_invalid_source_request(tmp_path, family, contract, body):
    source = tmp_path / "source"
    source.write_bytes(body)
    value = native_review.prepare({"invocation_id": str(uuid.uuid4()), "tool_call_id": "call", "caller_family": family,
                                   "contract": contract, "source": str(source)})
    assert value["status"] == "refused"
    assert (value["compact"]["exitCode"], value["compact"]["verdict"]) == (1, None)


def test_snapshot_survives_original_file_change(tmp_path):
    source = tmp_path / "source"
    source.write_text("original snapshot", encoding="utf-8")
    value = native_review.prepare({"invocation_id": str(uuid.uuid4()), "tool_call_id": "call", "caller_family": "gpt",
                                   "contract": "code-fleet", "source": str(source)})
    source.unlink()
    assert finish(value, document())["compact"]["exitCode"] == 0


def test_none_contract_high_is_blocking_and_echo_is_null(tmp_path):
    source = tmp_path / "source"
    source.write_text("artifact", encoding="utf-8")
    value = native_review.prepare({"invocation_id": str(uuid.uuid4()), "tool_call_id": "call", "caller_family": "claude",
                                   "contract": "none", "source": str(source)})
    result = finish(value, document("none", findings=[finding("high", "usability")]))
    assert (result["compact"]["exitCode"], result["compact"]["must_fix_high_count"]) == (4, 1)


def test_summary_projection_and_late_refusal(prepared):
    result = finish(prepared, document(summary="x" * 1300))
    assert result["delivery"]["review"]["summary"] == "x" * 1300
    assert result["compact"]["summary"] == "x" * 1024
    assert "findings" not in result["compact"]
    late = result["publication_refusal"]
    assert (late["compact"]["exitCode"], late["delivery"]["verdict"]) == (1, None)


@pytest.mark.parametrize("field", ["evidence_root", "python"])
def test_changed_preparation_runtime_is_not_qualified(prepared, field):
    value = deepcopy(prepared)
    value[field] = "unrelated root" if field == "evidence_root" else {"executable": "another-python", "version": [3, 8, 0]}
    assert finish(value, document())["compact"]["exitCode"] == 1



@pytest.mark.parametrize("layout,declaration_state", [
    (layout, state)
    for layout in ("harness/.omp/shared/extensions/discriminator",
                   ".omp/shared/extensions/discriminator")
    for state in ("present", "missing", "missing-command", "relative-python", "missing-python",
                  "directory-python", "missing-machine", "wrong-machine", "missing-profile",
                  "relative-profile", "wrong-profile", "machine-casefold", "no-profiles")
] + [
    (layout, "present")
    for layout in ("harness/.omp/profiles/legacy-profile/agent/extensions/discriminator",
                   "harness/.omp/agent/extensions/discriminator",
                   ".omp/profiles/legacy-profile/agent/extensions/discriminator",
                   ".omp/agent/extensions/discriminator",
                   "source/discriminator/harness/omp")
])
def test_extension_input_layouts(exported_skill, tmp_path, layout, declaration_state):
    """Execute the actual resolver, without importing OMP or dispatching any review."""
    bun = shutil.which("bun")
    assert bun is not None, "Bun is required for discriminator extension layout tests"
    candidate = exported_skill.parents[3]
    original = candidate / "harness/.omp/shared/extensions/discriminator/index.ts"
    directory = tmp_path / layout
    directory.mkdir(parents=True)
    extension = directory / "index.ts"
    shutil.copyfile(original, extension)
    supported = layout in {"harness/.omp/shared/extensions/discriminator",
                           ".omp/shared/extensions/discriminator"}
    target_home = tmp_path / "harness" if layout.startswith("harness/") else tmp_path
    home = tmp_path / "unrelated-home"
    home.mkdir()
    declaration = target_home / ".agents/agent-skills/runtime.json"
    command = ("python" if declaration_state == "relative-python" else
               str(tmp_path / "missing-python") if declaration_state == "missing-python" else
               str(target_home) if declaration_state == "directory-python" else sys.executable)
    value = {"machine": "fixture-host", "tokens": {"__USERPROFILE__": str(target_home), "__OMP_PYTHON__": command}}
    if declaration_state == "missing-command":
        value["tokens"].pop("__OMP_PYTHON__")
    elif declaration_state == "missing-machine":
        value.pop("machine")
    elif declaration_state == "wrong-machine":
        value["machine"] = "another-machine"
    elif declaration_state == "machine-casefold":
        value["machine"] = "FIXTURE-HOST"
    elif declaration_state == "missing-profile":
        value["tokens"].pop("__USERPROFILE__")
    elif declaration_state == "relative-profile":
        value["tokens"]["__USERPROFILE__"] = "harness"
    elif declaration_state == "wrong-profile":
        value["tokens"]["__USERPROFILE__"] = str(home)
    if declaration_state != "missing":
        declaration.parent.mkdir(parents=True, exist_ok=True)
        declaration.write_text(json.dumps(value), encoding="utf-8")
    # Neither a different HOME's runtime nor former staging/profile declarations may rescue it.
    ambient = home / ".agents/agent-skills/runtime.json"
    ambient.parent.mkdir(parents=True)
    ambient.write_text(json.dumps({"machine": "fixture-host", "tokens": {
        "__USERPROFILE__": str(home), "__OMP_PYTHON__": sys.executable}}), encoding="utf-8")
    staged = tmp_path / "config/machines/fixture-host.json"
    staged.parent.mkdir(parents=True)
    staged.write_text(json.dumps({"tokens": {"__OMP_PYTHON__": sys.executable}}), encoding="utf-8")
    if declaration_state != "no-profiles":
        for fallback in (home / ".omp/agent/mcp.json", target_home / ".omp/agent/mcp.json",
                         target_home / ".omp/profiles/legacy-profile/agent/mcp.json",
                         target_home / ".omp/profiles/arbitrary-future-profile/agent/mcp.json"):
            fallback.parent.mkdir(parents=True, exist_ok=True)
            fallback.write_text(json.dumps({"mcpServers": {"omp-relay": {"command": sys.executable}}}), encoding="utf-8")
    else:
        assert not (target_home / ".omp/profiles").exists()
        assert not (target_home / ".omp/agent/mcp.json").exists()
    (home / ".agents/skills/discriminator").mkdir(parents=True)
    runner = r'''
const fs = require('node:fs');
const path = require('node:path');
const nodeOs = require('node:os');
const {createHash} = require('node:crypto');
const os = {...nodeOs, homedir: () => process.env.FIXTURE_HOME, hostname: () => 'fixture-host'};
const {readFileSync, realpathSync, statSync} = fs;
const extensionPath = process.env.FIXTURE_EXTENSION;
const digest = bytes => createHash('sha256').update(bytes).digest('hex');
const need = (condition, reason) => { if (!condition) throw new Error(reason); };
const source = readFileSync(extensionPath, 'utf8');
const begin = source.indexOf('function installedInputs()');
const end = source.indexOf('function retainPreparation(', begin);
if (begin < 0 || end < 0) throw new Error('Missing source resolver');
const helper = source.match(/^function nonempty\([^]*?^}/m);
if (!helper) throw new Error('Missing source nonempty helper');
const compiled = new Bun.Transpiler({loader: 'ts'}).transformSync(helper[0] + '\n' + source.slice(begin, end));
try {
  const inputs = eval(compiled + '\ninstalledInputs()');
  process.stdout.write(JSON.stringify(inputs));
} catch (error) {
  process.stderr.write(String(error.message));
  process.exitCode = 1;
}
'''
    environment = {**os.environ, "FIXTURE_HOME": str(home), "FIXTURE_EXTENSION": str(extension),
                   "HOME": str(home), "USERPROFILE": str(home)}
    result = subprocess.run([bun, "--eval", runner], cwd=tmp_path, env=environment,
                            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30)
    reasons = {"missing-command": "MACHINE_PYTHON_DECLARATION_MISSING",
               "relative-python": "MACHINE_PYTHON_DECLARATION_MISSING",
               "directory-python": "MACHINE_PYTHON_EXECUTABLE_MISSING",
               "missing-machine": "RUNTIME_MACHINE_MISMATCH", "wrong-machine": "RUNTIME_MACHINE_MISMATCH",
               "missing-profile": "RUNTIME_PROFILE_MISMATCH", "relative-profile": "RUNTIME_PROFILE_MISMATCH",
               "wrong-profile": "RUNTIME_PROFILE_MISMATCH"}
    if not supported:
        assert result.returncode != 0 and "UNSUPPORTED_EXTENSION_LAYOUT" in result.stderr
    elif declaration_state in {"missing", "missing-python"}:
        assert result.returncode != 0
    elif declaration_state in reasons:
        assert result.returncode != 0 and reasons[declaration_state] in result.stderr
    else:
        assert result.returncode == 0, result.stderr
        observed = json.loads(result.stdout)
        expected_skill = target_home / ".agents/skills/discriminator"
        assert Path(observed["skillRoot"]) == expected_skill
        assert Path(observed["python"]) == Path(sys.executable).resolve()
        assert Path(observed["declaration"]["path"]) == declaration
        assert observed["declaration"]["sha256"] == hashlib.sha256(declaration.read_bytes()).hexdigest()


@pytest.fixture
def image_prepared(tmp_path):
    # Genuine one-pixel PNG, not an OCR/description stand-in; these are finite-helper tests only.
    import base64
    data = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aXxkAAAAASUVORK5CYII=")
    image = tmp_path / "pixel.png"
    image.write_bytes(data)
    source = tmp_path / "source.txt"
    source.write_text("Synthetic boundary artifact", encoding="utf-8")
    request = {"invocation_id": str(uuid.uuid4()), "tool_call_id": "image-boundary", "caller_family": "gpt",
               "contract": "code-fleet", "source": str(source),
               "images": [{"path": str(image), "sha256": hashlib.sha256(data).hexdigest(), "media_type": "image/png"}]}
    value = native_review.prepare(request)
    assert value["status"] == "prepared", value
    return request, value


@pytest.mark.parametrize("fault", ["hash", "media", "missing", "directory", "duplicate", "empty", "extra"])
def test_image_prepare_refuses_invalid_explicit_authority(image_prepared, tmp_path, fault):
    request, _ = deepcopy(image_prepared)
    image = request["images"][0]
    if fault == "hash": image["sha256"] = "0" * 64
    elif fault == "media": image["media_type"] = "image/svg+xml"
    elif fault == "missing": image["path"] = str(tmp_path / "missing.png")
    elif fault == "directory": image["path"] = str(tmp_path)
    elif fault == "duplicate": request["images"].append(deepcopy(image))
    elif fault == "empty": request["images"] = []
    else: image["url"] = "https://example.invalid/not-read"
    result = native_review.prepare(request)
    assert result["status"] == "refused"
    assert (result["compact"]["exitCode"], result["compact"]["verdict"]) == (1, None)


@pytest.mark.parametrize("fault", ["bytes", "data", "path", "media", "inventory", "unbound", "packet"])
def test_image_snapshot_and_packet_tamper_refuse(image_prepared, fault):
    _, prepared = deepcopy(image_prepared)
    image = prepared["images"][0]
    if fault == "bytes": image["bytes"] += 1
    elif fault == "data": image["data"] = "dGFtcGVyZWQ="
    elif fault == "path": image["path"] += "-different"
    elif fault == "media": image["media_type"] = "image/jpeg"
    elif fault == "inventory": prepared["binding"]["image_inventory_sha256"] = "0" * 64
    elif fault == "unbound": del prepared["binding"]["image_inventory_sha256"]
    else:
        prepared["packet_text"] += "\nforged image evidence"
        prepared["binding"]["packet_sha256"] = hashlib.sha256(prepared["packet_text"].encode()).hexdigest()
    with pytest.raises(native_review.NoReview):
        native_review._validate_prepared(prepared)


def test_images_never_qualify_with_text_only_host_observation(image_prepared):
    _, prepared = image_prepared
    invocation = operation(prepared, json.dumps(document()))
    result = native_review.finish(invocation)
    assert (result["compact"]["exitCode"], result["compact"]["verdict"]) == (1, None)


def test_image_snapshot_validation_never_rereads_original_paths(image_prepared):
    request, prepared = image_prepared
    Path(request["source"]).unlink()
    Path(request["images"][0]["path"]).unlink()
    binding, _ = native_review._validate_prepared(prepared)
    assert binding == prepared["binding"]
