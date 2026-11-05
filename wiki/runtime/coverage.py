"""Mechanical source-unit/topic coverage; never a semantic acceptance shortcut.

scope requires expected_pairs:[{unit_id,topic}] or units:[id,...] plus topic.
An exclusion must match an exact pair and scope_rule in scope.exclusions. The
controller supplies artifact_root (and capture_root if distinct) for current-byte
checks, not workers. These fields do not grant write authority.
"""
from __future__ import annotations

from pathlib import Path
from _workflow.contracts import ValidationResult
from .capture import capture_exclusions, contained_file, expected_units, file_bytes, issue, validate_capture


def scope_pairs(scope: dict) -> tuple[tuple[str, str], ...]:
    """Return explicit, ordered unit/topic membership; refuse duplicates/guessing."""
    if not isinstance(scope, dict):
        raise ValueError("Scope must be an object")
    pairs = scope.get("expected_pairs")
    if pairs is None:
        units, topic = scope.get("units"), scope.get("topic")
        if not isinstance(units, list) or not isinstance(topic, str) or not topic.strip():
            raise ValueError("Scope must bind expected_pairs or units plus topic")
        pairs = [{"unit_id": unit, "topic": topic} for unit in units]
    if not isinstance(pairs, list) or not pairs:
        raise ValueError("Expected pairs must be a nonempty array")
    result = []
    for pair in pairs:
        if not isinstance(pair, dict) or set(pair) != {"unit_id", "topic"}:
            raise ValueError("Each expected pair must contain unit_id and topic only")
        unit, topic = pair["unit_id"], pair["topic"]
        if not isinstance(unit, str) or not unit or not isinstance(topic, str) or not topic.strip():
            raise ValueError("Unit/topic identities must be nonempty strings")
        result.append((unit, topic))
    if len(set(result)) != len(result):
        raise ValueError("Duplicate unit/topic pair in scope")
    return tuple(result)


def validate_coverage(inventory: dict, coverage: dict, scope: dict) -> ValidationResult:
    errors = []
    try:
        pairs = scope_pairs(scope)
        root = Path(scope["artifact_root"])
        capture_root = Path(scope.get("capture_root", root))
        expected = expected_units(inventory, capture_root)
        ids = [unit["unit_id"] for unit in expected]
        if len(set(ids)) != len(ids) or set(unit for unit, _ in pairs) != set(ids):
            raise ValueError("Scope must account for every independently enumerated unit exactly by its declared topics")
        capture_result = validate_capture(inventory, capture_root)
        errors.extend(capture_result.errors)
        capture_excluded = capture_exclusions(inventory, expected)
        exclusions = scope.get("exclusions", [])
        if not isinstance(exclusions, list):
            raise ValueError("Scope exclusions must be an array")
        excluded = {}
        for exclusion in exclusions:
            if not isinstance(exclusion, dict):
                raise ValueError("Exclusion must bind an exact unit/topic/scope_rule")
            pair = (exclusion.get("unit_id"), exclusion.get("topic"))
            rule = exclusion.get("scope_rule")
            reason = exclusion.get("reason")
            if pair not in pairs or pair in excluded or not isinstance(rule, str) or not rule.strip() or not isinstance(reason, str) or not reason.strip():
                raise ValueError("Phantom, duplicate or unsupported scope exclusion")
            excluded[pair] = exclusion
        for unit, topic in pairs:
            if unit in capture_excluded:
                exclusion = excluded.get((unit, topic))
                if exclusion is None or exclusion["scope_rule"] != capture_excluded[unit]["scope_rule"]:
                    raise ValueError("Coverage must retain the exact partial capture exclusion")
    except (ValueError, OSError, TypeError, KeyError) as exc:
        return ValidationResult(False, (*errors, issue("COVERAGE_SCOPE_INVALID", str(exc))))
    return _validate_entries(coverage, pairs, root, excluded, errors)


