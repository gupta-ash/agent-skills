"""Durable review handles, retained artifacts and idempotent collection.

Each opaque validated handle names one run. This module owns storage locations
and publication, not review content, grading or supervisor run-record semantics.
Envelope encoding is reused so retained bytes preserve its Unicode and finite-
number rules. Published nonempty answers are guarded by bytes read from disk;
unreadable state refuses rather than being mistaken for an absent result.

Publication uses an atomic create-if-absent link where supported. The existing
empty-file replacement and link-less fallback are not competing-writer claims:
a concurrent answer can be overwritten on those paths. Normal tool writes never
create empty artifacts. No timeout or inter-run coordination is introduced here.

Nothing in this module deletes a run or enforces an age policy. Collection does
not consume an answer; retained storage can grow without bound.
"""

from __future__ import annotations

import json
import os
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# The ONE project-local edge this module has, and it is deliberate: `envelope` owns the
# encoding of a delivery document (R2, R18) and this module must not own a second one. The
# edge is acyclic — `envelope` imports only `envelope_schema`, and neither imports this.
from envelope import encode_payload

__all__ = [
    "ResultError",
    "MalformedHandle",
    "UnknownHandle",
    "NotPublished",
    "AlreadyPublished",
    "Unreadable",
    "RetainedArtifactConflict",
    "PublishOutcome",
    "default_store_root",
    "allocate",
    "run_dir",
    "published_path",
    "is_published",
    "publish",
    "publish_bytes",
    "collect",
    "collect_bytes",
    "retain",
    "retained_paths",
    "list_handles",
]


# --- the store's shape ----------------------------------------------------------------

STORE_DIR_NAME = ".discriminator"
RUNS_DIR_NAME = "runs"

#: The published answer. One per run, written once, never overwritten with a different
#: document. `envelope` owns what is inside it.
RESULT_FILENAME = "result.json"

#: THE RUN RECORD HAS NO NAME IN THIS MODULE, AND THAT IS THE SETTLEMENT.
#: A second name derived here would disagree with `supervisor.record_filename`, and §5.1's
#: record would then be written under a name no reader looks for. `supervisor.record_filename`
#: is the sole authority. This module owns the DIRECTORY the record sits in and hands that
#: directory to `supervisor`; the filename inside it is not this module's to know.
#: It is not imported to settle this: a reader of the store needs to find files, not to
#: re-derive a name, and an import for a name would put the run's whole machinery behind a
#: diagnostic.

#: In-flight temporaries. Named so that `retained_paths` can tell a promise from a
#: half-written file, and so a caller cannot `retain` something that looks like one.
TEMP_SUFFIX = ".publishing"

_HANDLE_RE = re.compile(r"\Adisc-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}\Z")
_ARTIFACT_NAME_RE = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._-]*\Z")

#: Only the answer is reserved by name. A record cannot be reserved by name from here
#: without re-deriving `supervisor`'s naming, and a set that reserves a name nothing writes
#: protects nothing while reading as though it protects something. What protects a record
#: instead is `retain` refusing to write over existing bytes — which holds whatever the
#: record is called, and holds EXCEPT where those bytes are zero: the residual `retain`
#: declares replaces a 0-byte file of any name it accepts, a record's included.
#: HELD IN THE FOLDED FORM `retain` COMPARES AGAINST, so that a name added here later is
#: reserved against its filesystem aliases too and not merely against its own spelling.
_RESERVED_NAMES = frozenset({RESULT_FILENAME.lower()})


def _folded_name(name: str) -> str:
    """The name the FILESYSTEM will resolve, never the string that was typed.

    ONE fold, consulted by EVERY check at `retain`'s door, because two checks that fold
    differently disagree about what a name IS — and the one that folds less then admits an
    alias for whatever the other refuses. That disagreement is what this helper exists to
    make impossible: measured on this box, the reserved-name check folded and the
    temporary-suffix check did not, so `trace.publishing.` was accepted, landed at
    `trace.publishing`, and `retained_paths` and `_other_files` both reported the durable
    artifact as an in-flight temporary and omitted it — a retained path promised to a caller
    that the store then said it did not hold.

    Windows folds case and strips trailing dots and spaces from a final component, and those
    are the only aliases this grammar can express: `_ARTIFACT_NAME_RE` admits no colon and no
    tilde, so an alternate data stream and an 8.3 short name are already refused by it, and
    every name it does admit is ASCII, where `lower()` is exactly the fold Windows performs.
    The fold is unconditional rather than platform-tested: a store that accepts on one
    platform the name that corrupts it on another is a defect waiting for a machine.

    IT DOES NOT COVER THE RESERVED DEVICE NAMES (`nul`, `con`, `com1`…), which are the same
    folding class and are not closed here. They are refused today by the filesystem rather
    than by this module — measured: the claim in `_atomic_write_bytes` raises
    `FileExistsError`, because the device name is always taken — so nothing is silently
    swallowed, but the refusal is an unnamed `OSError` rather than one of this module's.
    Stated rather than pretended.
    """
    return name.rstrip(". ").lower()


