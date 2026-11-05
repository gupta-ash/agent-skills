"""Strict launch/collect entry point for the Claude Code and Codex CLI routes.

The caller declares the artifact class and a readable source file. Missing or
same-family declarations refuse rather than defaulting to a reviewer. Transport
metadata must attest the expected reported family before canonical delivery.

This module coordinates packet construction, transport supervision, grading and
retained publication without reimplementing those components. A launch prints an
allocated handle before starting work; once issued, stdout contains only that
handle and subsequent failures are retained under it or diagnosed on stderr.
Collection is read-only and uses the same verdict-to-exit-code mapping as launch.

Reviewer sessions use a fresh empty directory, never the artifact's repository.
The CLI configuration selects the model; this module supplies no model override.
The applicable transport owns its timeout and internal attempt limit. Usage is
included in refusals; there is no successful non-review --help invocation.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

import envelope_schema

# --- the artifact classes ---------------------------------------------------------------

# Class names resolve uniformly to the installed contract files. Their content
# and canonical grading semantics belong to the contracts and envelope schema.
CONTRACT_NAMES = (
    "code-service",
    "code-fleet",
    "vault-raw",
    "vault-wiki",
    "research-report",
    "plan-design",
    "project-state",
    "model-instruction",
    "pipeline-template",
    "configuration",
)

# I6b's explicit opt-out. It resolves to no file and says so on stderr. It exists so that
# "no contract applies" is a DECLARATION a caller makes rather than a state the tool
# cannot distinguish from a caller who forgot.
CONTRACT_NONE = "none"

# The class an envelope carries when the refusal happened BEFORE a class was resolved —
# an unrecognised flag, a missing `--contract`. It is deliberately not `none`: `none` is a
# declaration the caller made, and writing it here would put words in their mouth. It is
# not a member of CONTRACT_NAMES either, so `envelope.primary_category_for` returns None
# for it and `envelope`'s absent-category clause takes the deny-by-default direction.
ARTIFACT_CLASS_UNRESOLVED = "unresolved"

# Runtime contracts are read from the installed skill, never an authoring checkout.
CONTRACTS_DIR = Path.home() / ".agents" / "skills" / "discriminator" / "contracts"

# --- the reviewer declaration ------------------------------------------------------------

# §7.2. The variable is per-harness and declarative. Its READER is what makes it a guard.
REVIEWER_ENV = "GAN_DISCRIMINATOR_CLI"
CALLER_FAMILY_ENV = "GAN_DISCRIMINATOR_CALLER_FAMILY"

# Default transport direction; the reciprocal route is selected explicitly.
REVIEWER_ACCEPTED = "codex"
REVIEWERS_ACCEPTED = ("codex", "claude")
CALLER_FAMILIES = ("codex", "claude")

# --- THE REVIEWER MODEL: NO NAME IS SENT, AND THAT IS THE DESIGN ---------------------------
#
# Leave the model unset so the reviewer CLI uses its own configuration. Family
# qualification checks the reported result rather than treating this as evidence.
REVIEWER_MODEL: str | None = None

# What `envelope.Provenance.requested_model` carries. It is a sentence, not a model id, so
# nothing downstream can mistake it for one — §0.1 forbids collapsing the three model
# identities, and a stated absence is not a collapse. The identity that DID answer arrives
# separately as `server_reported_model`, which on this path is the configured model the
# server resolved.
MODEL_UNSPECIFIED = "<none requested: no model key is sent; the reviewer CLI's configuration selects it>"
CLAUDE_MODEL_UNSPECIFIED = (
    "<none requested: Claude CLI configuration selects a Claude-family model>"
)

# §4 names this transport in as many words: codex's own `app-server`, over stdio. It is
# the REQUESTED transport; what the server reports about itself is a separate field that
# `supervisor` fills from the session and never from this one (§0.1).
TRANSPORT_REQUESTED = "codex app-server"

# Keep the service instance, reported model and actual delivery channel distinct.
# The handle identifies a durable run; a refusal before allocation uses stdout.
SERVICE_NAME = "discriminator/v1"
CHANNEL_RESULT_HANDLE = "result-handle"
CHANNEL_STDOUT = "stdout"

# --- the exit codes ------------------------------------------------------------------------

# Distinguish approved, no-review, usable nonblocking nonapproval and blocking
# nonapproval at the process boundary. Canonical grading owns the verdict floor.
EXIT_APPROVED = 0
EXIT_NO_REVIEW = 1  # §7.4 step 1: a tooling failure and a refusal are NOT a clean pass.
EXIT_NOT_APPROVED = 2
EXIT_FAIL = 4


class Refusal(Exception):
    """A caller-facing refusal, decided before or instead of a review.

    Carries a TITLE naming which of several different things went wrong (§7.1) and a
    DETAIL an operator can act on. `envelope` turns it into the layer-2 tooling-failure
    document; this class never formats one, because the envelope's shape is not this
    module's to know.

    `artifact_class` travels with the refusal because `envelope.no_review` requires one and
    a refusal raised before the class was resolved has none to give. It is not defaulted to
    `none` — see ARTIFACT_CLASS_UNRESOLVED.
    """

    def __init__(
        self, title: str, detail: str, artifact_class: str = ARTIFACT_CLASS_UNRESOLVED
    ) -> None:
        super().__init__(f"{title}: {detail}")
        self.title = title
        self.detail = detail
        self.artifact_class = artifact_class


# --- the published caller surface ----------------------------------------------------------


def _usage() -> str:
    """The caller-facing contract, built from the vocabularies rather than typed out.

    Severity names come from envelope_schema. Their definitions remain in the
    locked rubric, not a second scale restated in the usage message.
    """
    levels = " / ".join(envelope_schema.SEVERITY_VALUES)
    classes = "\n".join(f"      {name}" for name in CONTRACT_NAMES)
    return f"""\
discriminator — cross-family review. One entry point; there are no mode names.

  LAUNCH
    --contract <artifact-class>   REQUIRED. Selects the rubric and the contract.
                                  Use `{CONTRACT_NONE}` to declare that no contract
                                  applies. An absent flag is refused, not defaulted.
    --source <path>               REQUIRED. What is being reviewed. It is inlined into
                                  the packet; it is never the reviewer session's
                                  directory.
    --prompt-file <path>          OPTIONAL. What to look for in THIS review.

    stdout carries the HANDLE and nothing else. The answer is collected separately.

  COLLECT
    --collect <handle>            Once an answer is published, returns that same answer
                                  every time; collecting twice is not a second consumption.
                                  Collect BEFORE publication and you get a document
                                  describing the run's state instead — not the answer, and
                                  superseded by it.

  ARTIFACT CLASSES
{classes}
      {CONTRACT_NONE}

  SEVERITY
    {levels}. `low` does not exist — a cosmetic, stylistic or preference finding is not
    emitted at all. The levels are defined in the rubric the reviewer is shown, and
    nowhere else.

  THE TWO GATES ARE DIFFERENT GATES (I11)
    Verdict gate — {levels} all keep the verdict off `approve`. The reviewing model
    writes the verdict; nothing recomputes it from the counts.
    Fix-loop gate — only `critical` and must-fix `high` hold a fix loop open. A loop is
    allowed to stop with `medium` findings outstanding. Which `high` findings are
    must-fix is marked per finding in the delivered envelope; the caller reads that mark
    rather than deriving it.

  DECLARATION
    {CALLER_FAMILY_ENV} names the caller family and {REVIEWER_ENV} names the reviewer CLI.
    Both must be set to `codex` or `claude`, and they must differ. The selected transport's
    response metadata must then attest the expected reviewer family before a completed
    review is delivered.

  EXIT
    These four answer three questions and no more: whether a review happened at all,
    whether it approved, and whether its findings block. What no exit code answers is
    whether the review found anything — read the envelope for that, because a review can
    legitimately be `needs-attention` with an empty findings list.
    {EXIT_APPROVED}  a review ran and approved
    {EXIT_NOT_APPROVED}  a review ran, did not approve, and its findings do NOT block
    {EXIT_FAIL}  a review ran, did not approve, and its findings DO block
    {EXIT_NO_REVIEW}  no review happened — refused, or the tooling failed
    Migrating from before {EXIT_FAIL} existed: a caller testing `rc == {EXIT_NOT_APPROVED}`
    decides which it meant. "Did not approve" is now {EXIT_NOT_APPROVED} or {EXIT_FAIL}. "Did
    not approve and does not block" is {EXIT_NOT_APPROVED} alone, and changes nothing.