def validate_producer_coverage(coverage: dict, scope: dict) -> ValidationResult:
    """Check one assigned note's sidecar without revalidating unrelated captures."""
    try:
        pairs = scope_pairs(scope)
        if len(pairs) != 1:
            raise ValueError("A producer owns exactly one assigned unit/topic")
        root = Path(scope["artifact_root"])
        note = contained_file(root, scope["note_path"])
    except (ValueError, OSError, TypeError, KeyError) as exc:
        return ValidationResult(False, (issue("COVERAGE_SCOPE_INVALID", str(exc)),))
    result = _validate_entries(coverage, pairs, root, {}, [])
    if not result.valid:
        return result
    refs = coverage["entries"][0]["artifact_refs"]
    if len(refs) != 1 or contained_file(root, refs[0]["path"]) != note:
        return ValidationResult(False, (issue("COVERAGE_ARTIFACT_INVALID", "Producer coverage must bind only its current declared note"),), result.diagnostics)
    return result


def _validate_entries(coverage, pairs, root, excluded, errors):
    if not isinstance(coverage, dict) or type(coverage.get("schema_version")) is not int or coverage["schema_version"] != 1:
        return ValidationResult(False, (*errors, issue("COVERAGE_SCOPE_INVALID", "Coverage requires schema_version 1")))
    entries = coverage.get("entries")
    if not isinstance(entries, list):
        return ValidationResult(False, (*errors, issue("COVERAGE_SCOPE_INVALID", "Coverage entries must be an array")))
    seen = set()
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict) or set(entry) != {"unit_id", "topic", "artifact_refs", "disposition", "reason", "scope_rule"}:
            errors.append(issue("COVERAGE_ENTRY_INVALID", "Entry must have the six declared coverage fields", index=index))
            continue
        pair = (entry["unit_id"], entry["topic"])
        if not all(isinstance(value, str) and value for value in pair):
            errors.append(issue("COVERAGE_ENTRY_INVALID", "Invalid unit/topic identity", index=index))
            continue
        if pair in seen:
            errors.append(issue("COVERAGE_DUPLICATE_PAIR", "Repeated unit/topic coverage", unit_id=pair[0], topic=pair[1]))
        seen.add(pair)
        if pair not in pairs:
            errors.append(issue("COVERAGE_PHANTOM_PAIR", "Pair is outside the frozen scope", unit_id=pair[0], topic=pair[1]))
        if not isinstance(entry["reason"], str) or not entry["reason"].strip() or not isinstance(entry["scope_rule"], str) or not entry["scope_rule"].strip():
            errors.append(issue("COVERAGE_REASON_MISSING", "Every disposition needs a reason and explicit scope rule", index=index))
        refs, disposition = entry["artifact_refs"], entry["disposition"]
        if not isinstance(refs, list):
            errors.append(issue("COVERAGE_REFS_INVALID", "artifact_refs must be an array", index=index))
            continue
        if disposition == "outside_scope":
            exclusion = excluded.get(pair)
            if exclusion is None or entry["scope_rule"] != exclusion["scope_rule"] or entry["reason"] != exclusion["reason"] or refs:
                errors.append(issue("COVERAGE_UNSUPPORTED_EXCLUSION", "Exclusion does not match the explicit scope decision or has covered artifacts", index=index))
        elif disposition == "unavailable":
            errors.append(issue("COVERAGE_MATERIAL_UNAVAILABLE", "Unavailable material cannot satisfy strict complete-scope coverage", index=index))
        elif disposition == "covered":
            if pair in excluded or not refs:
                errors.append(issue("COVERAGE_REFS_INVALID", "Covered pair is excluded or has no actual artifact", index=index))
        else:
            errors.append(issue("COVERAGE_DISPOSITION_INVALID", "Unknown coverage disposition", index=index))
        paths = set()
        for ref in refs:
            try:
                if not isinstance(ref, dict) or set(ref) != {"path", "sha256"}:
                    raise ValueError("Artifact ref must contain path and sha256")
                path = contained_file(root, ref["path"])
                if path in paths:
                    raise ValueError("Duplicate resolved artifact path")
                paths.add(path)
                if not file_bytes(root, ref):
                    raise ValueError("Empty coverage artifact")
            except (ValueError, OSError, TypeError) as exc:
                errors.append(issue("COVERAGE_ARTIFACT_INVALID", str(exc), index=index))
    for unit, topic in sorted(set(pairs) - seen):
        errors.append(issue("COVERAGE_MISSING_PAIR", "Expected pair has no disposition", unit_id=unit, topic=topic))
    return ValidationResult(not errors, tuple(errors), (issue("STRUCTURAL_COVERAGE_ONLY", "Exact membership and hashes do not prove preservation of meaning or confer acceptance"),))
