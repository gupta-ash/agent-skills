"""Read-only structural capture checks, not a semantic/visual fidelity verdict.

The controller must freeze independent ``expected_units`` before dispatch, or
supply ``enumeration_ref={path,sha256}`` pointing to that JSON {units:[...]}.
Each expectation uses the retained unit fields; transformed outputs additionally
need independently observed ``expected_sha256`` (path->digest) or
``expected_bytes`` (path->exact byte count). Worker diagnostics cannot substitute
for the enumeration. Paths, including retained absolute paths, stay inside root.
"""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path, PureWindowsPath
import re
from typing import Mapping

from _workflow.contracts import ValidationResult


def issue(code: str, message: str, **details) -> dict:
    return {"code": code, "message": message, "details": details}


def digest(value) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def contained_file(root: Path, value: str) -> Path:
    """Resolve an existing regular file without traversal, symlinks or junctions."""
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ValueError("Expected a nonempty path")
    parts = value.replace("\\", "/").split("/")
    if ".." in parts or (PureWindowsPath(value).drive and not Path(value).is_absolute()):
        raise ValueError("Parent traversal or foreign/drive-relative path")
    root = Path(root).absolute()
    candidate = Path(value) if Path(value).is_absolute() else root / value
    candidate = candidate.absolute()
    candidate.relative_to(root)
    # Resolve only after checking every existing component, including root parents.
    for part in (candidate, *candidate.parents):
        if part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction()):
            raise ValueError("Symlinks/junctions are not evidence paths")
    candidate.resolve(strict=True).relative_to(root.resolve(strict=True))
    if not candidate.is_file():
        raise ValueError("Evidence must be a regular file")
    return candidate


def file_bytes(root: Path, ref: Mapping) -> bytes:
    """Read a bound {path,sha256} reference; refuse mismatched current bytes."""
    if not isinstance(ref, Mapping) or not digest(ref.get("sha256")):
        raise ValueError("A lowercase byte SHA256 is required")
    data = contained_file(root, ref.get("path")).read_bytes()
    if sha256(data).hexdigest() != ref["sha256"]:
        raise ValueError("Current file bytes do not match bound SHA256")
    return data


def expected_units(inventory: dict, root: Path | None = None) -> list:
    """Load controller-frozen enumeration, never worker diagnostics/counts.

    When both inline and referenced enumerations exist they must be equivalent.
    A reference requires root; coverage callers can pass an already frozen inline
    enumeration instead. The caller remains responsible for enumeration authority.
    """
    if not isinstance(inventory, dict):
        raise ValueError("Inventory must be an object")
    units = inventory.get("expected_units")
    ref = inventory.get("enumeration_ref")
    if ref is not None:
        if root is None:
            raise ValueError("Enumeration reference requires a capture_root")
        document = json.loads(file_bytes(root, ref).decode("utf-8"))
        observed = document.get("units") if isinstance(document, dict) else None
        if units is not None and units != observed:
            raise ValueError("Conflicting inline and referenced enumerations")
        units = observed
    if not isinstance(units, list) or not units:
        raise ValueError("Independent expected_units enumeration is missing or empty")
    return units


def enumerate_snapshot_units(sources: list[dict], capture_root: Path) -> list[dict]:
    """Enumerate complete supplied snapshots before worker dispatch.

    Each source has path,sha256,source_id,kind. Source IDs are already resolved
    origin identities, not generated from filenames. This helper counts whole
    snapshots only; PDF/EPUB extractors must enumerate pages/sections themselves.
    It does not turn transformed worker outputs into independent expectations.
    """
    if not isinstance(sources, list) or not sources:
        raise ValueError("Expected nonempty supplied snapshot references")
    result = []
    for ordinal, source in enumerate(sources, 1):
        if not isinstance(source, dict) or not digest(source.get("source_id")) or not isinstance(source.get("kind"), str) or not source["kind"]:
            raise ValueError("Each snapshot needs an explicit source_id and kind")
        data = file_bytes(capture_root, source)
        if not data:
            raise ValueError("An empty snapshot cannot be enumerated as complete")
        result.append({"unit_id": f"u{ordinal:06d}", "source_order": ordinal,
                       "kind": source["kind"], "source_id": source["source_id"],
                       "original_locator": {"path": source["path"]},
                       "original_sha256": source["sha256"], "original_bytes": len(data),
                       "expected_paths": [source["path"]]})
    return result


