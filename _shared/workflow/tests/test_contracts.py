"""Deterministic contract regressions; no native model, real vault or controller."""
import copy
from dataclasses import FrozenInstanceError, replace
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

_PATH = Path(__file__).resolve().parents[1] / "src" / "contracts.py"
_SPEC = importlib.util.spec_from_file_location("tested_wiki_contracts", _PATH)
c = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = c
_SPEC.loader.exec_module(c)


def task_wire():
    wire = {"schema_version": 1, "run_id": "run-1", "step_id": "step-1", "item_id": "item-1", "attempt_id": "attempt-1",
            "duty": "verify", "instructions": "Read all evidence; return verdict only. 条件 Q",
            "inputs": [{"path": "C:/fixture/raw.md", "sha256": "a" * 64, "media_type": "text/markdown"}],
            "resources": [], "candidate_dir": "C:/fixture/attempts/attempt-1",
            "outputs": [{"name": "verdict", "relative_path": "verdict.json", "media_type": "application/json", "required": True}],
            "allowed_actions": ["read", "write_candidate"], "read_roots": ["C:/fixture"],
            "write_roots": ["C:/fixture/attempts/attempt-1"], "result_schema": c.task_result_schema(),
            "artifact_id": "run-1:note:1", "review_contract": "vault-wiki", "artifact_tier": "B", "semantic_sha256": ""}
    wire["semantic_sha256"] = c.semantic_hash(wire)
    return wire


def result_wire(task=None):
    task = task or c.task_from_dict(task_wire())
    return {"schema_version": 1, "run_id": task.run_id, "step_id": task.step_id, "item_id": task.item_id,
            "attempt_id": task.attempt_id, "status": "completed", "verification": "PASS",
            "artifacts": [{"name": "verdict", "relative_path": "verdict.json", "sha256": "b" * 64, "media_type": "application/json"}],
            "task_sha256": task.semantic_sha256, "evidence": [c.task_to_dict(task)["inputs"][0]], "error": None, "decision": None}