"""


# --- refusal titles ------------------------------------------------------------------------
#
# Distinct titles, because §7.1's whole point is that an operator told the wrong one goes
# and fixes something that is not broken.

T_INPUT_INVALID = "Discriminator input invalid"
T_DID_NOT_RUN = "Discriminator did not run"
T_NO_CLASS = "Discriminator input invalid: no artifact class declared"
T_UNKNOWN_CLASS = "Discriminator input invalid: unknown --contract name"
T_MISSING_CONTRACT_FILE = "Discriminator configuration invalid: contract file missing"
T_EMPTY_CONTRACT_FILE = "Discriminator configuration invalid: contract file holds no contract"
T_REVIEWER_UNDECLARED = "Discriminator configuration invalid: reviewer not declared"
T_REVIEWER_UNREACHABLE = "Discriminator configuration invalid: reviewer not reachable"
T_ACQUISITION_UNBUILT = "Discriminator not buildable: no acquisition for this source"
T_SOURCE_UNREADABLE = "Discriminator input invalid: source unreadable"

# R6's two refusals, and they are deliberately separate texts. "There was nothing to
# review" and "there were changes and none reached the reviewer" are different
# measurements with different fixes. Collapsing them is how the second one becomes a
# manufactured PASS: a reviewer handed no evidence has no way to answer anything but
# approve, and that reads identically to an earned clean review.
#
# THE MEASUREMENT IS `packet`'s AND IS NEVER RE-DERIVED HERE (§7.1, R6). `packet` raises
# `PacketRefusal` carrying its own verbatim text, and that text is what an operator reads
# as the DETAIL below. The second title is currently UNREACHABLE, and honestly so: it is
# raised only by the git-diff acquisition arm, which has no declared caller surface in
# this build — see `_acquire`.
T_NOTHING_TO_REVIEW = "Nothing to review"
T_NOTHING_REACHED = "Nothing reviewable reached the reviewer"


# --- what a parse produces -------------------------------------------------------------------


@dataclass(frozen=True)
class ReviewRequest:
    """One launch, fully resolved. Every field is settled before any work is dispatched."""

    artifact_class: str
    contract_path: Path | None  # None only for the declared `none` opt-out.
    source: Path
    prompt_file: Path | None


@dataclass(frozen=True)
class Invocation:
    """A parsed command line: exactly one of these two is set."""

    review: ReviewRequest | None
    collect_handle: str | None


# --- argument scanning ----------------------------------------------------------------------

FLAG_CONTRACT = "--contract"
FLAG_SOURCE = "--source"
FLAG_PROMPT_FILE = "--prompt-file"
FLAG_COLLECT = "--collect"

KNOWN_FLAGS = (FLAG_CONTRACT, FLAG_SOURCE, FLAG_PROMPT_FILE, FLAG_COLLECT)


def _scan(argv: list[str]) -> dict[str, str]:
    """Turn argv into flag -> value, refusing every ambiguity rather than resolving one.

    R7 IS THE RULE THIS FUNCTION EXISTS FOR. An empty value is a legal command line and it
    collapses onto "absent" under any value-emptiness test. `--prompt-file ""` from a
    caller who believes their prompt was used must not become a review that quietly had
    none, so an empty value is REFUSED here, where the flag's presence is still visible —
    downstream there is no longer anything to tell the two apart.

    There are no positional parameters. An unrecognised argument is refused rather than
    ignored, so a caller who passes review text where a flag belongs is told so instead of
    receiving a confident review of something else.
    """
    seen: dict[str, str] = {}
    index = 0
    while index < len(argv):
        token = argv[index]
        if token not in KNOWN_FLAGS:
            raise Refusal(
                f"{T_INPUT_INVALID}: unrecognised argument",
                f"{token!r} is not a flag this tool accepts, and there are no positional "
                f"parameters.\n\n{_usage()}",
            )
        if token in seen:
            raise Refusal(
                f"{T_INPUT_INVALID}: repeated flag",
                f"{token} was passed more than once. A later occurrence would silently "
                f"replace an earlier one, including replacing it with nothing.",
            )
        if index + 1 >= len(argv) or argv[index + 1].startswith("--"):
            raise Refusal(
                f"{T_INPUT_INVALID}: flag without a value",
                f"{token} was passed with no value after it.",
            )
        value = argv[index + 1]
        if value.strip() == "":
            raise Refusal(
                f"{T_INPUT_INVALID}: empty flag value",
                f"{token} was passed with an empty value. This tests whether the FLAG was "
                f"passed, not whether its VALUE is non-empty, so an empty value is refused "
                f"rather than silently accepted as an absent flag.",
            )
        seen[token] = value
        index += 2
    return seen


def resolve_contract(name: str, contracts_dir: Path | None = None) -> Path | None:
    """Resolve an artifact-class name to the contract file, or refuse (I6b).

    `none` resolves to no file and returns None. Every other recognised name resolves
    uniformly under `contracts_dir`.

    A recognised name whose file is not there is a REFUSAL, not a silent no-contract run.
    That is the same fail-closed reason I6c gives for the absent flag: a review that
    silently ran without the contract it was asked for is indistinguishable at the caller
    from one that had it.

    RESOLUTION HERE IS THE PATH, NOT THE CONTENTS. A file that exists and holds nothing is
    the same fail-closed case with a different symptom, and it is refused at the point the
    file is actually read — see the contract branch in `_launch`.

    `contracts_dir` is a parameter and not a new environment variable on purpose — §12
    admits no new configuration surface. A test passes its own directory; the deployed
    tool uses the default and nothing about it is configurable.
    """
    directory = CONTRACTS_DIR if contracts_dir is None else contracts_dir

    if name == CONTRACT_NONE:
        return None

    if name not in CONTRACT_NAMES:
        valid = ", ".join(CONTRACT_NAMES + (CONTRACT_NONE,))
        raise Refusal(
            T_UNKNOWN_CLASS,
            f"{name!r} is not an artifact class. Valid values: {valid}.",
        )

    path = directory / f"{name}.md"
    if not path.is_file():
        raise Refusal(
            T_MISSING_CONTRACT_FILE,
            f"Artifact class {name!r} resolves to {path}, which is not a readable file. "
            f"The class is recognised, so this is a deployment fault rather than a caller "
            f"mistake. The review is refused rather than run without the contract it was "
            f"asked for.",
            artifact_class=name,
        )
    return path


def require_reviewer(env: dict[str, str] | None = None) -> str:
    """Read the reviewer declaration in the REFUSING form (§7.2), or raise.

    SCOPE: this compares a NAME. The separate
    caller-family guard establishes that the requested direction is cross-family, and the
    completed result is not delivered until its transport metadata names the expected model
    family. This function alone does neither of those things.
    """
    environ = os.environ if env is None else env
    raw = environ.get(REVIEWER_ENV)

    if raw is None:
        raise Refusal(
            T_REVIEWER_UNDECLARED,
            f"{REVIEWER_ENV} is not set. The reviewer is declared per harness and this "
            f"tool refuses rather than assuming one — an assumed reviewer is the "
            f"same-family self-review the tool exists to avoid.",
        )
    if raw.strip() == "":
        raise Refusal(
            T_REVIEWER_UNDECLARED,
            f"{REVIEWER_ENV} is set to an empty value, which is not a declaration.",
        )

    name = raw.strip()
    if name not in REVIEWERS_ACCEPTED:
        raise Refusal(
            T_REVIEWER_UNREACHABLE,
            f"{REVIEWER_ENV} declares {name!r}. This build reaches only "
            f"{', '.join(repr(value) for value in REVIEWERS_ACCEPTED)}, so a review under that "
            f"declaration cannot be performed. Refusing rather than substituting one.",
        )
    return name


def require_cross_family(env: dict[str, str] | None = None) -> str:
    """Require an explicit caller family and an explicitly different reviewer family."""
    environ = os.environ if env is None else env
    raw_caller = environ.get(CALLER_FAMILY_ENV)
    if raw_caller is None or raw_caller.strip() == "":
        raise Refusal(
            T_REVIEWER_UNDECLARED,
            f"{CALLER_FAMILY_ENV} is not set to 'codex' or 'claude'. The tool cannot prove "
            "the selected reviewer is cross-family without knowing the caller family, so it "
            "refuses rather than assuming one.",
        )
    caller = raw_caller.strip()
    if caller not in CALLER_FAMILIES:
        raise Refusal(
            T_REVIEWER_UNDECLARED,
            f"{CALLER_FAMILY_ENV} declares {caller!r}; valid values are "
            f"{', '.join(repr(value) for value in CALLER_FAMILIES)}.",
        )
    reviewer = require_reviewer(environ)
    if reviewer == caller:
        raise Refusal(
            T_REVIEWER_UNREACHABLE,
            f"{CALLER_FAMILY_ENV} and {REVIEWER_ENV} both declare {caller!r}. That would be "
            "same-family self-review, so the request is refused rather than run or rerouted.",
        )
    return reviewer


def reviewer_model_setting() -> dict[str, str]:
    """The `model` entry for `RunPlan.session_settings` — normally EMPTY, and that is right.

    `transport.thread_start` omits the key when it is not given, so an empty mapping here
    is what puts no `model` on the wire and lets the reviewer CLI's own configuration
    select the family (C-4, structurally). See REVIEWER_MODEL for the measurement.

    This is a function rather than a literal `{}` at the call site so that the ONE place a
    reviewer model could ever be decided stays greppable and stays here, next to the
    reasoning for why it is empty.
    """
    return {} if REVIEWER_MODEL is None else {"model": REVIEWER_MODEL}


def parse(argv: list[str], contracts_dir: Path | None = None) -> Invocation:
    """The whole caller contract, in one place, refusing before anything is dispatched."""
    flags = _scan(argv)

    if FLAG_COLLECT in flags:
        extra = sorted(set(flags) - {FLAG_COLLECT})
        if extra:
            raise Refusal(
                f"{T_INPUT_INVALID}: collect takes no other flags",
                f"{FLAG_COLLECT} names an answer that already exists; "
                f"{', '.join(extra)} would describe a review to run. These are different "
                f"requests and this tool will not guess which was meant.",
            )
        return Invocation(review=None, collect_handle=flags[FLAG_COLLECT])

    if FLAG_CONTRACT not in flags:
        raise Refusal(
            T_NO_CLASS,
            f"No artifact class was declared. Pass {FLAG_CONTRACT} <class>, or "
            f"{FLAG_CONTRACT} {CONTRACT_NONE} to declare explicitly that no contract "
            f"applies.\n\n{_usage()}",
        )
    if FLAG_SOURCE not in flags:
        raise Refusal(
            f"{T_INPUT_INVALID}: no source declared",
            f"Pass {FLAG_SOURCE} <path> naming what is to be reviewed.",
        )

    artifact_class = flags[FLAG_CONTRACT]
    contract_path = resolve_contract(artifact_class, contracts_dir)

    source = Path(flags[FLAG_SOURCE])
    if not source.exists():
        raise Refusal(
            f"{T_INPUT_INVALID}: source does not exist",
            f"{FLAG_SOURCE} names {source}, which is not there.",
            artifact_class=artifact_class,
        )

    prompt_file: Path | None = None
    if FLAG_PROMPT_FILE in flags:
        prompt_file = Path(flags[FLAG_PROMPT_FILE])
        if not prompt_file.is_file():
            raise Refusal(
                f"{T_INPUT_INVALID}: prompt-file missing or unreadable",
                f"{FLAG_PROMPT_FILE} names {prompt_file}, which is not a readable file.",
                artifact_class=artifact_class,
            )

    return Invocation(
        review=ReviewRequest(
            artifact_class=artifact_class,
            contract_path=contract_path,
            source=source,
            prompt_file=prompt_file,
        ),
        collect_handle=None,
    )


def exit_code_for(verdict: str | None) -> int:
    """Map the canonical delivered verdict; unknown labels fail closed.

    Grading owns the monotone floor. A legitimate needs-attention document can
    have no findings, so this boundary must not recompute the verdict from counts.
    Check fail before enum membership because it has its own blocking exit code.
    """
    if verdict == "approve":
        return EXIT_APPROVED
    if verdict == "fail":
        return EXIT_FAIL
    if verdict in envelope_schema.VERDICT_VALUES:
        return EXIT_NOT_APPROVED
    return EXIT_NO_REVIEW


# --- reading a file this tool did not write ---------------------------------------------------


def _read_text(path: Path, what: str, artifact_class: str) -> str:
    """Read one caller-named file, STRICTLY, or refuse.

    Strict because a lenient decode ALTERS the evidence, and R11's advisory exists for
    content that was altered on a path where altering it was unavoidable. This path can
    refuse instead, so it does.
    """
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise Refusal(
            T_SOURCE_UNREADABLE,
            f"{what} at {path} could not be read as UTF-8: {exc}. It is refused rather "
            f"than decoded leniently, because a lenient decode alters the evidence the "
            f"review would then be graded on.",
            artifact_class=artifact_class,
        ) from exc


def _acquire(request: ReviewRequest, packet):
    """Acquire the artifact — the stage the first draft of this module skipped entirely.

    `packet` owns acquisition (§3, correction (a)): it runs the commands, rc-checks every
    invocation and measures what reached the packet. This function's only job is to choose
    which of `packet`'s acquisitions applies to what the caller named, and to hand over the
    bytes.

    ONLY THE INLINE ARM IS BUILT, AND THAT IS A REPORTED GAP RATHER THAN A DECISION.
    `packet.acquire_git_diff` exists and R6/R8 are written about it, but NOTHING IN THE
    SIGNED DESIGN SAYS WHAT SELECTS IT. G6 deleted the mode names, none of the ten artifact
    classes names a diff, and there is no flag. Picking "a directory means diff the working
    tree, with no diff arguments" would be inventing a caller-visible behaviour out of the
    availability of a function, which is precisely the failure this file is being repaired
    for. A directory is therefore refused, loudly, naming what is missing.
    """
    if request.source.is_dir():
        raise Refusal(
            T_ACQUISITION_UNBUILT,
            f"{FLAG_SOURCE} names the directory {request.source}. Inlining a file is the "
            f"only acquisition this build has a declared caller surface for. `packet` also "
            f"offers a git-diff acquisition, but nothing in the signed design says what "
            f"selects it — there are no mode names (G6), no artifact class names a diff, "
            f"and there is no flag — so it is left unbuilt rather than wired up on a "
            f"guess. Pass a file.",
            artifact_class=request.artifact_class,
        )
    if not request.source.is_file():
        raise Refusal(
            f"{T_INPUT_INVALID}: source is neither a file nor a directory",
            f"{FLAG_SOURCE} names {request.source}, which exists but is not something "
            f"this tool can read as an artifact.",
            artifact_class=request.artifact_class,
        )

    return packet.acquire_inline(
        _read_text(request.source, "the source artifact", request.artifact_class)
    )


# --- the run --------------------------------------------------------------------------------


def _provenance(
    envelope,
    handle: str | None,
    server_reported_model: str | None,
    channel: str,
    *,
    requested_model: str | None = None,
    attested_route: str | None = None,
):
    """§7.8's three-part identity. §0.1's THREE MODEL IDENTITIES ARE THREE FIELDS.

    `attested_route` is supplied only after the spawned reviewer transport reports a model
    in the expected family. A declaration or executable name alone never fills it, and the
    string records the exact metadata the attestation rested on.

    `channel` IS A REQUIRED ARGUMENT AND IS NEVER DEFAULTED HERE. Only the site that routes
    a document knows which channel it went down, and this function knows nothing about
    either. See CHANNEL_RESULT_HANDLE.
    """
    instance = SERVICE_NAME if handle is None else f"{SERVICE_NAME} handle={handle}"
    if requested_model is None:
        requested_model = MODEL_UNSPECIFIED if REVIEWER_MODEL is None else REVIEWER_MODEL
    return envelope.Provenance(
        service_instance=instance,
        channel=channel,
        requested_model=requested_model,
        server_reported_model=server_reported_model,
        attested_route=attested_route,
    )


def _reported_model(telemetry, outcome, err) -> str | None:
    """What the SERVER said answered, read back off §5.1's run record.

    `supervisor` owns the record and is its only writer (§7.9); `telemetry` keeps its READ
    side and nothing else. This module opens nothing for writing here and reclassifies
    nothing — §7.9 row 4.

    A record that cannot be read leaves this None, which §0.1 says is the honest value for
    an identity the server did not give us. The failure is announced on the diagnostic
    stream rather than raised: an unreadable record must not destroy a review that already
    produced bytes.
    """
    paths = outcome.record_paths
    if not paths:
        return None
    try:
        record = telemetry.read_record(paths[-1])
    except (OSError, ValueError) as exc:
        print(
            f"[request] the run record at {paths[-1]} could not be read "
            f"({type(exc).__name__}: {exc}); the server-reported model is recorded as "
            f"null rather than filled from the request.",
            file=err,
            flush=True,
        )
        return None
    model = record.get("model")
    if not isinstance(model, dict):
        return None
    reported = model.get("reported")
    return reported if isinstance(reported, str) else None


def _reported_transport(telemetry, outcome, err) -> str | None:
    """What initialize reported for the transport serving the completed Codex turn."""
    paths = outcome.record_paths
    if not paths:
        return None
    try:
        record = telemetry.read_record(paths[-1])
    except (OSError, ValueError) as exc:
        print(
            f"[request] the run record at {paths[-1]} could not be read "
            f"({type(exc).__name__}: {exc}); the transport identity is recorded as null.",
            file=err,
            flush=True,
        )
        return None
    transport_identity = record.get("transport")
    if not isinstance(transport_identity, dict):
        return None
    reported = transport_identity.get("reported")
    return reported if isinstance(reported, str) else None


def _frame_dispositions(outcome) -> tuple[str | None, tuple[str, ...]]:
    """§4 of the frame-disposition rule: what the caller is told about the frames the reader
    kept but did not act on, and WHICH CHANNEL each thing goes down.

    `supervisor` keeps and counts these per ATTEMPT, so the aggregation is over every attempt
    of the run rather than the last one: a line lost on the first attempt was lost from the
    answer this run is about to deliver, and a resume does not un-lose it.

    THE BLOCKING ARM IS §7'S, AND ITS CRITERION WAS FIXED BEFORE THE MEASUREMENT THAT DECIDED
    IT. An unreadable frame raises the error condition, which is one-directional — it can only
    make the verdict more blocking and it never deletes a byte of the answer. The other two are
    notes, which author nothing: a question nothing answered can at worst leave a stall, and a
    stall is reported as an incomplete run rather than as a pass, so blocking on one would be
    the wrongly-blocks harm §6 refuses by name.

    THE PER-NAME TALLY OF DELIBERATELY IGNORED ANNOUNCEMENTS IS ABSENT FROM BOTH, and that is
    §4's third line rather than an omission: it goes to the durable run record only, where
    `supervisor` already writes it. Ignoring those frames is correct behaviour, the real server
    sends about two hundred of them on every healthy run, and reporting correct behaviour on a
    fault channel teaches a caller to stop reading it.

    NO FRAME'S OWN BYTES ARE COPIED INTO WHAT IS RETURNED HERE. The counts are the tool's and
    the bytes are the server's; §7.8a's layer split is the guarantee that nothing the far side
    wrote reaches the tool-authored key space, and a method name lifted off the wire into a
    note would be exactly that with a smaller blast radius. The kept frames — raw bytes and
    all — are on the run record, which is where §5 puts the forensics.
    """
    unparseable: list = []
    server_requests: list = []
    late_responses: list = []
    for attempt in outcome.attempts:
        unparseable.extend(attempt.unparseable_frames)
        server_requests.extend(attempt.server_requests)
        late_responses.extend(attempt.late_responses)

    notes: list[str] = []
    if server_requests:
        notes.append(
            f"the reviewer's server asked this run {len(server_requests)} question(s) and "
            f"nothing answered them, which is this build's deliberate decision rather than a "
            f"fault. They are kept whole on the run record. An unanswered question the caller "
            f"cannot see is a hang with no explanation, which is why it is said here; it moves "
            f"no verdict."
        )
    if late_responses:
        notes.append(
            f"{len(late_responses)} repl(y/ies) arrived after this run had stopped waiting for "
            f"them, and are kept whole on the run record. They carry no answer text, so none "
            f"can cost a finding — but an unmatched reply means this run's protocol bookkeeping "
            f"and the server's had diverged."
        )

    condition = None
    if unparseable:
        condition = (
            f"{len(unparseable)} line(s) of the reviewer's stream could not be read as a "
            f"message at all, and their raw bytes are kept on the run record. The answer above "
            f"is delivered whole and nothing was trimmed to say this — but an unreadable line "
            f"may have carried part of it, and no healthy run of this build has ever produced "
            f"one (frame-disposition rule §7), so one is treated as evidence that something was "
            f"lost rather than as ordinary noise"
        )
    return condition, tuple(notes)


def _both_conditions(existing: str | None, frames: str | None) -> str | None:
    """Two error conditions on one delivery, with neither replacing the other.

    The partial-output arm below ALREADY passes a condition of its own, unconditionally.
    Assigning the frame condition over it would delete a real condition in order to report a
    different one — the shape of the defect this change exists to end, arriving through the fix
    itself. Both are said, in the order they were decided.
    """
    present = [text for text in (existing, frames) if text]
    if not present:
        return None
    return "; and, separately, ".join(present)


def _publish_quietly(result, handle: str, document: dict, err) -> dict:
    """Put an answer at the handle, and RETURN THE DOCUMENT THE HANDLE ACTUALLY CARRIES.

    §7.7's hole is a caller left with a handle and no place the answer ever arrived. An
    already-published handle is `result` enforcing R19 — the first answer is never
    overwritten — and that is a correct refusal, not a failure of this run.

    IT IS NOT A PUBLICATION OF THE DOCUMENT PASSED IN, AND SWALLOWING IT WITHOUT SAYING SO
    IS HOW EVERY CALLER BELOW CAME TO DESCRIBE A DOCUMENT THE STORE REJECTED. The exit code
    was derived from the new document while a collect returned the older stored one, and the
    refusal path announced its failure document as published when the store had kept
    something else — success reported for a publication that did not happen. So the
    disposition is returned rather than absorbed: `document` itself when this call is what
    the handle now carries, and the STORED document otherwise. Identity, not equality, is
    what tells the two apart at the call sites.

    First-write-wins is untouched. `result` still refuses, the first answer still stands,
    and nothing here retries, replaces or re-publishes.
    """
    try:
        result.publish(handle, document)
    except result.AlreadyPublished as exc:
        print(
            f"[request] {handle} already carries a different answer; keeping it and "
            f"discarding this one: {exc}",
            file=err,
            flush=True,
        )
        return result.collect(handle)
    return document


def _launch(
    request: ReviewRequest,
    out,
    err,
    handle_box: list[str],
    *,
    reviewer: str = REVIEWER_ACCEPTED,
) -> tuple[str, int]:
    """Start the review, emit the handle, and return (handle, exit code).

    The sibling components are imported HERE rather than at module import, and that is
    deliberate: every refusal above is decided before any of them is needed, and a refusal
    path that cannot execute because the transport failed to import is a refusal that does
    not refuse.

    WHAT IS IMPORTED BEFORE THE HANDLE EXISTS IS EXACTLY WHAT PUBLISHING AN ANSWER
    REQUIRES, AND NOTHING ELSE. `result` owns the handle and the store; `envelope` authors
    every document that can be put under a handle, including the failure document
    `_emit_no_review` writes. Every remaining component is imported after the handle has
    been printed, so a component that fails to import still leaves the caller a durable
    name WITH the failure document published under it.

    The split is by PRECONDITION rather than by position. What the failure path needs in
    order to emit at all goes before the promise is made; what only the success path needs
    goes after.

    `envelope` earns that position rather than inheriting it. `result` already imports
    `envelope`, so an unimportable `envelope` never yielded a usable handle in the first
    place — but that is a property of another component's import list, which is not this
    module's to rely on. Naming the import here makes it a property of THIS ordering. If
    either cannot be imported, no handle is issued.

    The handle is written and flushed BEFORE the work starts, and it is put in `handle_box`
    in the same breath. §7.7's hole was a launcher that returns a handle, dies, and leaves
    the caller with no place to recover the answer from; a handle that only reaches the
    single exit on the SUCCESS path is that hole with extra steps, because every refusal
    below happens after the caller already has the name.

    THE ORDER BELOW IS THE WHOLE OF THIS MODULE'S INTEGRATION JOB, and each step names the
    component that owns it.
    """
    import envelope
    import result

    handle = result.allocate()
    handle_box.append(handle)
    print(handle, file=out, flush=True)

    import packet
    import supervisor
    import telemetry
    import transport
    import claude_reviewer
    import family_attestation

    # 1. THE PINNED ASSETS (`packet`, §7.2a-6). Three refusal branches — absent, unreadable,
    #    digest-mismatched — and all three refuse the run rather than grade without a rubric.
    assets = packet.load_assets()

    # 2. ACQUISITION (`packet`, §3 correction (a)).
    acquisition = _acquire(request, packet)

    # 3. THE PACKET (`packet`, §7.2a-2). The rubric is injected by the tool; there is no
    #    parameter here through which caller bytes land above the fences.
    contract_text = (
        None
        if request.contract_path is None
        else _read_text(request.contract_path, "the artifact contract", request.artifact_class)
    )
    #    A CONTRACT FILE THAT HOLDS NOTHING IS A DEPLOYMENT FAULT, refused here rather than
    #    carried into the packet. `packet` appends the contract row on the file being
    #    PRESENT and writes its packet note on the text being NON-EMPTY, so an empty file
    #    puts an empty contract row in front of the model while the packet's own note tells
    #    that same model no contract was appended — the packet and its self-description
    #    disagree, and a whitespace-only file gets the row and a note claiming a contract
    #    that says nothing. Repairing only the note would leave the real fault in place: the
    #    caller named a class, this deployment cannot supply its contract, and I6c's
    #    fail-closed rule says refuse rather than grade without it. `--contract none` is the
    #    supported way to declare that no contract applies; an empty file is not that
    #    declaration, it is a broken deployment of one.
    if contract_text is not None and contract_text.strip() == "":
        raise Refusal(
            T_EMPTY_CONTRACT_FILE,
            f"Artifact class {request.artifact_class!r} resolves to {request.contract_path}, "
            f"which is readable but holds no contract text. The class is recognised, so this "
            f"is a deployment fault rather than a caller mistake. The review is refused "
            f"rather than run against a contract that says nothing — pass "
            f"{FLAG_CONTRACT} {CONTRACT_NONE} if no contract is meant to apply.",
            artifact_class=request.artifact_class,
        )
    caller_prompt = (
        ""
        if request.prompt_file is None
        else _read_text(request.prompt_file, "the caller review prompt", request.artifact_class)
    )
    try:
        built = packet.build_packet(
            assets=assets,
            artifact_text=acquisition.text,
            caller_prompt=caller_prompt,
            artifact_class=request.artifact_class,
            contract_text=contract_text,
            acquisition_notes=acquisition.notes,
        )
    except packet.PacketRefusal as refusal:
        # R6 — THE EVIDENCE GATE, and it is the only row in the register that can manufacture
        # a PASS. THE MEASUREMENT IS `packet`'s. This module forwards its verbatim text and
        # never re-derives it or substitutes a cheaper test: a non-empty source is not the
        # same thing as a reviewable one, and the two refusals are two measurements rather
        # than two wordings.
        raise Refusal(
            T_NOTHING_TO_REVIEW,
            f"{request.source}: {refusal}",
            artifact_class=request.artifact_class,
        ) from refusal

    # 4. THE FENCE (`transport`, §7.5a rule 1, §7.5c).
    #
    #    ONE clean empty directory per launch, and it is the session's `cwd` on every call
    #    the transport makes. Codex's project-scoped discovery — skills, hooks, project
    #    docs, repo config, MCP — is keyed on `cwd`, and a directory with nothing in it has
    #    nothing to discover. The reviewed repository is never this directory, on any call;
    #    the artifact reaches the model as packet text and by no other route (rule 3).
    #
    #    It is ONE directory rather than one per session because §5.1 records `cwd` on the
    #    run record and a resume must pass THE SAME ONE. `transport` re-checks that it is
    #    empty at every construction, so a session that left files behind fails loudly at
    #    the next attempt instead of quietly reviewing inside its own leavings.
    session_cwd = tempfile.mkdtemp(prefix="discriminator-session-")

    # The Codex -> Claude route uses Claude CLI's structured-output contract. The
    # returned wrapper carries modelUsage metadata; that metadata is attested before any
    # model bytes can reach `envelope.deliver`. A missing or same-family identity raises and
    # reaches the existing no-review failure path rather than being labelled a review.
    if reviewer == "claude":
        try:
            claude_result = claude_reviewer.run_review(
                built.text,
                envelope_schema.ENVELOPE_SCHEMA,
                session_cwd,
                timeout_seconds=supervisor.THE_TIMEOUT_SECONDS,
                attempts=supervisor.MAX_ATTEMPTS,
            )
        finally:
            shutil.rmtree(session_cwd, ignore_errors=True)
        provenance = _provenance(
            envelope,
            handle,
            claude_result.reported_model,
            CHANNEL_RESULT_HANDLE,
            requested_model=CLAUDE_MODEL_UNSPECIFIED,
            attested_route=claude_result.attested_route,
        )
        delivered = envelope.deliver(
            model_text=claude_result.model_text,
            artifact_class=request.artifact_class,
            provenance=provenance,
        )
        stored = _publish_quietly(result, handle, delivered, err)
        return handle, exit_code_for(stored.get("verdict"))

    if reviewer != REVIEWER_ACCEPTED:
        raise family_attestation.FamilyAttestationError(
            f"reviewer {reviewer!r} has no transport; refusing rather than substituting one"
        )

    # 5. THE RUN PLAN (`supervisor`, §5.1). Authored here, carried there.
    plan = supervisor.RunPlan(
        # The OPAQUE bundle `transport` applies to `thread/start` and, on a resume, to
        # `thread/resume` (§5.3 — the settings are named fields of resume, so a resume that
        # omits them is a bug we would have to write). The keys are `transport`'s own
        # parameter names. `cwd` is deliberately NOT among them: `transport` takes no
        # per-call `cwd` argument, and that absence IS §7.5c's one enforced path. `model`
        # is normally absent too, for the reason at REVIEWER_MODEL.
        session_settings=reviewer_model_setting(),
        packet_text=built.text,
        # `envelope_schema` owns the ONE schema definition. `transport` forwards it exactly
        # as given and `envelope` enforces it; nothing here copies or edits it (§3.1).
        output_schema=envelope_schema.ENVELOPE_SCHEMA,
        cwd=session_cwd,
        artifact_class=request.artifact_class,
        contract=CONTRACT_NONE if request.contract_path is None else str(request.contract_path),
        model_requested=MODEL_UNSPECIFIED if REVIEWER_MODEL is None else REVIEWER_MODEL,
        transport_requested=TRANSPORT_REQUESTED,
        packet_digest=built.packet_digest,
        rubric_version=built.rubric_version,
    )

    # 6. THE TRANSPORT SESSION FACTORY (`transport`, §4.1a).
    #
    #    `supervisor` takes a FACTORY rather than a session because §5.6 makes a resume an
    #    attempt, and an attempt after a server death needs a new process. `initialize` is
    #    mandatory before `thread/start` (§4.1a) and belongs to `transport`, so the session
    #    `supervisor` receives is already through the handshake.
    def session_factory():
        session = transport.Transport(session_cwd=session_cwd)
        session.initialize()
        return session

    # 7. THE RUN (`supervisor`). It owns the ONE timeout, the liveness ledger, the durable
    #    record, the kill, the at-most-two attempts and the single terminal update. Nothing
    #    here holds a duration, a budget or a retry (C-34 SEVERE; C-4's retry is that second
    #    attempt and it is spent there).
    #
    #    The record lives in this run's own directory, which `result` owns and isolates per
    #    run (§7.7); `supervisor` owns the filename inside it and every field in the file.
    try:
        outcome = supervisor.Supervisor(result.run_dir(handle)).run(plan, session_factory)
    finally:
        # The session directory is scratch, not an answer: nothing durable is written to it
        # and `result` holds everything that is. Removal is best-effort because a child that
        # has not finished exiting can still hold a handle open on Windows, and failing a
        # completed review over a leftover scratch directory would be absurd.
        shutil.rmtree(session_cwd, ignore_errors=True)

    # 8. DELIVERY (`envelope`, §7.8a). Layer 1 is the model's bytes, immutable and
    #    byte-exact; layer 2 is tool-authored and the model can never write into it.
    #
    #    §5.4a decides which bytes go where and this module does not re-decide it: the
    #    partials are never discarded and never silently merged. Every partial is retained
    #    beside the answer through `result` (R15), so no byte is lost, and the delivered
    #    text is the model's completed answer where there is one and the latest partial
    #    where there is not.
    for index, partial in enumerate(outcome.partial_outputs, start=1):
        result.retain(handle, f"partial-{index}.txt", partial.encode("utf-8"))

    # CHANNEL_RESULT_HANDLE because the publication below is this document's ONLY delivery:
    # a launch writes no answer to stdout, so there is no other route it could have taken.
    reported_model = _reported_model(telemetry, outcome, err)
    reported_transport = _reported_transport(telemetry, outcome, err)
    attested_route = None
    if outcome.complete and outcome.final_output is not None:
        attested_route = family_attestation.attest_codex(reported_model, reported_transport)
    provenance = _provenance(
        envelope,
        handle,
        reported_model,
        CHANNEL_RESULT_HANDLE,
        attested_route=attested_route,
    )

    # §4 of the frame-disposition rule, decided ONCE and above both delivery arms. A caller
    # told about a lost line on one path and not on the other is the same silence with a
    # narrower mouth, and the arm below it is exactly the one a torn run can land in.
    frame_condition, frame_notes = _frame_dispositions(outcome)

    if outcome.complete and outcome.final_output is not None:
        delivered = envelope.deliver(
            model_text=outcome.final_output,
            artifact_class=request.artifact_class,
            provenance=provenance,
            error_condition=frame_condition,
            caller_notes=frame_notes,
        )
    elif outcome.partial_outputs:
        # §5.4a: *"if a resume yields nothing, the partial IS the content, forwarded under a
        # tool-typed verdict that cannot be a pass."* `error_condition` is what makes it so —
        # it can only make the verdict more blocking and it never deletes a byte.
        failure = outcome.tooling_failure
        delivered = envelope.deliver(
            model_text=outcome.partial_outputs[-1],
            artifact_class=request.artifact_class,
            provenance=provenance,
            error_condition=_both_conditions(
                (
                    "the review did not complete and no further attempt is available"
                    if failure is None
                    else f"{failure.reason}: {failure.detail}"
                ),
                frame_condition,
            ),
            caller_notes=frame_notes,
        )
    else:
        # No model bytes at all. R17's structural half: `no_review` has no parameter for
        # model bytes, so a tooling failure can never be authored over an answer that was
        # already received — and that is exactly why this arm is reached only when there
        # was none.
        #
        # THE FRAME DISPOSITIONS ARE NOT CARRIED HERE, and that is the layer split rather
        # than an oversight: `no_review` takes neither channel, because it already reports a
        # run that produced nothing under a reserved status no counts-only reader can read as
        # a pass. There is no answer for a lost line to have been lost FROM. The dispositions
        # are on the run record either way (§5).
        failure = outcome.tooling_failure
        delivered = envelope.no_review(
            status=envelope.STATUS_TOOLING_FAILURE,
            fault=envelope.FAULT_UNKNOWN_REVIEW_STATE,
            # None, and it is the honest value rather than a placeholder: this arm is a LAUNCH
            # that reached a reviewer and got nothing back. `describe_run` was never consulted
            # here, so there is no run state to report and composing one would be provenance
            # nothing read.
            run_state=None,
            title=T_DID_NOT_RUN,
            detail=(
                "The review produced no model output."
                if failure is None
                else f"{failure.reason}: {failure.detail}"
            ),
            # The request reached a reviewer and the reviewer answered with nothing, so
            # there is no condition in the request to resolve first — launching again is
            # the only move that can produce an answer. The second sentence is what keeps
            # an operator from relaunching indefinitely: a run that produces nothing twice
            # locates the fault somewhere this request cannot reach.
            recommendation=(
                "Launch the review again. If a second run also produces no output, the "
                "fault is in the reviewer or its transport rather than in this request."
            ),
            artifact_class=request.artifact_class,
            provenance=provenance,
        )

    # THE EXIT CODE IS THE STORED DOCUMENT'S. R19 makes the first answer the one a collect
    # returns, so a code derived from a document the store rejected would send the caller a
    # verdict nothing can read back — and 0 means approve, which is the direction that costs.
    stored = _publish_quietly(result, handle, delivered, err)
    return handle, exit_code_for(stored.get("verdict"))


def _collect(handle: str, out, err) -> int:
    """Return an already-published answer — or say what the run's own record says instead.

    `result` owns the store, idempotence and what a handle names. `envelope` owns the
    write, because R2 puts every payload write on stdout behind one guarded site — this
    module chooses neither the bytes nor the encoding, only that this is where a collect
    lands.

    AND WHERE NO ANSWER IS PUBLISHED, THIS IS THE ONE PLACE THE STATE OF THE RUN IS SAID.
    `result` is a byte-reader: it reports that no answer is present and states no cause,
    because it consulted no evidence for one. Measured on the form that did state a cause —
    a review that was running, and that published normally a few minutes later, was
    reported to its caller as a launch that had ended, while the liveness signals sat unread
    in the very file the message named. Nothing was destroyed by that; the cost is an
    operator who spends a second review because the first was called finished.

    So the diagnosis is asked of the component that WRITES the run record, and this function
    only composes what came back. It re-implements nothing: the states are `supervisor`'s
    constants and every value shown is a field `supervisor` read. No process is probed, no
    clock is read, no duration is decided and nothing waits — the record is the whole of the
    evidence, and each answer returns immediately.

    THE DOCUMENT GOES TO STDOUT, and `_emit_no_review`'s own test is what decides that:
    nothing has been written to stdout on a collect, so stdout is where the caller's answer
    belongs. Passing the handle would route it into the STORE instead, and on the in-flight
    state that is the worst thing this path could do — R19 keeps the first answer, so a
    *not finished yet* document published under the handle would be the answer the running
    review is about to publish, refused. The caller would be told the review is unfinished
    by the very document that guarantees it can never finish.
    """
    import envelope
    import result

    try:
        delivered = result.collect(handle)
    except result.NotPublished:
        # `Unreadable` and `UnknownHandle` are deliberately not caught beside it. "Bytes are
        # there and could not be read" and "this store holds no such run" are different
        # facts with their own correct messages already, and answering either with a
        # question about the run's STATE answers something the caller did not ask.
        #
        # `supervisor` is imported HERE rather than beside `result` above so that collecting
        # an answer that IS published cannot fail on it. Reading back a delivered review is
        # what §7.7's durable store exists to promise; it does not acquire a new way to
        # break in order to improve a message on the path where there is nothing to read.
        import supervisor

        state = supervisor.describe_run(result.run_dir(handle))

        # Only the fields the record actually carried, and it is one suffix rather than four
        # spellings. A state with no usable record contributes nothing to it, which is how
        # *and nothing more* is obtained from the data instead of from a rule someone has to
        # keep true: no-record says nothing here at all, and an unreadable record names the
        # file and no field of it.
        carried = ", ".join(
            f"{label} {value}"
            for label, value in (
                ("record", state.record_name),
                ("attempt", state.attempt),
                ("started", state.started_at),
                ("ended", state.ended_at),
            )
            if value is not None
        )
        carried = f"\n\nFor an operator: {carried}." if carried else ""

        # THE FAULT IS NAMED PER BRANCH, AND THE SPLIT THE TEXT HERE PRE-REGISTERED HAS NOW
        # BEEN MADE. It read that in-flight was "the state a follow-on may split off —
        # 'nothing is wrong, it is simply not done' is neither existing value — and a shared
        # assignment is what would have to be undone before that split could be made." The
        # per-branch assignment is exactly what made it cost one line. In-flight now carries
        # FAULT_REVIEW_UNFINISHED; the other three still carry FAULT_UNKNOWN_REVIEW_STATE,
        # which is what each of THOSE states is — the tool cannot know what the review would
        # have said, and on those three something is also genuinely wrong.
        #
        # AND THE RECOMMENDATION IS THE FIELD AN OPERATOR ACTS ON, so it is decided in the
        # same branch that decided the diagnosis. The one string this file used to emit for
        # every state told an operator to re-run — including on the in-flight state, whose
        # own detail text says in the same document that there is no need to launch a second
        # review. A document cannot carry both and be acted on.
        if state.state == supervisor.RUN_STATE_IN_FLIGHT:
            # The rule's §5: the last-sign-of-life figure is already written on every run
            # record and reaches nobody. This is where it reaches the caller, so the fix
            # delivers a scoped v1 item rather than adding one. It is the record's own
            # figure as the record last wrote it — nothing here reads a clock, and nothing
            # compares it against a threshold, which would be a second place a duration is
            # decided (C-34).
            quiet = (
                ""
                if state.quiet_seconds is None
                else (
                    f" Its own record puts the last sign of life at "
                    f"{state.quiet_seconds:.1f}s, as of when the record was last written."
                )
            )
            title = "Discriminator review is not finished"
            detail = (
                f"{handle} names a run whose record does not record that it ended, so on the "
                f"only evidence this tool has the review has not been reported finished and no "
                f"answer is published under this handle.{quiet} NOTHING HERE WATCHED A PROCESS: "
                f"this document cannot tell a review that is still working from one whose "
                f"process is gone, and it does not predict which of the two this is.{carried}"
            )
            recommendation = (
                "Collect this handle again; an answer published under it is returned from "
                "then on. Before launching a second review, collect once more and compare "
                "this document's own figures against this one — this tool watched no "
                "process, so it tells you neither to relaunch nor not to."
            )
            fault = envelope.FAULT_REVIEW_UNFINISHED
        elif state.state == supervisor.RUN_STATE_ENDED_UNPUBLISHED:
            # THIS BRANCH USED TO FORECAST THE HANDLE, AND THE FORECAST WAS FALSE. It said the
            # handle would not fill in later and that the review had to be launched again —
            # with stated confidence. `describe_run` answers for the highest attempt PRESENT
            # ON DISK, a retry cannot have a record until it has a thread id, and the retry
            # runs against THIS handle's own directory. So between one attempt's terminal
            # write and the next attempt's first write, a live review reads as ended here, and
            # an operator following the recommendation launched a duplicate of a review that
            # was still running. Driven through the real retry loop, not argued.
            #
            # The state is still reported exactly as the record states it. What is gone is the
            # claim about what happens NEXT, which no record carries: the attempt number will
            # not carry it either, because a later session may resume an interrupted record
            # and re-enter above the bound.
            title = "Discriminator review ended without publishing an answer"
            detail = (
                f"{handle} names a run whose record says it ended, and no answer was "
                f"published under it. That is what the record states about the run it "
                f"describes, and this document does not predict the handle: a retry of the "
                f"same review runs against this same handle and would publish under it. "
                f"Collect this handle again before launching a second review.{carried}"
            )
            recommendation = (
                "Collect this same handle again. A retry of the same review publishes under "
                "this handle, so launching a second review may duplicate one that is still "
                "running."
            )
            fault = envelope.FAULT_UNKNOWN_REVIEW_STATE
        elif state.state == supervisor.RUN_STATE_NO_RECORD:
            # THIS BRANCH IS THE ENDED BRANCH'S TWIN, AND IT KEPT THE DEFECT FOR A SESSION AFTER
            # THAT ONE WAS FIXED. The handle is allocated and PRINTED before the packet is built
            # and before the supervisor writes any record, so a collect inside that window is a
            # legitimate no-record read of a run that is starting normally. Telling the operator
            # to launch again there duplicates a review about to publish under this same handle.
            #
            # THE DIAGNOSIS BELOW IS RIGHT AND STAYS. Never-launched, launched-and-lost and
            # not-this-tool's-directory really are indistinguishable from here, and this branch
            # must not guess between them. What was wrong was the confident instruction at the
            # end of it, which is exactly what was wrong one branch over.
            title = "Discriminator has no record of a run at this handle"
            detail = (
                f"{handle} holds no run record, so this tool has no evidence about the run "
                f"at all. Never launched, launched and lost, and not this tool's directory "
                f"are indistinguishable from here, and naming one of them would be a cause "
                f"nothing was consulted for.{carried}"
            )
            recommendation = (
                "Check this is the handle the launch printed, then collect it again. The "
                "handle is printed before the run record exists, so a launch that is still "
                "starting looks exactly like one that never happened, and a second review "
                "started here may duplicate one that is about to publish."
            )
            fault = envelope.FAULT_UNKNOWN_REVIEW_STATE
        elif state.state == supervisor.RUN_STATE_RECORD_UNREADABLE:
            title = "Discriminator run record is present and could not be read"
            detail = (
                f"{handle} holds a run record this tool could not read, and no answer is "
                f"published under it. What state the run is in is therefore unknown: an "
                f"unreadable record is not an absent one and it is not an ended one, and "
                f"answering with either would be a claim the evidence does not carry."
                f"{carried}"
            )
            recommendation = (
                "Do not conclude the run ended, and do not conclude it never started. "
                "Preserve the run directory before relaunching, because it is the only "
                "evidence about this handle."
            )
            fault = envelope.FAULT_UNKNOWN_REVIEW_STATE
        else:
            # A state this file has no message for gets no message from this file. Composing
            # one would be the guess the whole change exists to remove, so the byte-reader's
            # own refusal stands instead — it names the missing answer and states no cause.
            raise

        # ARTIFACT_CLASS_UNRESOLVED because a collect names a handle and nothing else: the
        # class this run was launched under is not a thing this path resolved, and writing
        # one down would be provenance nothing here read. `None` for the handle routes the
        # document to stdout — see the docstring; it is the store this must stay out of.
        _emit_no_review(
            title,
            detail,
            recommendation,
            fault,
            ARTIFACT_CLASS_UNRESOLVED,
            None,
            out,
            err,
            # `supervisor`'s own constant and the record's own figure, both carried VERBATIM.
            # This function chose its branch on `state.state` and then stopped there, so four
            # states reached the caller as four prose titles and one shared fault: the
            # distinction was computed and discarded at the last step, and a consumer could
            # tell the states apart only by READING. These two lines are that distinction
            # reaching the program. Nothing is re-derived and nothing new is measured.
            run_state=state.state,
            quiet_seconds=state.quiet_seconds,
            last_frame_at=state.last_frame_at,
        )
        return EXIT_NO_REVIEW

    envelope.write_payload(delivered, out, diagnostic_stream=err)
    return exit_code_for(delivered.get("verdict"))


def _emit_no_review(
    title: str,
    detail: str,
    recommendation: str,
    fault: str,
    artifact_class: str,
    handle: str | None,
    out,
    err,
    *,
    run_state: str | None,
    quiet_seconds: float | None = None,
    last_frame_at: str | None = None,
    requested_model: str | None = None,
) -> None:
    """The tooling-failure document, in the TWO calls `envelope` actually offers.

    `no_review` builds it and `write_payload` is the one write; there is no single-call
    emitter, and there must not be, because §7.8a's layer split is what makes a reserved
    status unforgeable.

    `fault` AND `recommendation` ARE PARAMETERS BECAUSE NEITHER IS KNOWABLE HERE. This
    function is reached from three different conditions and it can see none of them: R4's
    split is a real split, and a caller-input fault and a fault in the tool itself are the
    two halves it exists to keep apart — deciding one of them here made every condition
    the same fault. The recommendation is worse, because it is the field an operator acts
    on: one string for three conditions told an operator to re-run a review that was still
    running. Both are decided where the condition is known and carried in from there.

    WHERE THE DOCUMENT GOES IS DECIDED BY WHETHER A HANDLE WAS ALREADY PRINTED, and the
    two cases are exclusive rather than cumulative:

      * NO handle — nothing has been written to stdout, so stdout is where the caller's
        answer belongs and the document is written there. This is every refusal decided
        before the launch: an unknown flag, a missing class, an undeclared reviewer.
      * A handle — §7.7 has already put a HANDLE and nothing else on stdout, and the
        launch contract says that is the whole of it. Writing the document there too
        would leave stdout carrying a handle AND a JSON document, and every caller that
        reads the stream as a handle would break on the concatenation. So the document is
        published UNDER the handle, where a caller looks for an answer, and stdout is left
        alone. The failure is still produced, still published and still collectable — the
        fix for a corrupt-stdout fault must not be a lost review.

    The handle case announces itself on the DIAGNOSTIC stream, because an operator
    watching a launch that refuses needs to be told the refusal exists and where it went,
    and stderr is the one stream that carries no payload.

    AND THE RECORDED CHANNEL IS DECIDED BY THAT SAME TEST, so it cannot drift from the
    route actually taken: a document written to stdout says so, and `result-handle` is
    reserved for one published under a handle. A caller reads that field to decide where
    the document is.

    AND IF THE STORE WILL NOT TAKE IT, STDOUT STILL DOES NOT GET IT. §7.7's contract is
    that a launch stream is the handle and nothing else; appending a document to it
    produces a stream that is neither, and no caller following the published contract can
    parse it — the corrupt-stdout fault, arriving through the code written to avoid a lost
    review. So the document is lost from the store and NOTHING about that is silent: the
    store failure, the title and the detail all go to the diagnostic stream, and the exit
    code the caller already returns for this path says no review happened. An
    already-published handle is a different case again: the handle names a REAL answer —
    the first one, which R19 keeps — so a caller collecting it reads a review rather than
    nothing. What it does NOT name is this document, which was rejected; the announcement
    below therefore says the document was not published instead of claiming it was.
    Measured on both forms of this function: a store failure escapes `main` and takes
    I10's single exit with it, which is why the publication is guarded here at all.
    """
    import envelope

    # The channel is chosen by the SAME test that routes the document, below.
    channel = CHANNEL_STDOUT if handle is None else CHANNEL_RESULT_HANDLE

    document = envelope.no_review(
        status=envelope.STATUS_TOOLING_FAILURE,
        fault=fault,
        title=title,
        detail=detail,
        recommendation=recommendation,
        artifact_class=artifact_class,
        run_state=run_state,
        quiet_seconds=quiet_seconds,
        last_frame_at=last_frame_at,
        provenance=_provenance(
            envelope,
            handle,
            None,
            channel,
            requested_model=requested_model,
        ),
    )

    if handle is None:
        envelope.write_payload(document, out, diagnostic_stream=err)
        return

    import result

    try:
        stored = _publish_quietly(result, handle, document, err)
    except Exception as store_failure:  # noqa: BLE001 - see the docstring; this is the loud arm
        print(
            f"[request] {handle} could not be published "
            f"({type(store_failure).__name__}: {store_failure}); the handle names no "
            f"answer. The failure document is NOT written to stdout — this launch already "
            f"printed a handle, and the launch contract is a handle and nothing else — so "
            f"the review it reported is lost from the store and is said here instead:\n"
            f"[request] no review happened: {title}\n{detail}",
            file=err,
            flush=True,
        )
        return

    if stored is not document:
        print(
            f"[request] no review happened: {title}. This failure document was NOT "
            f"published — {handle} already carried a different answer and R19 keeps the "
            f"first one, which is what {FLAG_COLLECT} {handle} returns.",
            file=err,
            flush=True,
        )
        return

    print(
        f"[request] no review happened: {title}. The failure document is published under "
        f"{handle} and is read with {FLAG_COLLECT} {handle}. It is deliberately NOT written "
        f"to stdout — this launch already printed a handle, and the launch contract is a "
        f"handle and nothing else.",
        file=err,
        flush=True,
    )


def main(argv: list[str] | None = None, out=None, err=None) -> int:
    """THE SINGLE EXIT (I10). One process, one return.

    Every path through this function leaves by the same return. THE TERMINAL UPDATE IS NOT
    WRITTEN HERE AND NO TELEMETRY CALL APPEARS IN THIS FILE: §7.9 puts the telemetry on
    §5.1's run record as fields — *"a field on that record, not a second file — and no
    second exit path"* — and the one terminal write happens inside `Supervisor.run`'s own
    single exit. A refusal decided before a run exists writes no terminal update and that
    is correct — §7.9 row 3: an outcome is written only by the run that had it, and v1
    never synthesises one for a record that simply stops.
    """
    args = list(sys.argv[1:] if argv is None else argv)
    stdout = sys.stdout if out is None else out
    stderr = sys.stderr if err is None else err

    # A box rather than a variable, because the handle must reach the two emitters below
    # even when the launch RAISES after emitting it — which is every refusal decided inside
    # `_launch`. A plain assignment only carries it on the path that returns normally.
    handle_box: list[str] = []

    # The class the parse RESOLVED, if it got that far. The generic arm at the bottom
    # authors a failure document for a fault that may have happened long after parsing
    # succeeded — asset loading, packet construction, supervision, delivery — and naming
    # the class `unresolved` there records a provenance that is simply untrue. It also
    # changes what happens downstream: `unresolved` is not a member of CONTRACT_NAMES, so
    # `envelope.primary_category_for` returns None for it and `envelope`'s absent-category
    # clause takes the deny-by-default direction instead of applying the real class's
    # policy. THE `Refusal` ARM READS IT TOO, AND THAT IS THE CORRECTION. A refusal raised
    # where the class is in hand carries its own; a refusal raised by a check that is handed
    # no class — the reviewer declaration is the live one, since `require_reviewer` takes no
    # artifact class and could not report one — carried `unresolved` even where the parse had
    # already resolved a class. On a valid request with a broken declaration that is false
    # provenance, and it applied the wrong class-policy branch as well. The earlier note here
    # defended that as the conservative direction. The effect it named is real and it is
    # confined to ONE field: `must_fix_high_count`, which `envelope.no_review` states for
    # itself is deliberately artifact-class dependent and settles nothing. What makes this
    # document conservative is carried elsewhere and reads no class at all —
    # `loop_should_continue` is unconditionally true, `verdict` is None, the status is
    # reserved, and the exit code is EXIT_NO_REVIEW. Conservative grading and class
    # provenance are therefore separate things, and misnaming the artifact is not how the
    # first one is obtained.
    artifact_class = ARTIFACT_CLASS_UNRESOLVED
    reviewer: str | None = None
    try:
        invocation = parse(args)

        if invocation.collect_handle is not None:
            return _collect(invocation.collect_handle, stdout, stderr)

        request = invocation.review
        assert request is not None  # parse returns exactly one of the two.
        artifact_class = request.artifact_class

        # Carry the accepted declaration to the single dispatch site. There is
        # exactly one selected transport and no fallback arm.
        reviewer = require_cross_family()

        # I6b states this line literally: the declared opt-out announces itself, so that
        # a run with no contract is visible in the operator's own transcript.
        if request.contract_path is None:
            print(
                f"{FLAG_CONTRACT} {CONTRACT_NONE}: no artifact contract will be appended.",
                file=stderr,
                flush=True,
            )

        _, code = _launch(request, stdout, stderr, handle_box, reviewer=reviewer)
        return code

    except Refusal as refusal:
        # `envelope` is imported HERE rather than at module scope for the reason `_launch`
        # states: a refusal decided before any sibling was needed must not become a refusal
        # that cannot refuse. It is reached at all only because the fault is now this
        # arm's to name, and it costs this path nothing it did not already spend —
        # `_emit_no_review` imports the same module as its first statement.
        import envelope

        _emit_no_review(
            refusal.title,
            refusal.detail,
            # A refusal names a condition the caller or the deployment can actually change
            # — an unknown flag, a missing contract file, an undeclared reviewer — so
            # running the request again after changing it is the whole of what there is to
            # do. This is the one arm where the old unconditional *re-run* was true.
            "Resolve the condition named above and run the request again.",
            # R4's SPLIT is a required argument and flattening it is what the row exists
            # against. Everything reaching here from a `Refusal` is a caller-input or
            # environment fault, and it is mid-loop actionable.
            envelope.FAULT_CALLER_OR_ENVIRONMENT,
            # The resolved class, wherever the refusal has none of its own. See the note on
            # `artifact_class` above: after the parse has assigned it, `unresolved` on a
            # refusal is a check that could not report a class, never a class that is not
            # there — so it is the refusal's missing value, not a contradicting one.
            artifact_class
            if refusal.artifact_class == ARTIFACT_CLASS_UNRESOLVED
            else refusal.artifact_class,
            handle_box[0] if handle_box else None,
            stdout,
            stderr,
            # NOTHING ON THIS ARM CONSULTED `describe_run`, so there is no run state to
            # report and None says exactly that. It is not a claim that no run exists — the
            # unexpected-exception arm below can be reached after a launch — which is why the
            # field means "no run state applies here" and never "no run".
            run_state=None,
            requested_model=(
                CLAUDE_MODEL_UNSPECIFIED if reviewer == "claude" else None
            ),
        )
        return EXIT_NO_REVIEW

    except Exception as unexpected:  # noqa: BLE001 - see below; this is the loud arm
        # §7.4 STEP 1 IS WHY THIS ARM EXISTS. A component that raises has produced no
        # review, and "no review happened" is an ANSWER the caller must receive in the
        # shape it reads answers in. Without this, a raising component leaves a traceback
        # on stderr and NOTHING on the channel the caller parses — which is the silent
        # failure this tool was built against, arriving through the tool itself.
        #
        # This is not `except: pass`. Nothing is swallowed: the type and the message go
        # into the refusal an operator reads, and the exit code says no review happened.
        # `Exception` deliberately does not catch KeyboardInterrupt or SystemExit.
        #
        # If `envelope` itself cannot be imported, the interpreter's own traceback stands
        # and is loud. A second emitter here would be a second author of the delivery
        # document, which is the one thing §7.8a's layer split exists to prevent.
        import envelope

        _emit_no_review(
            T_DID_NOT_RUN,
            f"{type(unexpected).__name__}: {unexpected}",
            # h04: this arm is reached by a component RAISING, which is a fault in the tool
            # rather than in the request — so a caller told to fix their input is told to
            # fix something that is not broken. The hedge is not filler: whether an
            # unchanged re-run reproduces is not measured, and at least one route here (a
            # pinned asset a byte out of date) is fixed by a rebuild and not by a code
            # change.
            "This is a fault in the tool, not in the request. Re-running an unchanged "
            "request will most likely reproduce it.",
            # h04's adjudicated fix. FAULT_CALLER_OR_ENVIRONMENT was this arm's label until
            # now, and it is the one label that is certainly wrong here: nothing about the
            # caller's input is implicated by a component that raised.
            envelope.FAULT_UNKNOWN_REVIEW_STATE,
            artifact_class,
            handle_box[0] if handle_box else None,
            stdout,
            stderr,
            # NOTHING ON THIS ARM CONSULTED `describe_run`, so there is no run state to
            # report and None says exactly that. It is not a claim that no run exists — the
            # unexpected-exception arm below can be reached after a launch — which is why the
            # field means "no run state applies here" and never "no run".
            run_state=None,
            requested_model=(
                CLAUDE_MODEL_UNSPECIFIED if reviewer == "claude" else None
            ),
        )
        return EXIT_NO_REVIEW


if __name__ == "__main__":
    raise SystemExit(main())