# --- refusals -------------------------------------------------------------------------


class ResultError(Exception):
    """Base for every refusal this module makes."""


class MalformedHandle(ResultError):
    """The token is not a handle this module could ever have issued."""


class UnknownHandle(ResultError):
    """A well-formed handle that names no run in this store."""


class NotPublished(ResultError):
    """The run exists and holds no answer.

    Kept distinct from `UnknownHandle` on purpose: "there is no such run" and "the run's
    directory is there and nothing has been published into it" send an operator to two
    different places. WHICH state sits behind the second — never launched, still running,
    or ended without publishing — is not said here and cannot be said here: this module
    reads bytes at a path and never reads the run record. §7.7's recoverable death is one
    of the three, and asserting it as the only one was measured telling an operator that a
    live review had died.
    """


class AlreadyPublished(ResultError):
    """R19. A different answer is already on disk and publishing would destroy it."""

    def __init__(self, message: str, *, handle: str, path: Path) -> None:
        super().__init__(message)
        self.handle = handle
        self.path = path


class Unreadable(ResultError):
    """The published file could not be read, so what it holds is unknown.

    Not the same as empty and not the same as absent — bytes may well be sitting there.
    Refusing keeps the file; collapsing this into "absent" would overwrite an answer this
    process could not see.
    """


class RetainedArtifactConflict(ResultError):
    """A retained artifact of that name already exists with different bytes.

    R15's rule from the other direction: a path already handed out is a promise, and
    overwriting it is a deletion wearing a write's clothes.
    """


# --- the published outcome ------------------------------------------------------------


@dataclass(frozen=True)
class PublishOutcome:
    """What publication did, so a caller need not re-read the store to find out.

    `wrote` is False on the idempotent path — the same bytes were already there and no
    write was performed. It is a report of what happened, not a guard: the guard is the
    filesystem read inside `publish_bytes`, and nothing in this module ever consults this
    object to decide anything (R19).
    """

    handle: str
    path: Path
    wrote: bool


# --- where the store lives ------------------------------------------------------------


def default_store_root() -> Path:
    """The one place this component keeps runs, decided here and nowhere else.

    No environment variable and no flag: §12 states that v1 adds no configuration surface
    at all, and the fleet-configured store that would own one is v2 (C-44). A per-user
    location is what makes a handle collectable by a LATER session — including one started
    after the launcher died, which is the case §7.7 was written for.

    It is deliberately NOT inside the reviewed tree. §7.5a rule 1 keeps the reviewed
    repository off every path this tool writes.
    """
    return Path.home() / STORE_DIR_NAME / RUNS_DIR_NAME


def _resolve_root(store_root: str | os.PathLike[str] | None) -> Path:
    """Resolve the store root a call operates against.

    The parameter exists for tests and for a caller that owns a store of its own; it is a
    code argument, not a configuration surface. A caller that supplies one owns pairing it
    back on collect — the handle names a run WITHIN a store, not a store.
    """
    root = default_store_root() if store_root is None else Path(store_root)
    if not root.is_absolute():
        raise ValueError(
            f"result: store root must be absolute; got {str(root)!r}. A relative store "
            f"resolves against whatever cwd the process happens to hold, and a handle "
            f"issued under one cwd would then be uncollectable under another."
        )
    return root


# --- handles --------------------------------------------------------------------------


def _new_handle() -> str:
    """A handle: the issuing instant, and a random component wide enough that two runs
    starting in the same second do not collide.

    The timestamp is there so the store sorts usefully for a human reading it, not so
    anything computes with it. Nothing in this module derives a duration from a handle.
    """
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"disc-{stamp}-{secrets.token_hex(6)}"


