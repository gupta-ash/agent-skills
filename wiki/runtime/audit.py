"""Read-only audit planning and serialized expected-preimage publication.

Reports are domain-validated inputs, not permission to mutate note bodies. Only
an enrolled operations/verification region and the existing manifest are writable.
The caller holds project ownership; PublicationManager is the sole vault writer.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import re


from .schema import ResolvedVault, SchemaError, _load


@dataclass(frozen=True)
class AuditChange:
    path: Path
    expected_sha256: str | None
    content: bytes
    role: str
    region: str | None


@dataclass(frozen=True)
class AuditPlan:
    root: Path
    changes: tuple[AuditChange, ...]
    receipts: tuple[dict, ...]
    schema_sha256: str
    run_id: str
    operation_id: str


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _error(code, message, **details):
    raise SchemaError(code, message, details)


def _path(root, relative):
    if not isinstance(relative, str) or not relative or "\\" in relative:
        _error("AUDIT_UNSAFE_PATH", "Audit paths must be vault-relative POSIX paths")
    posix, windows = PurePosixPath(relative), PureWindowsPath(relative)
    if posix.is_absolute() or windows.drive or any(part in ("..", ".") for part in relative.split("/")):
        _error("AUDIT_UNSAFE_PATH", "Audit path escapes its root")
    target = root / relative
    for part in (target, *target.parents):
        if part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction()):
            _error("AUDIT_UNSAFE_PATH", "Audit path cannot traverse links")
        if part == root:
            break
    if target.exists() and not target.is_file():
        _error("AUDIT_UNSAFE_PATH", "Audit target must be a regular file")
    return target


def _frontmatter(data):
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise SchemaError("AUDIT_INVALID_LOG", "Audit log is not UTF-8") from exc
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        return {}
    end = next((index for index in range(1, len(lines)) if lines[index] in ("---", "...")), None)
    if end is None:
        _error("AUDIT_INVALID_LOG", "Audit log has unclosed frontmatter")
    value = _load("\n".join(lines[1:end]), "audit log frontmatter") or {}
    if not isinstance(value, dict):
        _error("AUDIT_INVALID_LOG", "Audit log frontmatter must be a mapping")
    return value


def _protected(path):
    if not path.exists():
        return False
    data = path.read_bytes()
    if path.suffix.lower() == ".json":
        try:
            fields = json.loads(data)
        except (ValueError, UnicodeDecodeError):
            return True
    else:
        fields = _frontmatter(data)
    if not isinstance(fields, dict):
        return True
    flag = fields.get("human_edited", False)
    return flag not in (False, None) or fields.get("pipeline_id") == "hand-curated" or fields.get("verify_log_locked", False) not in (False, None)


def _count(report, name, aliases=(), *, default=None):
    values = []
    for key in (name, *aliases):
        value = report
        for part in key.split("."):
            if not isinstance(value, dict) or part not in value:
                break
            value = value[part]
        else:
            if type(value) is not int or value < 0:
                _error("AUDIT_INVALID_REPORT", f"{key} must be a nonnegative integer")
            values.append(value)
    if not values:
        if default is not None:
            return default
        _error("AUDIT_INVALID_REPORT", f"Missing explicit {name} count")
    if len(set(values)) != 1:
        _error("AUDIT_REPORT_CONFLICT", f"Conflicting {name} aliases")
    return values[0]


def _record(report, run_id, operation_id, attempted_at):
    if not isinstance(report, dict):
        _error("AUDIT_INVALID_REPORT", "Audit report must be an object")
    if any(not isinstance(value, str) or not value or "\n" in value or "\r" in value for value in (run_id, operation_id, attempted_at)):
        _error("AUDIT_INVALID_REPORT", "Audit identities and attempt time must be nonempty single-line strings")
    try:
        timestamp = datetime.fromisoformat(attempted_at.replace("Z", "+00:00"))
        if timestamp.utcoffset() is None or timestamp.utcoffset().total_seconds() != 0:
            raise ValueError("UTC required")
    except ValueError as exc:
        raise SchemaError("AUDIT_INVALID_REPORT", "attempted_at must be an actual UTC ISO8601 timestamp") from exc
    axis = report.get("verification_axis")
    if axis not in ("drift", "content"):
        _error("AUDIT_INVALID_REPORT", "verification_axis must be drift or content")
    operation = report.get("operation", "verification")
    if operation not in ("verification", "ingest"):
        _error("AUDIT_INVALID_REPORT", "Unknown audit operation kind")
    selected = _count(report, "selected", ("selected_count", "pages_expected", "total_slugs"))
    processed = _count(report, "processed", ("processed_count", "pages_processed"))
    verified = _count(report, "verified", ("verified_count", "summary.verified"))
    if not verified <= processed <= selected:
        _error("AUDIT_INVALID_REPORT", "Expected verified <= processed <= selected")
    complete = report.get("fan_in_complete")
    if type(complete) is not bool or (complete and processed != selected):
        _error("AUDIT_INVALID_REPORT", "Complete fan-in requires every selected subject")
    tooling = report.get("tooling_failures", [])
    if not isinstance(tooling, list):
        _error("AUDIT_INVALID_REPORT", "tooling_failures must be a list")
    integrity = report.get("review_integrity_escalations", [])
    if not isinstance(integrity, list):
        _error("AUDIT_INVALID_REPORT", "review_integrity_escalations must be a list")
    integrity = list(integrity)
    summary = report.get("summary", {})
    if not isinstance(summary, dict):
        _error("AUDIT_INVALID_REPORT", "summary must be an object")
    failures = _count(report, "tooling_failure", ("summary.tooling_failure",), default=len(tooling))
    if tooling and failures != len(tooling):
        _error("AUDIT_REPORT_CONFLICT", "Tooling-failure count conflicts with its subject list")
    if failures > processed:
        _error("AUDIT_INVALID_REPORT", "Tooling failures exceed processed subjects")
    status_fields = ("verified", "needs_attention", "insufficient_data", "tooling_failure")
    if axis == "content" and all(key in summary for key in status_fields):
        counts = [_count(report, "summary." + key) for key in status_fields]
        if sum(counts) != processed:
            _error("AUDIT_REPORT_CONFLICT", "Page verdict counts do not account for processed subjects")
    for name in ("missing_cc2_outputs", "unparseable_cc2_outputs", "missing_outputs", "unparseable_outputs"):
        values = report.get(name, [])
        if not isinstance(values, list) or (complete and values):
            _error("AUDIT_REPORT_CONFLICT", "Complete fan-in cannot contain missing/unparseable outputs")
    pages = report.get("pages")
    if pages is not None:
        if not isinstance(pages, list) or len(pages) != processed or any(not isinstance(page, dict) for page in pages):
            _error("AUDIT_REPORT_CONFLICT", "Page results do not account for processed subjects")
        if axis == "content":
            clean = sum(page.get("status") == "VERIFIED" for page in pages)
            if clean != verified:
                _error("AUDIT_REPORT_CONFLICT", "Verified page count conflicts with page results")
            page_failures = sum(page.get("status") == "TOOLING_FAILURE" for page in pages)
            if page_failures != failures:
                _error("AUDIT_REPORT_CONFLICT", "Tooling-failure count conflicts with page results")
            for page in pages:
                escalated = bool(page.get("summary", {}).get("claims_review_integrity_escalated")) or any(finding.get("review_integrity_escalated") for finding in page.get("findings", []))
                if escalated:
                    if page.get("page_id") not in integrity:
                        integrity.append(page.get("page_id"))
                    if page.get("status") == "VERIFIED":
                        _error("AUDIT_REPORT_CONFLICT", "A rejected native review cannot verify a page")
                if page.get("status") == "VERIFIED" and (page.get("claims_total") == 0 or page.get("total_claims") == 0):
                    _error("AUDIT_REPORT_CONFLICT", "A zero-claim page is not verified")
    if report.get("claims_total") == 0 and verified:
        _error("AUDIT_REPORT_CONFLICT", "Zero claims cannot refresh semantic verification")
    if "scope" in report and "selectors" in report and report["scope"] != report["selectors"]:
        _error("AUDIT_REPORT_CONFLICT", "Conflicting scope/selectors aliases")
    # Retain compatibility fields, but never let a report supply arbitrary
    # manifest patches. The complete report remains at its private report_path.
    record = {"run_id": run_id, "operation_id": operation_id, "operation": operation,
              "verification_axis": axis, "selected": selected, "processed": processed,
              "verified": verified, "fan_in_complete": complete, "attempted_at": attempted_at,
              "scope": report.get("scope", report.get("selectors", {})), "tooling_failures": tooling,
              "tooling_failure_count": failures, "summary": summary, "report_path": report.get("report_path"),
              "review_integrity_escalations": integrity,
              "semantic_verification_refreshed": operation != "ingest" and axis == "content" and complete and verified > 0 and failures == 0 and not integrity}
    record["fan_in_diagnostics"] = {key: report[key] for key in ("missing_cc2_outputs", "unparseable_cc2_outputs", "missing_outputs", "unparseable_outputs") if key in report}
    if axis == "drift":
        record["drift"] = {key: report[key] for key in ("freshness_filter", "source_class_filter", "snapshot_on_drift", "backfill_mode") if key in report}
        for key in ("clean", "drift_detected", "source_unavailable", "skipped_book", "backfilled"):
            if key in report or key in summary:
                record["drift"][key] = _count(report, key, ("summary." + key,))
    if operation == "ingest":
        record["ingest"] = {key: report[key] for key in ("input", "source_type", "created", "updated", "compilations_affected", "depth1_links_followed", "depth2_links_unfetched") if key in report}
    try:
        return json.loads(_json(record))
    except (TypeError, ValueError) as exc:
        raise SchemaError("AUDIT_INVALID_REPORT", "Audit fields must be JSON values") from exc


def _append_region(data, region, record):
    begin, end = (f"<!-- ash_wiki:{side} {region} -->".encode() for side in ("begin", "end"))
    if data.count(begin) != 1 or data.count(end) != 1 or data.index(begin) >= data.index(end):
        _error("AUDIT_REGION_NOT_ENROLLED", "Audit requires a single balanced managed region")
    if any(re.search(rb"(?m)^" + re.escape(marker) + rb"[\t ]*\r?$", data) is None for marker in (begin, end)):
        _error("AUDIT_REGION_NOT_ENROLLED", "Audit markers must occupy complete lines")
    left, right = data.index(begin) + len(begin), data.index(end)
    # A literal digest marker binds both identities and all consumed report
    # values, so partial publication can be prepared again without duplication.
    identity = sha256(_json([record["run_id"], record["operation_id"]]).encode()).hexdigest()
    marker = f"<!-- ash_wiki:audit {identity} ".encode()
    desired_marker = marker + sha256(_json(record).encode("utf-8")).hexdigest().encode() + b" -->"
    owned = data[left:right]
    if marker in data[:left] or marker in data[right:]:
        _error("AUDIT_OPERATION_CONFLICT", "Audit marker is outside the owned region")
    if marker in owned:
        if owned.count(marker) != 1 or desired_marker not in owned:
            _error("AUDIT_OPERATION_CONFLICT", "Audit operation already records a different report")
        return data
    newline = b"\r\n" if b"\r\n" in owned or (not owned.strip() and b"\r\n" in data) else b"\n"
    def value(item):
        return _json(item)
    if record["operation"] == "ingest":
        heading = f"## {record['attempted_at']} — wiki ingest"
        labels = [("Input", record["ingest"].get("input")), ("Source type", record["ingest"].get("source_type")),
                  ("Created", record["ingest"].get("created")), ("Updated", record["ingest"].get("updated")),
                  ("Compilations affected", record["ingest"].get("compilations_affected")),
                  ("Depth-1 links followed", record["ingest"].get("depth1_links_followed")),
                  ("Depth-2 links noted (unfetched)", record["ingest"].get("depth2_links_unfetched"))]
    elif record["verification_axis"] == "content":
        heading = f"## Content-check {record['attempted_at']}"
        labels = [("Mode", "content_check"), ("Slugs expected", record["selected"]),
                  ("Slugs processed", record["processed"]), ("Slugs verified", record["verified"]),
                  ("Verdict summary", record["summary"]),
                  ("Claims insufficient_data", record["summary"].get("claims_insufficient_data")),
                  ("Claims not reviewed", record["summary"].get("claims_not_reviewed")),
                  ("Claims escalated on review integrity", record["summary"].get("claims_review_integrity_escalated"))]
    else:
        heading = f"## Raw re-verification {record['attempted_at']}"
        labels = [("Mode", "drift_check"), ("Raws selected", record["selected"]),
                  ("Raws processed", record["processed"]), ("Raws verified", record["verified"]),
                  ("Freshness filter", record["drift"].get("freshness_filter")),
                  ("Source-class filter", record["drift"].get("source_class_filter")),
                  ("Snapshot-on-drift", record["drift"].get("snapshot_on_drift")),
                  ("Backfill mode", record["drift"].get("backfill_mode")),
                  ("Counts", {"Total": record["selected"], "Clean": record["drift"].get("clean"),
                              "Drift detected": record["drift"].get("drift_detected"),
                              "Source unavailable": record["drift"].get("source_unavailable"),
                              "Skipped (book)": record["drift"].get("skipped_book"),
                              "Backfilled": record["drift"].get("backfilled")}),
                  ("Drift detail", record["report_path"])]
    labels += [("Pipeline", record["run_id"]), ("Operation", record["operation_id"]),
               ("Verification axis", record["verification_axis"]), ("Fan-in complete", record["fan_in_complete"]),
               ("Fan-in diagnostics", record["fan_in_diagnostics"]),
               ("Tooling failures", record["tooling_failures"]), ("Report", record["report_path"]),
               ("Review integrity escalations", record["review_integrity_escalations"])]
    lines = [desired_marker.decode(), heading, ""] + [f"**{label}:** {value(item)}" for label, item in labels] + [""]
    addition = newline.join(line.encode("utf-8") for line in lines) + newline
    separator = b"" if owned.endswith(newline * 2) else newline if owned.endswith(newline) else newline * 2
    return data[:right] + separator + addition + data[right:]


def prepare_audit(resolved: ResolvedVault, report: dict, run_id: str,
                  operation_id: str, attempted_at: str) -> AuditPlan:
    """Prepare candidates without mkdir, database, native or network effects.

    Required report: verification_axis, selected/processed/verified counts,
    fan_in_complete. Retained pages_expected/pages_processed/summary.verified
    aliases are accepted only when equivalent. Optional scope/selectors, summary,
    tooling_failures, report_path and operation='ingest' retain audit context.
    """
    record = _record(report, run_id, operation_id, attempted_at)
    if not resolved.controls_enrolled:
        _error("VAULT_CONTROLS_NOT_ENROLLED", "Audit writes require enrolled control ownership")
    root = resolved.root
    schema = _path(root, ".wiki-schema.md")
    schema_hash = sha256(schema.read_bytes()).hexdigest()
    if schema_hash != resolved.constraints_ref.sha256:
        _error("AUDIT_SCHEMA_CHANGED", "Schema changed after resolution")
    manifest_path = _path(root, resolved.canonical_files["manifest"])
    if not manifest_path.exists():
        _error("AUDIT_MANIFEST_MISSING", "Missing raw manifest; audit cannot claim a successful write")
    manifest_bytes = manifest_path.read_bytes()
    try:
        manifest = json.loads(manifest_bytes)
    except (ValueError, UnicodeDecodeError) as exc:
        raise SchemaError("AUDIT_INVALID_MANIFEST", "Manifest must be a JSON object") from exc
    if not isinstance(manifest, dict):
        _error("AUDIT_INVALID_MANIFEST", "Legacy flat-array manifests require explicit regeneration")
    manifest_protected = _protected(manifest_path)
    namespace = manifest.setdefault("wiki_workflow", {})
    if not isinstance(namespace, dict):
        _error("AUDIT_INVALID_MANIFEST", "wiki_workflow namespace must be an object")
    operations = namespace.setdefault("audit_operations", {})
    if not isinstance(operations, dict):
        _error("AUDIT_INVALID_MANIFEST", "audit_operations namespace must be an object")
    key = sha256(_json([run_id, operation_id]).encode()).hexdigest()
    if key in operations and operations[key] != record:
        _error("AUDIT_OPERATION_CONFLICT", "Run/operation ID already binds a different audit record")
    changes, receipts = [], []
    role, region = ("log", "operations") if record["operation"] == "ingest" else ("verify_log", "verification")
    log_relative = resolved.canonical_files[role]
    log = _path(root, log_relative)
    for reference_role, reference_path in resolved.canonical_files.items():
        if reference_role not in {"schema", "hub", "index", "manifest", "log", "verify_log"} and reference_path in {log_relative, resolved.canonical_files["manifest"]}:
            _error("AUDIT_PROTECTED_REFERENCE", "Audit target is also a protected canonical reference")
    if not log.exists():
        _error("AUDIT_LOG_MISSING", "Initialize/enroll the missing audit log before writing")
    before = log.read_bytes()
    fields = _frontmatter(before)
    if fields.get("verify_log_locked", False) not in (False, None):
        _error("AUDIT_LOG_LOCKED", "Locked audit log cannot receive this operation", path=str(log))
    if _protected(log):
        receipts.append({"path": str(log), "role": role, "status": "skipped_protected", "proposal": record})
    else:
        if resolved.raw_declarations.get("managed_navigation", {}).get(log_relative) != region:
            _error("AUDIT_REGION_NOT_ENROLLED", "Audit log region is not enrolled in the schema")
        after = _append_region(before, region, record)
        if after != before:
            changes.append(AuditChange(log, sha256(before).hexdigest(), after, role, region))
        else:
            receipts.append({"path": str(log), "role": role, "status": "already_recorded"})
    if manifest_protected:
        receipts.append({"path": str(manifest_path), "role": "manifest", "status": "skipped_protected", "proposal": record})
    elif key not in operations:
        operations[key] = record
        manifest["last_verify_attempt_at"] = attempted_at
        if record["verification_axis"] == "content" and record["operation"] != "ingest":
            manifest["last_verified_scope"] = record["scope"]
            manifest["last_verified_slug_count"] = record["verified"]
        if record["semantic_verification_refreshed"]:
            manifest["last_verified_at"] = attempted_at
        after = (json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
        changes.append(AuditChange(manifest_path, sha256(manifest_bytes).hexdigest(), after, "manifest", None))
    else:
        receipts.append({"path": str(manifest_path), "role": "manifest", "status": "already_recorded"})
    return AuditPlan(root, tuple(changes), tuple(receipts), schema_hash, run_id, operation_id)


def apply_audit(plan: AuditPlan, publication, run_id, attempt_id, operation_id,
                candidate_dir: Path) -> tuple[dict, ...]:
    """Write private candidates, then call the sole canonical PublicationManager.

    On a failed effect, SchemaError.details.receipts retains every completed or
    protected-skipped effect. Resume with the same plan and candidate directory.
    """
    if (run_id, operation_id) != (plan.run_id, plan.operation_id):
        _error("AUDIT_OPERATION_CONFLICT", "Application cannot change the planned run/operation")
    if sha256(_path(plan.root, ".wiki-schema.md").read_bytes()).hexdigest() != plan.schema_sha256:
        _error("AUDIT_SCHEMA_CHANGED", "Schema changed before audit publication")
    candidate_dir = Path(candidate_dir)
    if not candidate_dir.is_absolute() or ".." in candidate_dir.parts:
        _error("AUDIT_UNSAFE_PATH", "Candidate directory must be absolute")
    if candidate_dir == plan.root or plan.root in candidate_dir.parents:
        _error("AUDIT_UNSAFE_PATH", "Audit candidates must remain outside the vault")
    for parent in (candidate_dir, *candidate_dir.parents):
        if parent.is_symlink() or (hasattr(parent, "is_junction") and parent.is_junction()):
            _error("AUDIT_UNSAFE_PATH", "Candidates cannot traverse links")
    receipts = list(plan.receipts)
    for change in plan.changes:
        relative = change.path.relative_to(plan.root).as_posix()
        candidate = candidate_dir / (sha256((operation_id + "\0" + relative).encode()).hexdigest() + ".candidate")
        effect_id = operation_id + ":audit:" + sha256(relative.encode()).hexdigest()
        try:
            candidate_dir.mkdir(parents=True, exist_ok=True)
            if candidate.is_symlink() or (hasattr(candidate, "is_junction") and candidate.is_junction()):
                _error("AUDIT_UNSAFE_PATH", "Candidate must not be a link")
            if candidate.exists():
                if not candidate.is_file() or candidate.read_bytes() != change.content:
                    _error("AUDIT_CANDIDATE_CONFLICT", "Existing audit candidate has different bytes")
            else:
                with candidate.open("xb") as stream:
                    stream.write(change.content)
            receipts.append(publication.publish_file(run_id, attempt_id, candidate, change.path,
                change.expected_sha256, effect_id, root=plan.root, protected_check=_protected))
        except Exception as exc:
            raise SchemaError(getattr(exc, "code", "AUDIT_APPLY_FAILED"), str(exc),
                              {"receipts": receipts, "path": str(change.path), "operation_id": effect_id,
                               "cause": getattr(exc, "details", {})}) from exc
    return tuple(receipts)