def capture_exclusions(inventory: dict, units: list[dict]) -> dict[str, dict]:
    """Validate a controller-frozen partial selection, never an inferred skip.

    Partial inventories retain placeholder units with status outside_scope and
    the matching scope_rule. They are valid only for the named selected scope,
    never labelled complete. Unavailable *selected* units always fail.
    """
    scope = inventory.get("scope", {})
    if not isinstance(scope, dict):
        raise ValueError("Capture scope must be an object")
    exclusions = scope.get("exclusions", [])
    if not isinstance(exclusions, list):
        raise ValueError("Capture exclusions must be an array")
    if not exclusions:
        if inventory.get("capture_scope", "complete") != "complete":
            raise ValueError("Partial scope requires explicit exclusions")
        return {}
    ids = {unit["unit_id"] for unit in units}
    result = {}
    for entry in exclusions:
        if not isinstance(entry, dict) or set(entry) != {"unit_id", "scope_rule", "reason"}:
            raise ValueError("Capture exclusion requires unit_id,scope_rule,reason")
        if not all(isinstance(value, str) and value.strip() for value in entry.values()):
            raise ValueError("Capture exclusion values must be nonempty strings")
        uid = entry["unit_id"]
        if uid not in ids or uid in result:
            raise ValueError("Phantom or duplicate capture exclusion")
        result[uid] = entry
    selected = scope.get("selected_units")
    if inventory.get("capture_scope") != "partial" or not isinstance(selected, list) or not selected or any(not isinstance(uid, str) for uid in selected) or len(set(selected)) != len(selected) or set(selected) != ids - result.keys():
        raise ValueError("Partial capture must bind exactly its nonempty selected units")
    return result


def _paths(root, paths):
    if not isinstance(paths, list) or not paths or any(not isinstance(p, str) for p in paths):
        raise ValueError("Expected a nonempty path array")
    resolved = [contained_file(root, p) for p in paths]
    if len(set(resolved)) != len(resolved):
        raise ValueError("Duplicate paths")
    return resolved