def _check_handle(handle: Any) -> str:
    """Validate a handle against its own grammar before it reaches the filesystem.

    Handles arrive from a caller's command line. `..`, a separator, a drive letter or an
    absolute path would each escape the store, so the grammar is a whitelist and the check
    is total: anything that is not exactly a handle this module could have issued is
    refused by name.
    """
    if not isinstance(handle, str):
        raise MalformedHandle(
            f"result: a handle is a string; got {type(handle).__name__}."
        )
    if not _HANDLE_RE.match(handle):
        raise MalformedHandle(
            f"result: {handle!r} is not a handle this store could have issued. The form "
            f"is 'disc-<UTC compact timestamp>-<random hex>'."
        )
    return handle


def run_dir(handle: str, *, store_root: str | os.PathLike[str] | None = None) -> Path:
    """The directory holding one run's durable files. Does not require it to exist."""
    root = _resolve_root(store_root)
    directory = root / _check_handle(handle)
    # Defence in depth behind the grammar, and it must NORMALISE before it compares: an
    # unnormalised `parent` of "<store>/.." is "<store>", so the check would pass on the
    # one input it most needs to refuse. A grammar and a containment test fail for
    # different reasons, and this one keeps holding if the grammar is ever widened.
    if os.path.dirname(os.path.normpath(directory)) != os.path.normpath(root):
        raise MalformedHandle(
            f"result: handle {handle!r} does not resolve inside the store at {root}."
        )
    return directory


def published_path(handle: str, *, store_root: str | os.PathLike[str] | None = None) -> Path:
    """Where this run's answer is published."""
    return run_dir(handle, store_root=store_root) / RESULT_FILENAME


def _require_run(handle: str, store_root: str | os.PathLike[str] | None) -> Path:
    directory = run_dir(handle, store_root=store_root)
    if not directory.is_dir():
        raise UnknownHandle(
            f"result: handle {handle!r} names no run in the store at "
            f"{_resolve_root(store_root)}."
        )
    return directory


def allocate(*, store_root: str | os.PathLike[str] | None = None) -> str:
    """Issue a handle and create the run directory it names.

    Called BEFORE any work, because §7.7's hole is a launcher that returns a handle and
    dies: a handle that only becomes real after a successful run is that same hole with
    extra steps. After this returns, the place exists whatever happens next.

    Concurrent-run isolation is here and only here: every run gets its own directory under
    its own unique name, so two runs launched from the same working directory at the same
    instant share nothing. The directory is created exclusively — a collision is raised,
    never absorbed, because absorbing one would hand two runs the same place to write.
    """
    root = _resolve_root(store_root)
    root.mkdir(parents=True, exist_ok=True)
    handle = _new_handle()
    try:
        (root / handle).mkdir(exist_ok=False)
    except FileExistsError as exc:
        raise ResultError(
            f"result: allocated handle {handle!r} already exists in {root}. Two runs must "
            f"never share a directory, so this is refused rather than reused."
        ) from exc
    return handle


# --- the R19 guard --------------------------------------------------------------------

#: The outcomes of looking at a published file, deliberately distinguishable.
#: `ABSENT` and `EMPTY` are both "holds nothing" and both permit a write — that is R19's
#: 0-byte carve-out. `UNREADABLE` is neither, and it permits nothing.
_ABSENT = "absent"
_EMPTY = "empty"
_PRESENT = "present"
_UNREADABLE = "unreadable"


def _published_state(path: Path) -> tuple[str, bytes | None]:
    """Read the answer that is on disk RIGHT NOW.

    This is the whole of R19's guard. It is called at overwrite time, it observes the
    filesystem, and its answer is the bytes themselves rather than a claim about them.
    There is no flag anywhere in this module that records whether a publish has happened,
    because a flag and a rename are two bytecodes and the window between them is the
    failure this row was written from.
    """
    try:
        with open(path, "rb") as handle:
            data = handle.read()
    except FileNotFoundError:
        return _ABSENT, None
    except OSError:
        # Permission, lock, device error. Bytes may well be sitting there; saying
        # "nothing is here" would overwrite an answer this process could not see.
        return _UNREADABLE, None
    if not data:
        return _EMPTY, b""
    return _PRESENT, data


