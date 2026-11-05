"""Origin identity, separate hash domains and read-only artifact bindings.

The controller supplies receipt InputRefs, never a worker's accepted:true flag.
Receipt JSON binds gate,artifact_id,output_sha256,source_ids,inputs:[{path,sha256}],
coverage_ref:{path,sha256}|null,status and, for review,
contract/producer/producer_evidence:[{path,sha256}]. Producer is the unchanged
native envelope; checks consume canonical fields, not severities or review meaning.
Receipt authenticity/dispatch ownership remains the durable controller's duty.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Mapping, Sequence

from _workflow.contracts import InputRef, ValidationResult
from .capture import contained_file, digest, file_bytes, issue


def source_id(material: dict) -> str:
    """Hash the persisted {kind,id} identity, not a chapter/file/snapshot count.

    Callers prefer ISBN+edition/provider identity; otherwise supply the canonical
    source reference. This function never invents identity or lineage metadata.
    """
    if not isinstance(material, dict) or set(material) != {"kind", "id"} or any(not isinstance(material[k], str) or not material[k].strip() or "\x00" in material[k] for k in material):
        raise ValueError("Source identity requires exactly nonempty kind and id strings")
    return sha256(json.dumps(material, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")).hexdigest()


def validate_source_identity(material: dict, expected_id: str) -> ValidationResult:
    """Check persisted origin material without inferring identity or lineage."""
    try:
        if not digest(expected_id) or source_id(material) != expected_id:
            raise ValueError("Origin digest does not match its persisted identity material")
        return ValidationResult(True)
    except (ValueError, TypeError) as exc:
        return ValidationResult(False, (issue("SOURCE_IDENTITY_MISMATCH", str(exc)),))


def normalized_body(text: str) -> str:
    """Remove leading YAML frontmatter, normalize CRLF to LF, retain body bytes."""
    if not isinstance(text, str):
        raise ValueError("Body input must be text")
    text = text.replace("\r\n", "\n")
    lines = text.splitlines(keepends=True)
    if lines and lines[0].lstrip("\ufeff").rstrip("\n") == "---":
        closing = next((i for i in range(1, len(lines)) if lines[i].rstrip("\n") in {"---", "..."}), None)
        if closing is None:
            raise ValueError("Cannot hash a body with unclosed frontmatter")
        text = "".join(lines[closing + 1:])
    return text


def content_hash(text: str) -> str:
    return "sha256:" + sha256(normalized_body(text).encode("utf-8")).hexdigest()


def source_hash(data: bytes, kind: str = "bytes") -> dict:
    """Compute transport/text hash only; never overwrite a manifest authority."""
    if not isinstance(data, bytes) or kind not in ("bytes", "text-lf"):
        raise ValueError("Expected bytes and source hash kind bytes|text-lf")
    if kind == "text-lf":
        data = data.decode("utf-8").replace("\r\n", "\n").encode("utf-8")
    return {"source_hash": sha256(data).hexdigest(), "source_hash_kind": kind, "source_hash_algorithm": "sha256"}


def compare_source_hashes(baseline: Mapping, current: Mapping) -> ValidationResult:
    """Compare like acquisition domains only; unavailable never rebaselines.

    source_sha256/source_hash_authority remain separate manifest assertions, not
    candidates for substitution into the local transport hash fields.
    """
    if not isinstance(baseline, Mapping) or not digest(baseline.get("source_hash")):
        return ValidationResult(False, (issue("BASELINE_UNAVAILABLE", "No acquired-source hash baseline; no rebaseline performed"),))
    if not isinstance(current, Mapping) or not digest(current.get("source_hash")):
        return ValidationResult(False, (issue("SOURCE_HASH_UNAVAILABLE", "No current acquired-source hash"),))
    domain = (baseline.get("source_hash_kind"), baseline.get("source_hash_algorithm"))
    if domain not in (("bytes", "sha256"), ("text-lf", "sha256")) or domain != (current.get("source_hash_kind"), current.get("source_hash_algorithm")):
        return ValidationResult(False, (issue("HASH_DOMAIN_MISMATCH", "Body, transport and manifest-authority hashes are not interchangeable"),))
    if baseline["source_hash"] != current["source_hash"]:
        return ValidationResult(False, (issue("SOURCE_DRIFT", "Like-domain source hashes differ"),))
    return ValidationResult(True)


def _input(value) -> InputRef:
    if isinstance(value, InputRef):
        return value
    if isinstance(value, Mapping) and set(value) == {"path", "sha256", "media_type"}:
        return InputRef(value["path"], value["sha256"], value["media_type"])
    raise ValueError("Artifact evidence must be a complete InputRef")


@dataclass(frozen=True)
class WikiArtifactRecord:
    artifact_id: str
    artifact_role: str
    path: str
    sha256: str
    source_ids: tuple[str, ...]
    topic: str | None
    raw_inputs: tuple[InputRef, ...]
    accepted_receipts: tuple[InputRef, ...]
    coverage_ref: InputRef | None

    def __post_init__(self):
        if not isinstance(self.artifact_id, str) or not self.artifact_id.strip():
            raise ValueError("Artifact identity is required")
        if self.artifact_role not in ("capture", "topic_distill", "synthesis", "strategy"):
            raise ValueError("Unknown artifact_role")
        if not isinstance(self.path, str) or not self.path or not digest(self.sha256):
            raise ValueError("Artifact path and byte SHA256 are required")
        if self.topic is not None and (not isinstance(self.topic, str) or not self.topic.strip()):
            raise ValueError("topic must be nonempty or null")
        if not isinstance(self.source_ids, (tuple, list)) or not self.source_ids or any(not digest(sid) for sid in self.source_ids) or len(set(self.source_ids)) != len(self.source_ids):
            raise ValueError("Explicit distinct full origin digests are required")
        object.__setattr__(self, "source_ids", tuple(self.source_ids))
        for field in ("raw_inputs", "accepted_receipts"):
            values = getattr(self, field)
            if not isinstance(values, (tuple, list)):
                raise ValueError(field + " must be a sequence of InputRefs")
            object.__setattr__(self, field, tuple(_input(value) for value in values))
        if self.coverage_ref is not None:
            object.__setattr__(self, "coverage_ref", _input(self.coverage_ref))


def lineage_summary(source_ids: Sequence[str], independent_lineages: Mapping[str, str | None] | None = None) -> dict:
    """Count origins separately; unknown lineage remains null, never independent.

    Explicit lineage IDs denote independently established upstream groups supplied
    by the controller, not model-inferred similarity or distinct raw filenames.
    """
    if not isinstance(source_ids, Sequence) or isinstance(source_ids, (str, bytes)) or not source_ids or any(not digest(sid) for sid in source_ids):
        raise ValueError("Source IDs must be origin digests")
    origins = set(source_ids)
    mapping = {} if independent_lineages is None else independent_lineages
    if not isinstance(mapping, Mapping) or set(mapping) - origins:
        raise ValueError("Lineage map contains an unknown origin")
    known = {sid: mapping.get(sid) for sid in origins}
    if any(value is not None and (not isinstance(value, str) or not value.strip()) for value in known.values()):
        raise ValueError("Lineage values must be explicit nonempty strings or null")
    missing = sorted(sid for sid, value in known.items() if value is None)
    return {"origin_count": len(origins), "independent_lineage_count": None if missing else len(set(known.values())), "unknown_lineage_source_ids": missing, "synthesis_scope": "cross_origin" if len(origins) > 1 else "single_origin"}


def _evidence_authority(root: Path, value: str, evidence_roots: tuple[Path, ...]) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError("Evidence requires an explicit path")
    path = Path(value)
    if not path.is_absolute():
        return root
    for authority in (root, *evidence_roots):
        if path.is_relative_to(authority):
            return authority
    raise ValueError("Evidence is outside the vault and explicitly authorized private roots")


def _binding(root: Path, refs: list | tuple, evidence_roots: tuple[Path, ...] = ()) -> dict[Path, str]:
    result = {}
    for ref in refs:
        if isinstance(ref, InputRef):
            ref = {"path": ref.path, "sha256": ref.sha256}
        if not isinstance(ref, Mapping) or set(ref) != {"path", "sha256"}:
            raise ValueError("Receipt binding requires path/sha256 only")
        authority = _evidence_authority(root, ref["path"], evidence_roots)
        path = contained_file(authority, ref["path"])
        if not file_bytes(authority, ref):
            raise ValueError("Empty raw/native evidence cannot establish a binding")
        if path in result:
            raise ValueError("Duplicate raw input or receipt binding")
        result[path] = ref["sha256"]
    return result


def _projection_identity(receipt):
    fields = {"native_producer_ref", "projection_id", "projection_ref"}
    if not fields.intersection(receipt):
        return None
    if not fields <= set(receipt) or not isinstance(receipt["projection_id"], str) or not receipt["projection_id"]:
        raise ValueError("Projected acceptance requires complete native/projection lineage")
    return receipt["projection_id"], _input(receipt["native_producer_ref"]), _input(receipt["projection_ref"])


def _lifecycle_projection(receipt, record, output, root, evidence_roots):
    """Validate H0→H1 lineage without pretending the native tool reviewed H1."""
    identity = _projection_identity(receipt)
    if identity is None:
        return None
    _projection_id, native, proof_ref = identity
    native_bytes = file_bytes(_evidence_authority(root, native.path, evidence_roots), {"path": native.path, "sha256": native.sha256})
    proof = json.loads(file_bytes(_evidence_authority(root, proof_ref.path, evidence_roots), {"path": proof_ref.path, "sha256": proof_ref.sha256}).decode("utf-8"))
    if not isinstance(proof, dict) or type(proof.get("schema_version")) is not int or proof.get("schema_version") != 1 or proof.get("kind") != "wiki-lifecycle" or proof.get("projection_id") != receipt["projection_id"]:
        raise ValueError("Unknown or mismatched lifecycle projection proof")
    source, candidate = proof.get("source"), proof.get("candidate")
    if (not isinstance(source, dict) or not isinstance(candidate, dict)
            or {key: source.get(key) for key in ("path", "sha256", "media_type")} != {"path": native.path, "sha256": native.sha256, "media_type": native.media_type}
            or candidate.get("sha256") != record.sha256 or source.get("media_type") != "text/markdown" or candidate.get("media_type") != "text/markdown"):
        raise ValueError("Projection does not bind the original native H0 and canonical H1 bytes")
    _evidence_authority(root, candidate.get("path"), evidence_roots)
    from _workflow.publication import PublicationError, validate_byte_projection
    try:
        validate_byte_projection(native_bytes, output, proof.get("edits"))
    except PublicationError as exc:
        raise ValueError(str(exc)) from exc
    import yaml
    def header(payload):
        text = payload.decode("utf-8")
        opening = re.match(r"\A\ufeff?---[ \t]*\r?\n", text)
        closing = re.compile(r"^---[ \t]*(?:\r?\n|$)", re.MULTILINE).search(text, opening.end()) if opening else None
        if closing is None:
            raise ValueError("Lifecycle projection requires real leading YAML")
        body = text[opening.end():closing.start()]
        try:
            node = yaml.compose(body, Loader=yaml.SafeLoader)
            values = yaml.safe_load(body)
        except yaml.YAMLError as exc:
            raise ValueError("Invalid projection frontmatter") from exc
        if not isinstance(node, yaml.MappingNode) or not isinstance(values, dict):
            raise ValueError("Projection frontmatter must be a mapping")
        keys = [key.value for key, _value in node.value]
        if len(keys) != len(set(keys)):
            raise ValueError("Duplicate projection frontmatter keys")
        return text, opening.end(), node, values
    original_text, header_start, original_node, original = header(native_bytes)
    _final_text, _header_start, _final_node, final = header(output)
    allowed = {"status", "last_verified"}
    if (proof.get("changed_fields") != ["status", "last_verified"] or original.get("status") != "draft"
            or original.get("last_verified") is not None or final.get("status") not in ("verified", "published")
            or set(original) != set(final)):
        raise ValueError("Lifecycle projection changes unsupported state/fields")
    for key in set(original) - allowed:
        if type(original[key]) is not type(final[key]) or original[key] != final[key]:
            raise ValueError("Lifecycle projection changed non-lifecycle metadata")
    spans = {}
    for key, value in original_node.value:
        if key.value in allowed:
            if not isinstance(value, yaml.ScalarNode) or any(value is other for other_key, other in original_node.value if other_key.value != key.value):
                raise ValueError("Shared/aliased lifecycle scalars cannot be projected")
            start = len(original_text[:header_start + value.start_mark.index].encode("utf-8"))
            end = len(original_text[:header_start + value.end_mark.index].encode("utf-8"))
            spans[(start, end)] = key.value
    edits = proof["edits"]
    if set(spans.values()) != allowed or len(edits) != 2 or {(edit["start"], edit["end"]) for edit in edits} != set(spans):
        raise ValueError("Projection edits must match exactly the two original YAML scalar spans")
    if any(b"\n" in bytes.fromhex(edit["after_hex"]) or b"\r" in bytes.fromhex(edit["after_hex"]) for edit in edits):
        raise ValueError("Lifecycle scalar replacements cannot inject new YAML lines")
    accepted = proof.get("accepted_at")
    if not isinstance(accepted, str) or not accepted.endswith("Z"):
        raise ValueError("Projection requires an actual UTC acceptance timestamp")
    instant = datetime.fromisoformat(accepted[:-1] + "+00:00")
    if instant.tzinfo is None or instant.utcoffset() != timezone.utc.utcoffset(instant):
        raise ValueError("Projection acceptance time must be UTC")
    verified = final.get("last_verified")
    if isinstance(verified, datetime):
        matches_time = verified == instant
    elif isinstance(verified, date):
        matches_time = verified == instant.date()
    elif isinstance(verified, str):
        matches_time = verified == instant.date().isoformat() or (verified.endswith("Z") and datetime.fromisoformat(verified[:-1] + "+00:00") == instant)
    else:
        matches_time = False
    if not matches_time:
        raise ValueError("Projected verification time differs from the actual accepted observation")
    gate_refs = proof.get("gate_refs")
    if not isinstance(gate_refs, list) or not gate_refs:
        raise ValueError("Projection requires actual hash-bound gate evidence")
    for ref in gate_refs:
        if not isinstance(ref, dict) or not {"path", "sha256", "media_type"} <= set(ref):
            raise ValueError("Projection gate evidence requires full references")
        _binding(root, [{"path": ref["path"], "sha256": ref["sha256"]}], evidence_roots)
    return identity


def validate_artifact_record(record: WikiArtifactRecord, root: Path, *, require_acceptance: bool = False, required_gates: tuple[str, ...] = ("capture", "fidelity", "coverage", "review"), review_contract: str = "vault-wiki", evidence_roots: tuple[Path, ...] = (), native_evidence_aliases: Mapping[str, InputRef] | None = None) -> ValidationResult:
    """Verify current output/raw/coverage/receipt bytes; never promote a draft.

    A draft with no receipts can be structurally valid only when acceptance is
    not requested. Consumers selecting accepted material MUST request acceptance
    and separately enforce controller ownership, coverage and native provenance.
    Receipt hashes are not proof they were produced by the required native tool.
    Private evidence roots are explicit caller authority, never inferred from a
    receipt. They grant no authority over canonical output or raw-input paths.
    Native path aliases must come from the trusted controller retention ledger;
    they bind unchanged original compact paths to exact retained evidence bytes.
    """
    errors, gates = [], set()
    try:
        root = Path(root)
        if native_evidence_aliases is not None and not isinstance(native_evidence_aliases, Mapping):
            raise ValueError("Native evidence aliases require an explicit trusted mapping")
        aliases = {}
        for original, value in (native_evidence_aliases or {}).items():
            if not isinstance(original, str) or not Path(original).is_absolute():
                raise ValueError("Native evidence alias origins must be absolute paths")
            aliases[original] = _input(value)
        evidence_roots = tuple(Path(value) for value in evidence_roots)
        for authority in evidence_roots:
            if not authority.is_absolute() or not authority.is_dir():
                raise ValueError("Private evidence roots must be existing absolute directories")
            if any(part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction())
                   for part in (authority, *authority.parents)):
                raise ValueError("Private evidence roots cannot traverse symlinks or junctions")
        output = file_bytes(root, {"path": record.path, "sha256": record.sha256})
        if not output:
            raise ValueError("Empty artifact is not a publishable output")
        raw = _binding(root, record.raw_inputs)
        if not raw:
            raise ValueError("Artifact has no raw evidence binding")
        coverage = None
        if record.coverage_ref is not None:
            coverage = {"path": record.coverage_ref.path, "sha256": record.coverage_ref.sha256}
            if not file_bytes(_evidence_authority(root, coverage["path"], evidence_roots), coverage):
                raise ValueError("Empty coverage evidence")
        if record.artifact_role != "capture" and record.topic is None:
            raise ValueError("Derived artifacts require an explicit topic")
        receipt_paths = set()
        projection_seen, projection_identity = False, None
        for ref in record.accepted_receipts:
            authority = _evidence_authority(root, ref.path, evidence_roots)
            path = contained_file(authority, ref.path)
            if path in receipt_paths:
                raise ValueError("Repeated acceptance receipt")
            receipt_paths.add(path)
            receipt = json.loads(file_bytes(authority, {"path": ref.path, "sha256": ref.sha256}).decode("utf-8"))
            if not isinstance(receipt, dict):
                raise ValueError("Acceptance receipt must be an object")
            current_projection = _projection_identity(receipt)
            if not projection_seen:
                if current_projection is not None:
                    _lifecycle_projection(receipt, record, output, root, evidence_roots)
                projection_seen, projection_identity = True, current_projection
            elif current_projection != projection_identity:
                raise ValueError("Acceptance gates disagree on native/projection scope")
            gate = receipt.get("gate")
            if not isinstance(gate, str) or gate not in required_gates or gate in gates:
                raise ValueError("Unknown/duplicate acceptance gate")
            if receipt.get("artifact_id") != record.artifact_id or receipt.get("output_sha256") != record.sha256 or receipt.get("status") != "completed":
                raise ValueError("Receipt identity/current output/status mismatch")
            origins = receipt.get("source_ids")
            if not isinstance(origins, list) or len(origins) != len(record.source_ids) or set(origins) != set(record.source_ids):
                raise ValueError("Receipt origin identities changed or contain duplicates")
            if not isinstance(receipt.get("inputs"), list) or _binding(root, receipt["inputs"]) != raw:
                raise ValueError("Receipt does not bind exactly the current raw input set")
            bound_coverage = receipt.get("coverage_ref")
            if coverage is None:
                if bound_coverage is not None:
                    raise ValueError("Receipt has a phantom coverage reference")
            elif not isinstance(bound_coverage, dict) or _binding(root, [bound_coverage], evidence_roots) != _binding(root, [coverage], evidence_roots):
                raise ValueError("Receipt does not bind current coverage")
            if gate == "review":
                producer = receipt.get("producer")
                if receipt.get("contract") != review_contract or not isinstance(producer, dict):
                    raise ValueError("Required native review contract/envelope missing")
                if producer.get("status") != "reviewed" or producer.get("verdict") not in ("approve", "needs-attention") or type(producer.get("exitCode")) is not int or producer["exitCode"] not in (0, 2) or producer.get("loop_should_continue") is not False:
                    raise ValueError("Native review is fail, unavailable or not qualifying")
                if (producer["exitCode"] == 0) != (producer["verdict"] == "approve"):
                    raise ValueError("Native review verdict/exit fields disagree")
                evidence = receipt.get("producer_evidence")
                if not isinstance(evidence, list) or not evidence:
                    raise ValueError("Actual native evidence bindings are missing")
                evidence_paths = _binding(root, evidence, evidence_roots)
                compact = "result_path" in producer or "receipt_path" in producer
                if compact:
                    for field in ("result_path", "receipt_path"):
                        original = producer.get(field)
                        if original in aliases:
                            alias = aliases[original]
                            authority = _evidence_authority(root, alias.path, evidence_roots)
                            retained = contained_file(authority, alias.path)
                            if evidence_paths.get(retained) != alias.sha256:
                                raise ValueError("Native retained alias is not an exact bound evidence member")
                        else:
                            authority = _evidence_authority(root, original, evidence_roots)
                            if contained_file(authority, original) not in evidence_paths:
                                raise ValueError("Native compact evidence is not readable/hash-bound")
                elif producer.get("review_conforming") is not True:
                    raise ValueError("Native full evidence is not qualified")
            elif receipt.get("verification") != "PASS":
                raise ValueError("Capture/fidelity/coverage receipt is not PASS")
            gates.add(gate)
        if require_acceptance:
            if coverage is None:
                raise ValueError("Acceptance requires current coverage evidence")
            if not required_gates or not set(required_gates).issubset(gates):
                raise ValueError("Missing required current acceptance receipts")
    except (ValueError, OSError, TypeError, KeyError, UnicodeError) as exc:
        errors.append(issue("ARTIFACT_BINDING_INVALID", str(exc)))
    return ValidationResult(not errors, tuple(errors), (issue("PROVENANCE_BINDING_ONLY", "No draft promotion, semantic proof, or native dispatch authenticity is inferred"),))