def validate_capture(inventory: dict, capture_root: Path) -> ValidationResult:
    """Check enumeration, membership, containment, current bytes and completeness.

    A valid result proves these mechanical checks only. Fidelity, image meaning
    and complete semantic preservation still require the explicit verifier/review.
    Original hashes are byte hashes, not normalized content/source-authority hashes.
    """
    errors = []
    try:
        expected = expected_units(inventory, capture_root)
        excluded = capture_exclusions(inventory, expected)
    except (ValueError, OSError, TypeError, KeyError, UnicodeError) as exc:
        return ValidationResult(False, (issue("CAPTURE_ENUMERATION_UNAVAILABLE", str(exc)),))
    if inventory.get("schema_version") != 1 or type(inventory.get("schema_version")) is not int:
        errors.append(issue("CAPTURE_SCHEMA_INVALID", "Expected schema_version 1"))
    actual = inventory.get("units")
    if not isinstance(actual, list):
        return ValidationResult(False, (*errors, issue("CAPTURE_UNITS_INVALID", "units must be an array")))
    expected_by_id, actual_by_id = {}, {}
    for label, units, indexed in (("expected", expected, expected_by_id), ("actual", actual, actual_by_id)):
        orders = set()
        for ordinal, unit in enumerate(units, 1):
            if not isinstance(unit, dict):
                errors.append(issue("CAPTURE_UNIT_INVALID", "Unit must be an object", collection=label, ordinal=ordinal))
                continue
            uid, order = unit.get("unit_id"), unit.get("source_order")
            if not isinstance(uid, str) or re.fullmatch(r"u[0-9]{6,}", uid) is None:
                errors.append(issue("CAPTURE_UNIT_INVALID", "Invalid stable unit_id", collection=label, ordinal=ordinal))
                continue
            if uid in indexed:
                errors.append(issue("CAPTURE_DUPLICATE_UNIT", "Repeated unit identity", unit_id=uid, collection=label))
            indexed[uid] = unit
            if type(order) is not int or order != ordinal or order in orders or uid != f"u{ordinal:06d}":
                errors.append(issue("CAPTURE_ORDER_INVALID", "Units must retain immutable source order/IDs", unit_id=uid, collection=label))
            if type(order) is int:
                orders.add(order)
    for uid in sorted(expected_by_id.keys() - actual_by_id.keys()):
        errors.append(issue("CAPTURE_MISSING_UNIT", "Expected unit is absent", unit_id=uid))
    for uid in sorted(actual_by_id.keys() - expected_by_id.keys()):
        errors.append(issue("CAPTURE_PHANTOM_UNIT", "Unit was not independently enumerated", unit_id=uid))
    for uid, expectation in expected_by_id.items():
        unit = actual_by_id.get(uid)
        if unit is None:
            continue
        for field in ("kind", "source_id", "original_locator", "original_sha256"):
            value = expectation.get(field)
            if value is None or value == "" or unit.get(field) != value:
                errors.append(issue("CAPTURE_IDENTITY_MISMATCH", "Unit disagrees with frozen enumeration", unit_id=uid, field=field))
        if not digest(expectation.get("source_id")) or not digest(expectation.get("original_sha256")):
            errors.append(issue("CAPTURE_HASH_INVALID", "Source identity/original byte digest is invalid", unit_id=uid))
        if uid in excluded:
            if unit.get("status") != "outside_scope" or unit.get("scope_rule") != excluded[uid]["scope_rule"]:
                errors.append(issue("CAPTURE_UNSUPPORTED_EXCLUSION", "Excluded placeholder does not match frozen scope", unit_id=uid))
            continue
        try:
            exp_paths = _paths(capture_root, expectation.get("expected_paths"))
            claimed_paths = _paths(capture_root, unit.get("expected_paths"))
            produced = _paths(capture_root, unit.get("produced_paths"))
            if exp_paths != claimed_paths or set(exp_paths) != set(produced):
                raise ValueError("Expected/produced path membership differs from enumeration")
            hashes = unit.get("produced_sha256")
            if isinstance(hashes, str) and len(produced) == 1:
                hashes = {unit["produced_paths"][0]: hashes}
            if not isinstance(hashes, dict) or set(hashes) != set(unit["produced_paths"]):
                raise ValueError("Every produced path needs exactly one observed byte digest")
            trusted_hashes = expectation.get("expected_sha256", {})
            trusted_sizes = expectation.get("expected_bytes", {})
            if not isinstance(trusted_hashes, dict) or not isinstance(trusted_sizes, dict):
                raise ValueError("Expected output hashes/sizes must be mappings")
            if set(trusted_hashes) - set(expectation["expected_paths"]) or set(trusted_sizes) - set(expectation["expected_paths"]):
                raise ValueError("Expected output metadata has phantom paths")
            locator = expectation.get("original_locator")
            if not isinstance(locator, dict):
                raise ValueError("Original locator must bind a local immutable source snapshot")
            original = contained_file(capture_root, locator.get("path"))
            original_data = original.read_bytes()
            if sha256(original_data).hexdigest() != expectation.get("original_sha256"):
                raise ValueError("Original source bytes changed")
            for owner in (expectation, unit):
                if "original_bytes" in owner and (type(owner["original_bytes"]) is not int or owner["original_bytes"] != len(original_data)):
                    raise ValueError("Original byte count disagrees with actual source")
            for name, path in zip(unit["produced_paths"], produced):
                data = path.read_bytes()
                if not data or not digest(hashes[name]) or sha256(data).hexdigest() != hashes[name]:
                    raise ValueError("Empty output or produced byte hash mismatch")
                expected_name = expectation["expected_paths"][exp_paths.index(path)]
                bound_hash, bound_size = trusted_hashes.get(expected_name), trusted_sizes.get(expected_name)
                if path == original:
                    bound_hash = expectation["original_sha256"]
                if bound_hash is None and bound_size is None:
                    raise ValueError("Transformed output lacks independent hash/byte completeness metadata")
                if bound_hash is not None and (not digest(bound_hash) or hashes[name] != bound_hash):
                    raise ValueError("Produced output differs from independent expected bytes")
                if bound_size is not None and (type(bound_size) is not int or bound_size <= 0 or len(data) != bound_size):
                    raise ValueError("Output byte length is truncated or unexpected")
        except (ValueError, OSError, TypeError, KeyError) as exc:
            errors.append(issue("CAPTURE_BYTES_INVALID", str(exc), unit_id=uid))
        completeness = unit.get("coverage")
        if not isinstance(completeness, dict):
            errors.append(issue("CAPTURE_COMPLETENESS_UNAVAILABLE", "Missing capture completeness metadata", unit_id=uid))
        else:
            complete = completeness.get("complete") is True or all(completeness.get(k) is True for k in ("complete_source_read", "full_body_retained"))
            if not complete:
                errors.append(issue("CAPTURE_INCOMPLETE", "Unit is not explicitly complete", unit_id=uid))
            for key in ("missing", "unavailable", "truncation", "truncated", "omissions", "skipped_units", "unprocessed_images"):
                if completeness.get(key):
                    errors.append(issue("CAPTURE_INCOMPLETE", "Missing/unavailable/truncated material", unit_id=uid, category=key))
            images = completeness.get("images", [])
            if not isinstance(images, list) or any(not isinstance(image, dict) or image.get("processed") is not True for image in images):
                errors.append(issue("CAPTURE_IMAGES_UNPROCESSED", "Image fidelity processing is incomplete", unit_id=uid))
        if unit.get("status") in ("unavailable", "partial", "truncated", "missing", "outside_scope"):
            errors.append(issue("CAPTURE_INCOMPLETE", "Unit status cannot satisfy complete capture", unit_id=uid))
    diagnostics = inventory.get("diagnostics", {})
    if isinstance(diagnostics, dict):
        for key in ("missing_units", "unavailable_units", "truncated_units", "omissions", "unprocessed_images", "skipped_subjects"):
            values = diagnostics.get(key)
            if values and (not isinstance(values, list) or any(not isinstance(value, str) or value not in excluded for value in values)):
                errors.append(issue("CAPTURE_INCOMPLETE", "Inventory discloses unaccounted material", category=key))
    notices = [issue("STRUCTURAL_CAPTURE_ONLY", "No semantic/visual fidelity or native review acceptance is inferred")]
    if excluded:
        notices.append(issue("PARTIAL_CAPTURE_SCOPE", "Capture is complete only for its explicit selected scope", excluded_units=sorted(excluded)))
    return ValidationResult(not errors, tuple(errors), tuple(notices))