def _existing_answer(target: Path, data: bytes) -> bool | None:
    """Compare what is on disk RIGHT NOW against the bytes a caller asked to write.

    True  — the same bytes are already there, so writing them again would change nothing.
    False — DIFFERENT bytes are there. What that means is the CALLER's to say: the answer
            and a retained artifact are protected by the same rule and refused under two
            different names, and only the caller knows which one it is.
    None  — the path holds nothing: absent, or the 0 bytes R19 carves out.

    `Unreadable` is raised for the third state, which is never collapsed into either of the
    other two. This is the one place that comparison is written, so it cannot come out
    differently before a write and after a lost claim.
    """
    state, existing = _published_state(target)
    if state == _UNREADABLE:
        raise Unreadable(
            f"result: {target} could not be read, so what it holds is unknown. Refusing to "
            f"write over it. The file is kept."
        )
    if state == _PRESENT:
        return existing == data
    return None


# --- atomic writes --------------------------------------------------------------------


def _atomic_write_bytes(directory: Path, name: str, data: bytes, *, replace: bool) -> bool:
    """Put these bytes at `name`, whole or not at all. True when THIS call wrote them.

    The temporary sits in the SAME directory as its target, because a rename is atomic only
    within one filesystem, and its name carries a RANDOM component so two writers never
    share one. A process id does not do that job: two publishers inside one process derive
    the SAME temporary from it, and then one truncates, renames away or cleans up the
    other's file mid-write. `fsync` precedes the rename: "durable" means the bytes survive
    the machine, not merely that the write call returned.

    `replace=False` is the CLAIM, and it is what makes the overwrite guard sound. A hard
    link creates the name and fails if it is taken, in ONE operation — so the target goes
    from absent to whole with no window in which a second writer can see it absent and then
    destroy it. It waits for nothing and names no duration (C-34); the loser is told
    immediately and settles by reading, which is the same guard as everywhere else here.

    `replace=True` is for the one case where there is nothing to destroy: a target holding
    0 bytes, which R19 says is not an answer.

    There is no directory fsync — Windows offers no handle to one. The rename itself is
    atomic on both platforms this tool runs on; what is not guaranteed is that the
    directory entry survives a power loss, and that is stated rather than pretended.
    """
    final = directory / name
    tmp = directory / f"{name}.{secrets.token_hex(8)}{TEMP_SUFFIX}"
    try:
        with open(tmp, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if replace:
            os.replace(tmp, final)
            return True
        try:
            os.link(tmp, final)
        except FileExistsError:
            return False
        except OSError:
            # This filesystem does not do hard links. A rename gives up the claim's
            # atomicity, so it is taken ONLY while the target still holds nothing: a writer
            # that cannot claim atomically must at least never overwrite bytes it can see.
            # Refusing outright instead would fail publication on the FILESYSTEM rather
            # than on the answer, and a publication that cannot happen is a completed
            # review delivered to the caller as "no review happened".
            if _existing_answer(final, data) is not None:
                return False
            os.replace(tmp, final)
        return True
    finally:
        # A failed write must leave nothing behind that a reader could mistake for an
        # answer, and after a successful link the temporary and the target are the same
        # file — so unlinking it costs the target nothing. One cleanup, on every exit.
        try:
            os.unlink(tmp)
        except OSError:
            pass


# --- publication ----------------------------------------------------------------------


def _settle_publication(handle: str, target: Path, data: bytes) -> PublishOutcome | None:
    """Turn `_existing_answer`'s comparison into publication's answer, or None to write."""
    same = _existing_answer(target, data)
    if same is None:
        return None
    if same:
        return PublishOutcome(handle=handle, path=target, wrote=False)
    raise AlreadyPublished(
        f"result: {handle} already holds a different published answer at {target}. "
        f"Refusing to overwrite it — it may already have been collected.",
        handle=handle,
        path=target,
    )


def publish_bytes(
    handle: str,
    data: bytes,
    *,
    store_root: str | os.PathLike[str] | None = None,
) -> PublishOutcome:
    """Publish this run's answer, atomically, and never destroy one already delivered.

    The bytes are stored exactly as given. §7.8a makes layer 1 immutable and forwarded
    byte-exact, and this is the last place those bytes pass through, so re-encoding them
    here would break that promise where nothing could observe it.

    R19, in order:
      * read what is on disk NOW;
      * unreadable -> refuse and keep the file;
      * present and identical -> the idempotent path, no write, no error;
      * present and different -> refuse, because the answer already reached a reader;
      * absent -> CLAIM the name atomically and write, except on the link-less-filesystem
        fallback `_atomic_write_bytes` states for itself. If the claim is lost, a second
        publisher got there inside the window, and the same outcomes are read off disk
        again rather than assumed — the claim is what makes "absent" mean absent at the
        instant of the write, which a read alone can never mean.
      * 0 bytes -> THE RESIDUAL, DECLARED HERE RATHER THAN CLOSED. The claim cannot be
        made against a name that is already taken, so the loser re-reads, finds nothing
        again, and the write that follows REPLACES: an answer a competing publisher lands
        between that re-read and this write is overwritten rather than refused. THE WINDOW
        IS THAT GAP AND NOTHING WIDER. Reaching it needs an actor outside this tool —
        nothing here ever writes a 0-byte result, so the empty file holding the name was
        put in the run directory by something that is not this tool. It stays open because
        every closure available on both platforms costs more than the window does; the R19
        section of the module docstring works each one through.
    """
    if not isinstance(data, (bytes, bytearray)):
        raise TypeError(
            f"result: publish_bytes takes bytes; got {type(data).__name__}. Use publish() "
            f"for a document, or ask `envelope` for the exact bytes it wants stored."
        )
    data = bytes(data)
    if not data:
        raise ValueError(
            "result: refusing to publish zero bytes. A 0-byte answer is indistinguishable "
            "from an unpublished run, and a caller collecting it would read no verdict."
        )

    directory = _require_run(handle, store_root)
    target = directory / RESULT_FILENAME

    settled = _settle_publication(handle, target, data)
    if settled is not None:
        return settled

    if not _atomic_write_bytes(directory, RESULT_FILENAME, data, replace=False):
        # The name was taken between the read above and the claim. That is the race this
        # protocol exists for, and it is settled by reading, never by writing anyway.
        settled = _settle_publication(handle, target, data)
        if settled is not None:
            return settled
        # It is there and holds nothing. R19's carve-out: 0 bytes is not an answer, so it is
        # replaced — the residual this function's docstring declares, and NOT a claim.
        _atomic_write_bytes(directory, RESULT_FILENAME, data, replace=True)
    return PublishOutcome(handle=handle, path=target, wrote=True)


def publish(
    handle: str,
    document: dict[str, Any],
    *,
    store_root: str | os.PathLike[str] | None = None,
) -> PublishOutcome:
    """Publish a JSON document as this run's answer.

    The document is `envelope`'s layer-2 delivery envelope. This module does not inspect
    it, does not validate its keys and does not know its vocabulary — it hands it to
    `envelope`'s encoder and stores the bytes that come back.

    THE ENCODER IS `envelope.encode_payload` AND NOT A SECOND ONE WRITTEN HERE, because
    rules live inside it that a plain `json.dumps` here does not have:
      * a lone surrogate in the model's text makes a strict encode RAISE. `encode_payload`
        re-emits the SAME JSON VALUE with every non-ASCII character escaped instead, so the
        review is published rather than destroyed. Raising here would hand the caller's
        error path a complete review with real findings and let it report "no review
        happened" (R2).
      * a non-finite number is refused rather than written. Python will emit `Infinity` and
        `NaN` happily; no JSON reader accepts them, so the run would publish an answer that
        only Python can read back — silently, since Python's own parser accepts them too.
    Whether the escaping fallback was used is a fact about BYTES, not about the value, so
    it is not carried into the store; a caller that needs to know asks `envelope`.

    Key order is preserved because ordering is `envelope`'s decision, not a storage detail.

    A caller holding an object rather than a dictionary converts it on `envelope`'s side; a
    store that learns how to unwrap a content type has acquired a reason to be forgiving
    about content, which is the coupling §3.1 refuses.

    THE CHECK IS `dict`, NOT `Mapping`. `Mapping` admits values the encoder cannot serialise
    at all: `collections.UserDict` satisfies it and then makes `json.dumps` raise `TypeError`
    from inside `envelope` — a refusal this module never made and never named, arriving at
    the caller as neither a published answer nor a stated reason. Converting the top level to
    a plain dictionary instead would close ONE input and leave the identical failure for a
    mapping nested inside it — measured: a plain dict holding a `UserDict` raises the same
    TypeError — so the conversion buys a guard that reads as total and is not. The contract
    therefore narrows to what `envelope.encode_payload` is annotated to take. Every dict
    SUBCLASS still passes and still serialises; anything else converts on `envelope`'s side
    before it arrives. THE GUARD REMAINS TOP-LEVEL: a value nested deeper that no JSON
    encoder can take still raises out of `envelope`, and that is stated rather than pretended.
    """
    if not isinstance(document, dict):
        raise TypeError(
            f"result: publish takes a JSON object; got {type(document).__name__}. "
            f"`envelope` owns the conversion from its own delivery object to a document."
        )
    try:
        payload, _escaped = encode_payload(document)
    except ValueError as exc:
        raise ValueError(
            f"result: refusing to publish the answer for {handle} — `envelope` could not "
            f"encode it as JSON ({exc}). Nothing was written and no earlier answer was "
            f"touched."
        ) from exc
    return publish_bytes(handle, payload, store_root=store_root)


# --- collection -----------------------------------------------------------------------


def _refuse_json_constant(token: str) -> Any:
    """Python's JSON reader calls this for `Infinity`, `-Infinity` and `NaN`. None of them
    is JSON, so none of them is an answer this store will hand back as one."""
    raise ValueError(
        f"{token} is not a JSON value. The published answer contains a number no JSON "
        f"reader outside Python will accept."
    )


def collect_bytes(
    handle: str, *, store_root: str | os.PathLike[str] | None = None
) -> bytes:
    """Return the published answer's exact bytes.

    Idempotent by construction: this function performs no write of any kind — no marker,
    no timestamp, no move. Collecting is reading, so collecting twice returns the same
    answer and the second caller is not told the answer is gone.

    IT NAMES NO CAUSE WHEN THERE IS NO ANSWER. The refusal carries the handle, the path,
    and the neutral listing of whatever else the run directory holds — and stops there.
    This function has read bytes at ONE path and has never read the run record, so
    "allocated and never published", "the launch died" and "still running" are
    indistinguishable from here. It asserted the second of those for a while, and it was
    measured telling an operator a review had died while that review's own record — the
    file the message itself pointed at — said running, carried no end time, and published
    its answer normally minutes later. Which state it is belongs to the component that OWNS
    the record, and this module still does not know that record's name.
    """
    directory = _require_run(handle, store_root)
    target = directory / RESULT_FILENAME

    state, data = _published_state(target)
    if state == _UNREADABLE:
        raise Unreadable(
            f"result: {target} exists and could not be read. This is not the same as an "
            f"unpublished run — the answer may be intact and unreachable by this process."
        )
    if state in (_ABSENT, _EMPTY):
        raise NotPublished(
            f"result: {handle} holds no answer at {target} ({_other_files(directory)})."
        )
    assert data is not None  # _PRESENT always carries its bytes.
    return data


def _other_files(directory: Path) -> str:
    """What the run directory holds BESIDES the answer, discovered rather than assumed.

    It NAMES what it found and infers nothing from it. `supervisor` owns the run record's
    name; a reader does not need to know it to look in a directory — and not knowing it is
    exactly why an empty listing may not be read back as "no run was ever recorded here".
    That clause used to be appended to the empty case and it was a diagnosis this function
    never made: what it establishes is that nothing else is there AT THE MOMENT IT LOOKED,
    which a run still on its way to writing a record satisfies as fully as a run that never
    started.

    In-flight temporaries are excluded: they are not promises and may vanish at any moment.
    A listing that fails is reported as a failure, never as an empty directory — this is a
    diagnostic on a refusal path, and one that lies is worse than one that is missing.
    """
    try:
        found = sorted(
            path.name
            for path in directory.iterdir()
            if path.is_file()
            and path.name != RESULT_FILENAME
            and not path.name.endswith(TEMP_SUFFIX)
        )
    except OSError as exc:
        return f"the run directory could not be listed: {exc}"
    if not found:
        return "the run directory holds nothing else"
    return "the run directory holds " + ", ".join(found)


def collect(
    handle: str, *, store_root: str | os.PathLike[str] | None = None
) -> dict[str, Any]:
    """Return the published answer as a document.

    `collect_bytes` is the primitive; use it wherever byte-exactness matters (§7.8a). This
    is the convenience for a caller that wants the parsed form, and it fails loudly rather
    than returning something empty-shaped: a store that answers `{}` for a corrupt file is
    a store that reports a broken review as a clean one.

    THE PARSE IS AS STRICT AS THE WRITE. Python's JSON reader accepts `Infinity`, `-Infinity`
    and `NaN`, which are not JSON and which `envelope` refuses to emit; accepting them on the
    way back in would let a document that no other reader can parse look perfectly healthy
    here, and the asymmetry would be discovered by whatever consumes the answer next.
    """
    data = collect_bytes(handle, store_root=store_root)
    try:
        document = json.loads(data.decode("utf-8"), parse_constant=_refuse_json_constant)
    except ValueError as exc:
        # Covers the decode, the parse and the constants refusal alike: UnicodeDecodeError
        # and JSONDecodeError are both ValueError, and naming them separately here would
        # only invite one of the three to be forgotten.
        raise Unreadable(
            f"result: the answer published for {handle} is not readable as a JSON "
            f"document ({exc}). The bytes are kept and are available from collect_bytes."
        ) from exc
    if not isinstance(document, dict):
        raise Unreadable(
            f"result: the answer published for {handle} is a JSON "
            f"{type(document).__name__}, not an object."
        )
    return document


def is_published(
    handle: str, *, store_root: str | os.PathLike[str] | None = None
) -> bool:
    """Whether an answer is on disk for this run.

    Answered by reading the filesystem, like every other question this module answers
    about publication. A caller must not use this as a permission to write — `publish`
    claims the name atomically at write time and that claim, not this read, is the guard
    (R19).

    UNREADABLE IS NOT False. It is the third state the guard exists to keep distinct, and
    collapsing it here would have this function report a published answer as absent — the
    one wrong answer that leads a caller to overwrite a delivered review. It raises for the
    same reason `collect_bytes` does.
    """
    target = _require_run(handle, store_root) / RESULT_FILENAME
    state, _ = _published_state(target)
    if state == _UNREADABLE:
        raise Unreadable(
            f"result: {target} exists and could not be read, so whether this run holds an "
            f"answer is unknown. It is not reported as unpublished."
        )
    return state == _PRESENT


# --- retention ------------------------------------------------------------------------


def retain(
    handle: str,
    name: str,
    data: bytes,
    *,
    store_root: str | os.PathLike[str] | None = None,
) -> Path:
    """Keep a diagnostic artifact with the run, and return the path that names it.

    R15. The returned path is a promise the moment a caller puts it in an envelope or on
    stderr, so this module never deletes it, and never overwrites it with different bytes —
    except on the residual below, and on the link-less-filesystem fallback that
    `_atomic_write_bytes` states for itself. Re-retaining identical bytes is a no-op rather
    than an error, so a retry is not a failure.

    THE 0-BYTE RESIDUAL, DECLARED HERE RATHER THAN CLOSED, and it is publication's exactly.
    A name already held by a file holding nothing cannot be claimed, so the loser re-reads,
    finds nothing again, and the write that follows REPLACES: an artifact another writer
    lands at that name between the re-read and this write is overwritten rather than
    refused. THE WINDOW IS THAT GAP AND NOTHING WIDER. Reaching it needs an actor outside
    this tool — nothing here ever writes a 0-byte artifact, so the empty file holding the
    name was put in the run directory by something that is not this tool. It stays open for
    the reasons the R19 section of the module docstring works through, one closure at a
    time.

    ZERO BYTES ARE REFUSED, for the same reason `publish_bytes` refuses them and by the
    same rule: R19's 0-byte carve-out says a file holding nothing is not content, so an
    empty artifact could never be protected by the conflict check above — it would be
    classified as holding nothing and a later, DIFFERENT call would quietly overwrite the
    very path this function promised never to overwrite. Refusing at the door is the only
    version of this that keeps the promise, and an empty artifact was never worth naming to
    a caller anyway: it says exactly as much as the file that was never written.

    `supervisor` and `transport` decide WHAT is worth keeping — the raw stream, the model's
    unparsed text, the stderr of a failed acquisition. This module decides only that it
    lands beside the run rather than in a temporary directory something else will sweep.
    """
    if not isinstance(data, (bytes, bytearray)):
        raise TypeError(
            f"result: retain takes bytes; got {type(data).__name__}."
        )
    if not isinstance(name, str) or not _ARTIFACT_NAME_RE.match(name):
        raise ValueError(
            f"result: {name!r} is not a usable artifact name. It must be a single plain "
            f"filename — no separators, no leading dot."
        )
    # Both checks below compare the FOLDED name — `_folded_name` says why there is exactly
    # one fold and what it covers. Measured on this box: a retain under `RESULT.JSON` or
    # `result.json.` landed bytes at the publication path, after which `is_published`
    # reported an answer no publish had written, `collect` handed the artifact back as the
    # verdict, and the real publish was then refused by R19; a retain under
    # `trace.publishing.` landed a durable artifact the store then reported as an in-flight
    # temporary and left out of `retained_paths` entirely.
    folded = _folded_name(name)
    if folded in _RESERVED_NAMES:
        raise ValueError(
            f"result: {name!r} is reserved. The answer is written by publish(), and a "
            f"retained artifact must not shadow it."
        )
    if folded.endswith(TEMP_SUFFIX):
        raise ValueError(
            f"result: {name!r} ends with the in-flight suffix and would be indistinguish"
            f"able from a half-written file."
        )
    payload = bytes(data)
    if not payload:
        raise ValueError(
            f"result: refusing to retain zero bytes as {name!r}. A 0-byte artifact holds "
            f"nothing, cannot be told apart from one that was never written, and cannot be "
            f"protected from a later write — so naming it to a caller would be a promise "
            f"this module could not keep."
        )

    directory = _require_run(handle, store_root)
    target = directory / name

    # `_existing_answer` is shared with publication and speaks of "the answer" when it
    # cannot read. Here the path is a retained artifact, and a refusal that misnames what
    # it kept sends an operator to the wrong file. The comparison still raises, nothing is
    # written, the file is kept — as for a claim that falls back for want of hard links.
    try:
        same = _existing_answer(target, payload)
        if same is None:
            # Nothing is there — claim the name atomically. A retained path is a promise
            # under exactly the rule the answer has, so it gets exactly the answer's
            # protocol: the loser of a race is told by the filesystem, not by a read that
            # has already gone stale.
            if _atomic_write_bytes(directory, name, payload, replace=False):
                return target
            same = _existing_answer(target, payload)
            if same is None:
                # It holds 0 bytes, which is not an artifact this module wrote — the
                # residual this function's docstring declares, and NOT a claim.
                _atomic_write_bytes(directory, name, payload, replace=True)
                return target
    except Unreadable as exc:
        raise Unreadable(
            f"result: retained artifact {target} could not be read, so what it holds is "
            f"unknown. Refusing to write over it. The file is kept."
        ) from exc
    if same:
        return target
    raise RetainedArtifactConflict(
        f"result: {target} already holds different bytes. A retained path is a "
        f"promise once it has been named to a caller, so it is not overwritten."
    )


def retained_paths(
    handle: str, *, store_root: str | os.PathLike[str] | None = None
) -> list[Path]:
    """Every durable file this run holds, so a caller can NAME them rather than guess.

    R15's second half, and the half most easily dropped: the retention arm of the original
    tool does not merely skip a delete, it states each path it kept, so the promise
    resolves without the caller going looking. In-flight temporaries are excluded — they
    are not promises and may vanish at any moment.

    THE EXCLUSION IS DELIBERATELY UNFOLDED, unlike the checks at `retain`'s door. It matches
    the literal suffix `_atomic_write_bytes` writes, and nothing else. Folding it would widen
    it: a file some other actor left at `X.PUBLISHING` would then be dropped from the
    inventory, which is the very failure — a durable file reported as in-flight — that
    folding the DOOR exists to prevent. The door refuses every name whose folded form ends
    with the suffix, so no artifact this module accepts can reach this listing needing a fold.
    """
    directory = _require_run(handle, store_root)
    return sorted(
        path
        for path in directory.iterdir()
        if path.is_file() and not path.name.endswith(TEMP_SUFFIX)
    )


def list_handles(*, store_root: str | os.PathLike[str] | None = None) -> list[str]:
    """Every run in the store, oldest name first.

    A reader's way back in when a handle was lost with the process that printed it. Only
    directories whose names are valid handles are returned; anything else in the store was
    not put there by this module and is not claimed by it.
    """
    root = _resolve_root(store_root)
    if not root.is_dir():
        return []
    return sorted(
        entry.name
        for entry in root.iterdir()
        if entry.is_dir() and _HANDLE_RE.match(entry.name)
    )