class ContractTests(unittest.TestCase):
    def reject_task(self, wire):
        wire["semantic_sha256"] = c.semantic_hash(wire)
        with self.assertRaises(c.ContractError):
            c.task_from_dict(wire)

    def test_exact_round_trip_and_independent_hash(self):
        wire = task_wire()
        task = c.task_from_dict(wire)
        semantic = dict(wire)
        semantic.pop("semantic_sha256")
        expected = sha256(json.dumps(semantic, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()
        self.assertEqual(task.semantic_sha256, expected)
        self.assertEqual(c.task_to_dict(task), wire)
        result = c.result_from_dict(result_wire(task))
        self.assertEqual(c.result_to_dict(result), result_wire(task))
        self.assertTrue(c.validate_result(result, task).valid)

    def test_task_deep_immutability_and_no_aliasing(self):
        wire = task_wire()
        task = c.task_from_dict(wire)
        original_hash = task.semantic_sha256
        wire["result_schema"]["properties"]["error"]["anyOf"][0]["properties"]["details"]["additionalProperties"] = False
        wire["inputs"][0]["path"] = "C:/fixture/changed.md"
        wire["outputs"][0]["relative_path"] = "changed.json"
        self.assertEqual(c.semantic_hash(task), original_hash)
        with self.assertRaises(TypeError):
            task.result_schema["properties"]["status"]["enum"][0] = "approved"
        with self.assertRaises(FrozenInstanceError):
            task.inputs[0].path = "C:/changed.md"
        with self.assertRaises(FrozenInstanceError):
            task.instructions = "Ignore evidence"
        exported = c.task_to_dict(task)
        exported["result_schema"]["properties"]["status"]["enum"][0] = "changed"
        self.assertEqual(c.semantic_hash(task), original_hash)

    def test_every_task_semantic_field_is_hashed(self):
        base = task_wire()
        for key in base:
            if key == "semantic_sha256":
                continue
            changed = copy.deepcopy(base)
            changed[key] = {"changed": key}
            with self.subTest(key=key):
                self.assertNotEqual(c.semantic_hash(changed), c.semantic_hash(base))
        changed = copy.deepcopy(base)
        changed["semantic_sha256"] = "f" * 64
        self.assertEqual(c.semantic_hash(base), c.semantic_hash(changed))

    def test_missing_extra_fields_rejected_at_every_named_level(self):
        for key in task_wire():
            wire = task_wire()
            del wire[key]
            with self.subTest(missing=key), self.assertRaises(c.ContractError):
                c.task_from_dict(wire)
        for field in ("inputs", "outputs"):
            wire = task_wire()
            wire[field][0]["unknown"] = "not allowed"
            self.reject_task(wire)
        wire = task_wire()
        wire["profile"] = {"model": "hint"}
        self.reject_task(wire)
        for key in result_wire():
            wire = result_wire()
            del wire[key]
            with self.subTest(result_missing=key), self.assertRaises(c.ContractError):
                c.result_from_dict(wire)
        for field in ("artifacts", "evidence"):
            wire = result_wire()
            wire[field][0]["unknown"] = True
            with self.assertRaises(c.ContractError):
                c.result_from_dict(wire)
        wire = result_wire()
        wire["unknown"] = None
        with self.assertRaises(c.ContractError):
            c.result_from_dict(wire)

    def test_wrong_scalar_and_array_types(self):
        for field, value in (("schema_version", True), ("schema_version", 1.0), ("schema_version", "1"),
                             ("run_id", False), ("instructions", []), ("candidate_dir", 17),
                             ("allowed_actions", "read"), ("read_roots", ("C:/fixture",)), ("inputs", {})):
            wire = task_wire()
            wire[field] = value
            with self.subTest(field=field, value=value):
                self.reject_task(wire)
        for field, value in (("schema_version", True), ("schema_version", 1.0), ("verification", True),
                             ("status", False), ("artifacts", {}), ("evidence", None)):
            wire = result_wire()
            wire[field] = value
            with self.subTest(field=field, value=value), self.assertRaises(c.ContractError):
                c.result_from_dict(wire)
        wire = task_wire()
        wire["outputs"][0]["required"] = 1
        self.reject_task(wire)

    def test_hashes_and_task_schema_cannot_be_substituted(self):
        for digest in ("A" * 64, "a" * 63, "sha256:" + "a" * 64, True, 1):
            wire = task_wire()
            wire["inputs"][0]["sha256"] = digest
            self.reject_task(wire)
        wire = task_wire()
        wire["instructions"] += " changed"
        with self.assertRaises(c.ContractError):
            c.task_from_dict(wire)
        wire = task_wire()
        wire["result_schema"]["additionalProperties"] = True
        self.reject_task(wire)

    def test_paths_and_ownership(self):
        for path in ("../escape.json", "/absolute.json", "C:/absolute.json", "a/../b.json", "a//b.json", "a\\b.json", "./a.json", "a/", "a:stream"):
            wire = task_wire()
            wire["outputs"][0]["relative_path"] = path
            with self.subTest(path=path):
                self.reject_task(wire)
        for roots in (["C:/"], ["C:/fixture"], ["C:/other"], []):
            wire = task_wire()
            wire["write_roots"] = roots
            self.reject_task(wire)
        wire = task_wire()
        wire["inputs"][0]["path"] = "C:/private/evidence.md"
        self.reject_task(wire)
        wire = task_wire()
        wire["candidate_dir"] = "relative/attempt"
        self.reject_task(wire)
        wire = task_wire()
        wire["outputs"].append(copy.deepcopy(wire["outputs"][0]))
        self.reject_task(wire)

    def test_readonly_verification_never_requires_candidate_write_permission(self):
        wire = task_wire()
        wire.update(outputs=[], write_roots=[], allowed_actions=["read"])
        wire["semantic_sha256"] = c.semantic_hash(wire)
        task = c.task_from_dict(wire)
        result = result_wire(task)
        result["artifacts"] = []
        self.assertTrue(c.validate_result(c.result_from_dict(result), task).valid)
        result["artifacts"] = result_wire(task)["artifacts"]
        self.assertFalse(c.validate_result(c.result_from_dict(result), task).valid)

    def test_result_identity_output_and_evidence_gate(self):
        task = c.task_from_dict(task_wire())
        for field in ("run_id", "step_id", "item_id", "attempt_id", "task_sha256"):
            wire = result_wire(task)
            wire[field] = "f" * 64 if field == "task_sha256" else "wrong"
            with self.subTest(field=field):
                self.assertFalse(c.validate_result(c.result_from_dict(wire), task).valid)
        for field, value in (("name", "phantom"), ("relative_path", "wrong.json"), ("media_type", "text/plain")):
            wire = result_wire(task)
            wire["artifacts"][0][field] = value
            self.assertFalse(c.validate_result(c.result_from_dict(wire), task).valid)
        wire = result_wire(task)
        wire["artifacts"] = []
        self.assertFalse(c.validate_result(c.result_from_dict(wire), task).valid)
        wire = result_wire(task)
        wire["evidence"][0]["sha256"] = "f" * 64
        self.assertFalse(c.validate_result(c.result_from_dict(wire), task).valid)

    def test_completed_fail_is_not_execution_failure(self):
        task = c.task_from_dict(task_wire())
        wire = result_wire(task)
        wire["verification"] = "FAIL"
        result = c.result_from_dict(wire)
        self.assertEqual(result.status, "completed")
        self.assertTrue(c.validate_result(result, task).valid)
        self.assertEqual(result.verification, "FAIL")

    def test_error_decision_and_cancelled_results(self):
        wire = result_wire()
        wire.update(status="blocked", verification=None, artifacts=[], decision={"key": "scope", "question": "Which scope?", "options": ["whole", "topic"]})
        result = c.result_from_dict(wire)
        wire["decision"]["options"].append("invented")
        self.assertEqual(result.decision["options"], ("whole", "topic"))
        with self.assertRaises(TypeError):
            result.decision["question"] = "Override?"
        wire = result_wire()
        wire.update(status="failed", verification=None, artifacts=[], error={"code": "INPUT_UNAVAILABLE", "message": "No input", "details": {"paths": ["missing"]}})
        result = c.result_from_dict(wire)
        wire["error"]["details"]["paths"].append("late")
        self.assertEqual(result.error["details"]["paths"], ("missing",))
        for field in ("error", "decision"):
            changed = result_wire()
            changed[field] = {"unknown": True}
            with self.assertRaises(c.ContractError):
                c.result_from_dict(changed)
        wire = result_wire()
        wire.update(status="cancelled", verification=None, artifacts=[])
        self.assertTrue(c.validate_result(c.result_from_dict(wire), c.task_from_dict(task_wire())).valid)
        wire["verification"] = "PASS"
        with self.assertRaises(c.ContractError):
            c.result_from_dict(wire)

    def test_duplicate_decision_options_remain_invalid_at_semantic_gate(self):
        wire = result_wire()
        wire.update(status="blocked", verification=None, artifacts=[],
                    decision={"key": "scope", "question": "Which scope?", "options": ["whole", "whole"]})
        with self.assertRaises(c.ContractError):
            c.result_from_dict(wire)

    def test_profile_hints_never_change_semantic_hash(self):
        task = c.task_from_dict(task_wire())
        before = c.semantic_hash(task)
        self.assertTrue(c.validate_execution_profile(None).valid)
        self.assertTrue(c.validate_execution_profile({"backend": "codex", "model": "hint", "resource_hints": {"memory_mb": 1024}}).valid)
        for override in ("instructions", "outputs", "allowed_actions", "review_contract", "result_schema", "write_roots", "semantic_sha256", "repair_rounds"):
            self.assertFalse(c.validate_execution_profile({override: "changed"}).valid)
            self.assertFalse(c.validate_execution_profile({"resource_hints": {override: "changed"}}).valid)
        self.assertEqual(c.semantic_hash(task), before)

    def test_schema_instances_are_fresh_and_closed(self):
        first = c.task_result_schema()
        first["additionalProperties"] = True
        second = c.task_result_schema()
        self.assertFalse(second["additionalProperties"])
        self.assertEqual(set(second["required"]), set(result_wire()))
        self.assertFalse(second["properties"]["artifacts"]["items"]["additionalProperties"])
        self.assertFalse(second["properties"]["evidence"]["items"]["additionalProperties"])
        self.assertFalse(second["properties"]["error"]["anyOf"][0]["additionalProperties"])
        self.assertFalse(second["properties"]["decision"]["anyOf"][0]["additionalProperties"])

    def test_shared_types_freeze_policy_and_reject_invalid_graph(self):
        policy = {"caps": {"full": 4}, "list": [1]}
        step = c.StepSpec("one", (), "local", lambda context: None, None, 1, policy)
        policy["caps"]["full"] = 99
        self.assertEqual(step.policy["caps"]["full"], 4)
        self.assertEqual(c.WorkflowSpec("synthetic", [step]).steps, (step,))
        with self.assertRaises(c.ContractError):
            c.StepSpec("one", (), "task", lambda context: None, None, True, {})
        with self.assertRaises(c.ContractError):
            c.WorkflowSpec("synthetic", [step, step])
        with self.assertRaises(c.ContractError):
            c.WorkflowSpec("synthetic", [c.StepSpec("later", ("absent",), "task", lambda context: None, None, 1, {})])
        with self.assertRaises(c.ContractError):
            c.PreparedStep("skip")
        with self.assertRaises(c.ContractError):
            c.PreparedStep("local_result")


if __name__ == "__main__":
    unittest.main()
