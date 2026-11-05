"""HTTP, Playwright and raw CDP transports with public profile/session ownership.

Copilot is callable only for disabled refusal. Install trafilatura separately
for HTML extraction; optional pypdf reads PDF text layers and a missing pypdf
fails only that transport attempt with pdf_extractor_missing."""

from __future__ import annotations

import atexit
import hashlib
import http.client
import io
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import state
from extract import (_challenge_reason, _json_auth_refusal,
                     classify_failure, html_to_markdown, is_real_content)
from safety import (UnsafeURL, safe_opener, validate_url_safety)

# ---- Constants ---------------------------------------------------------------

CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)
DEFAULT_HEADERS = {
    "User-Agent": CHROME_UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
MAX_HTTP_BYTES = 10 * 1024 * 1024  # 10 MB
DEFAULT_COPILOT_MODEL = "gpt-5.5"
PLAYWRIGHT_SESSION_PREFIX = "webfetch-"

# Sentinel for "caller did not supply a session snapshot". `None` cannot serve:
# it is what the session listing returns for UNKNOWN, so one value could not
# mean both "fetch it yourself" and "here it is, and it is unknown".
_SESSIONS_UNSET = object()

# Printed by `playwright-cli list` when there are genuinely no sessions. It is
# the ONLY positive evidence of emptiness; absent it, zero parsed records means
# we did not understand the output, not that there was nothing to understand.
_NO_BROWSERS_MARKER = "(no browsers)"
# Session names embed the owning Python PID so concurrent processes don't
# collide on a single canonical name in playwright-cli's daemon (iter-2 HIGH 1).
# Reaper recognizes these by prefix + parses the embedded PID for liveness.
PLAYWRIGHT_HEADLESS_SESSION = f"webfetch-headless-{os.getpid()}"
PLAYWRIGHT_HEADED_SESSION = f"webfetch-headed-{os.getpid()}"

# The session families that can OWN one of our numbered profile dirs, and it
# is NOT every `webfetch-` name. A session that ATTACHES to a browser holds no
# dir of ours, and upstream renders an attached session with NO user-data-dir
# line -- so matching the bare prefix makes one of those read as an owner whose
# dir cannot be established, every numbered dir then reads unclaimable, and
# both browser tiers die. Cross-family review 2026-08-29.
#
# THE `webfetch-cdp-<pid>-<n>` FAMILY THAT MOTIVATED THIS IS NO LONGER CREATED.
# `tier_cdp` has driven a raw DevTools websocket since `f30aa6b` (2026-09-04)
# and opens no playwright-cli session at all -- measured as a straddling pair,
# one such name constructed at `f30aa6b^` and zero at `f30aa6b`. The narrow
# tuple stays, for a reason that does not depend on that family existing: only
# these two take a numbered dir, and a daemon outlives the code that filled it.
PROFILE_OWNING_SESSION_PREFIXES = ("webfetch-headless-", "webfetch-headed-")


# Tier 2 (headed) is IN THE CASCADE AND ENABLED BY DEFAULT, and this is the
# AUTHORITY for that -- the cascade in fetch.py reads this predicate rather
# than owning a copy. The variable it reads is an opt-OUT.
HEADED_TIER_DISABLE_ENV = "WEBFETCH_DISABLE_HEADED"


def headed_tier_allowed() -> bool:
    """Allow visible headed browsing by default, unless explicitly opted out.

    WEBFETCH_DISABLE_HEADED accepts 1, true or yes (case-insensitive). The
    cascade and warmed-session launch guard share this policy. Tests inject
    browser boundaries rather than opening real windows."""
    return os.environ.get(HEADED_TIER_DISABLE_ENV, "").lower() not in (
        "1", "true", "yes")


PLAYWRIGHT_OPEN_TIMEOUT = 30
PLAYWRIGHT_EVAL_TIMEOUT = 15
PLAYWRIGHT_CLOSE_TIMEOUT = 5
# A JS challenge clears itself SECONDS AFTER `load` fires, so a single DOM
# read is guaranteed to catch the interstitial and never the page. These
# bound the re-read: the total wall-clock we will spend on a document that
# is POSITIVELY a challenge, and the gap between reads. A document that is
# not a challenge on its first read pays neither.
_CHALLENGE_SETTLE_DEFAULT = 12.0


def _settle_budget_from_env() -> float:
    """`WEBFETCH_CHALLENGE_WAIT` as a float, defaulting on anything else.

    R5, at the most expensive place a violation can sit. This is evaluated at
    IMPORT, so a bare `float()` on a malformed value ("12s", "twelve", a stray
    space) raised ValueError out of `import tiers` and killed EVERY fetch on
    the machine: no JSON envelope, no failure class, and a traceback naming
    `float()` rather than the variable that caused it. A fetch is not where an
    operator should learn that a shell profile has a typo in it, and one
    unreadable setting must never be able to take the whole skill down.

    UNSET IS SILENT, SET-AND-UNUSABLE IS NOT. An absent variable is the
    ordinary case and says nothing. A value that is present and cannot be read
    is a mistake someone made and can fix, so it gets a line on stderr naming
    the variable, the value, the default now in force and the shape of a good
    value. An empty string is in the second class deliberately: it was typed.

    A parsed value is returned as-is, including a non-positive one. Zero is a
    coherent "do not wait" and negative is bounded by the same comparison, so
    neither is this function's business to overrule.
    """
    raw = os.environ.get("WEBFETCH_CHALLENGE_WAIT")
    if raw is None:
        return _CHALLENGE_SETTLE_DEFAULT
    try:
        return float(raw)
    except (TypeError, ValueError):
        print(f"webfetch: WEBFETCH_CHALLENGE_WAIT={raw!r} is not a "
              f"number of seconds; using the default "
              f"{_CHALLENGE_SETTLE_DEFAULT}. Unset the variable, or set it to "
              f"a plain number such as 12.", file=sys.stderr)
        return _CHALLENGE_SETTLE_DEFAULT


CHALLENGE_SETTLE_BUDGET = _settle_budget_from_env()
CHALLENGE_POLL_S = 1.0
# Resolved at import to handle Windows .cmd shim (subprocess.run on Windows
# doesn't auto-resolve `playwright-cli` to `playwright-cli.cmd` without shell=True).
_PLAYWRIGHT_BIN_FOUND = shutil.which("playwright-cli")
# EVERY SUCCESSFUL LOOKUP IS MADE ABSOLUTE; ONLY THE FALLBACK STAYS BARE, AND
# THE `None` IS WHAT TELLS THEM APART. `_run_pw` pins the working directory, so
# a result that is relative in ANY sense would move under it.
# A SEPARATOR TEST IS NOT ENOUGH, and that was the first draft's defect: a PATH
# entry may be EMPTY, which means "the current directory", and `shutil.which`
# then returns `os.path.join("", name)` -- a separator-free BARE NAME that is
# nonetheless a cwd-relative result. Keeping `which`'s `None` separate is what
# distinguishes that from the fallback, because `or "playwright-cli"` erases
# the difference. Cross-family review round 3 found this.
# `abspath` on an already-absolute result is a normalisation only; on the
# empty-component result it resolves against the import-time cwd, which is
# exactly where `which` found the file.
# THE FALLBACK MUST STAY BARE: `abspath` would turn it into
# `<cwd>/playwright-cli`, a file that does not exist, destroying the PATH
# lookup it exists for.
# DISCLOSED, because it is a real behaviour change rather than a no-op: in the
# bare-name case Windows' own CreateProcess search includes the working
# directory, which is now the state dir rather than the caller's, so a
# `playwright-cli` sitting beside the caller is no longer picked up. That path
# was never one we wanted to execute from.
PLAYWRIGHT_BIN = (
    os.path.abspath(_PLAYWRIGHT_BIN_FOUND)
    if _PLAYWRIGHT_BIN_FOUND is not None
    else "playwright-cli"
)

# Persistent profile scheme for Tier 1/2 (D19). Numbered: first session gets
# `<base>`, next concurrent session gets `<base>2`, then `<base>3`, ... up to
# PROFILE_MAX_INSTANCES. Solves the "Chromium profile in use" lock by giving
# every concurrent Chromium its own dir.
# The public default is ~/.cache/webfetch/playwright, isolated per home.
# An explicit public environment override can select another owned base.
def _default_profile_base() -> str:
    """Return the public persistent profile base beneath the user home."""
    return str(Path.home() / ".cache" / "webfetch" / "playwright")

PROFILE_BASE = os.environ.get("WEBFETCH_PLAYWRIGHT_PROFILE", _default_profile_base())
# A RELATIVE OVERRIDE IS RESOLVED HERE, AGAINST THE cwd AT IMPORT, BECAUSE
# `_run_pw` PINS THE WORKING DIRECTORY IT LATER SPAWNS IN. The default above is
# absolute, so this is a no-op for every machine that does not set the variable;
# the guard exists for the one that does. Left relative, `--profile=` would mean
# one directory before this change and a different one after it -- silently, and
# in the one place a wrong answer costs a browser profile rather than a message.
# Resolving against the IMPORT cwd is what keeps the value meaning what it means
# today, rather than what the pinned directory would make it mean.
if not os.path.isabs(PROFILE_BASE):
    PROFILE_BASE = os.path.abspath(PROFILE_BASE)
PROFILE_MAX_INSTANCES = int(os.environ.get(
    "WEBFETCH_PLAYWRIGHT_PROFILE_MAX", "10"
))

# Wall-clock ceiling for one claim sweep. Each candidate whose lock we win
# costs a fresh `playwright-cli list`, and that call has its own 10s timeout,
# so an unresponsive CLI could otherwise consume PROFILE_MAX_INSTANCES * 10s
# before the fetch deadline even starts counting.
PROFILE_SWEEP_BUDGET = float(os.environ.get(
    "WEBFETCH_PROFILE_SWEEP_BUDGET", "20"
))

# ---- TierResult --------------------------------------------------------------

@dataclass
class TierResult:
    ok: bool
    tier: str
    content: str = ""           # extracted markdown or supported textual body
    html: str = ""              # raw HTML when available
    status: int | None = None
    # Did `status` come off a response, or did this tier invent it?
    # The browser tiers substitute 200 when their capture route was unavailable;
    # tier_http reports what the origin sent. The disabled Copilot tier has no
    # status. Default True preserves observed statuses; any tier manufacturing
    # one must explicitly set this False rather than over-claim provenance.
    #
    # Meaningless when `status is None`: the envelope omits both keys
    # together, so there is no state where a marker sits beside no status.
    status_is_real: bool = True
    bytes_: int = 0
    elapsed_ms: int = 0
    error: str = ""
    failure_class: str = ""
    redirect_chain: list[str] = field(default_factory=list)
    # The response's OWN header casing, because the escalation gate's vendor
    # arms read names the origin chose and header names are case-insensitive on
    # the wire. Consumers lowercase; this does not.
    headers: dict[str, str] = field(default_factory=dict)
    # The redirect chain as (status, from_url, to_url), which `redirect_chain`
    # cannot carry: it is a list of URLs and fetch.py's cascade re-validates
    # every element of it against the SSRF guard, so its element type is not
    # ours to repurpose. The third element is the RESOLVED ABSOLUTE target and
    # not the raw `Location` line -- both transports produce it that way
    # (urllib resolves inside http_error_302 before the handler is called, the
    # impersonate arm urljoins), and a relative `/login` cannot answer the one
    # question the gate asks a chain, which is what HOST it ended at.
    hops: list[tuple[int, str, str]] = field(default_factory=list)
    # The RAW, multi-valued Set-Cookie lines. NOT derivable from `headers`: a
    # flat dict over urllib's email.message.Message keeps only the LAST
    # duplicate and curl_cffi's Headers.items() joins them with ", ", so the
    # vendor cookie arms (`datadome=`, `_px*`, `aws-waf-token`) get their own
    # field or they get nothing. Best-effort -- see _raw_set_cookie.
    set_cookie: list[str] = field(default_factory=list)
    pages_read: list[str] | None = None  # retained result field; raw tiers leave unset
    extractor: str = "trafilatura"       # "trafilatura" | "pypdf" | "verbatim"
    final_url: str = ""                  # H4 fix: post-redirect URL; empty if same as input
    # A document-level failure skips browser DOM extraction, never the gate.
    # Historical PDF results: browsers 0/140, Copilot 14/20. Copilot is now
    # disabled; those measurements do not authorize synthesis rescue.
    skip_browser_tiers: bool = False
    # Tier 0 makes up to TWO transport attempts (urllib, then curl_cffi
    # impersonation) and they are ONE tier -- tier_idx 0-4 is baked into the
    # blocklist schema, so neither may become a tier of its own. This is how
    # the envelope still says which one ran and what each one saw. Set ONLY
    # when a second attempt actually happened, so a one-attempt fetch keeps
    # exactly the envelope it has today.
    sub_attempts: list[dict] | None = None
    error_code: str = ""
    # Current-document UI observations; access policy lives in the shared inspector.
    access_gate_candidates: list[dict] = field(default_factory=list)
    blocked_access: dict | None = None


# ---- Tier 0: HTTP ------------------------------------------------------------

# Content types whose body IS the content. Running an HTML extractor over them
# returns nothing: 449 of the 580 "extracted too short" rows extracted ZERO
# chars and are these -- api.github.com (72), raw.githubusercontent.com (44),
# cdn.jsdelivr.net (33), api.npmjs.org (13).
_HTML_CONTENT_TYPES = ("text/html", "application/xhtml+xml")
_VERBATIM_CONTENT_TYPES = ("application/json", "application/javascript",
                           "application/xml")


def _is_verbatim_text_type(ct_main: str) -> bool:
    """True for a textual content type that is NOT HTML, so the body is the answer."""
    if ct_main in _HTML_CONTENT_TYPES:
        return False
    return (ct_main.startswith("text/")
            or ct_main in _VERBATIM_CONTENT_TYPES
            or (ct_main.startswith("application/") and ct_main.endswith("+json")))


# Byte-order marks, longest first: UTF-32LE begins with the UTF-16LE mark, so
# testing the short one first would strip two bytes and leave two nulls.
_BOMS = (b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff", b"\xef\xbb\xbf",
         b"\xff\xfe", b"\xfe\xff")
_LEADING_WS = b" \t\r\n\f\v"


def _pdf_signature_at_start(raw: bytes) -> bool:
    """True when %PDF- is the FIRST thing in the document.

    A byte-offset SCAN of the first kilobyte is what let an HTML page
    documenting the format -- `<pre>%PDF-1.7 ...</pre>` -- enter the PDF
    branch at all, and every downstream repair for that was building on the
    wrong entry rule. A real PDF begins with its signature, so require that
    and the false positive cannot arise: an HTML page that merely MENTIONS a
    PDF header never enters, whether or not it carries <html>, and whether or
    not pypdf is installed to fail on it.

    Only a BOM and leading ASCII whitespace are skipped. pypdf agrees with
    this bound rather than with the old window: measured 2026-08-30, its
    reader seeks to 0 and reads 5 bytes, and in strict mode raises
    `PdfReadError: PDF starts with ..., but '%PDF-' expected`. Non-strict
    mode only warns and then relies on xref recovery.

    KNOWN RESIDUAL, and it is a real capability reduction. The PDF Reference
    implementation notes record that Acrobat accepts a header anywhere in the
    first 1024 bytes, which is why files with a proxy banner or CGI preamble
    ahead of the signature exist at all. Those no longer enter this branch;
    they fall to the HTML path and, failing there, escalate to copilot. This
    project has never captured one -- the fixture that used to assert the
    capability was synthetic -- so the trade is a documented-but-unobserved
    input against a false positive we reproduced. If a real one turns up,
    widen the SKIP here; do not go back to scanning a window.
    """
    i = 0
    for bom in _BOMS:
        if raw.startswith(bom):
            i = len(bom)
            break
    while i < len(raw) and raw[i] in _LEADING_WS:
        i += 1
    return raw[i:i + 5] == b"%PDF-"


def _extract_pdf_text(raw_bytes: bytes, *,
                      deadline: float | None = None) -> tuple[str, str]:
    """Return (text, error). Exactly one is non-empty.

    The caller has already confirmed the bytes are a PDF.

    `deadline` is the caller's absolute time.monotonic() budget, enforced at
    THREE points, because any one of them alone leaves a hole:

      1. BEFORE each page -- cheap, and it is what stops a long multi-page
         document once the budget is gone.
      2. AFTER the loop -- because 1 alone never re-checks once the LAST page
         starts, so a one-page PDF got no protection at all and the final page
         of any PDF was equally unguarded: the check passed, extraction ran
         long, and this returned SUCCESS on a result that arrived after the
         budget expired. Reporting success late is worse than reporting a
         timeout, so 2 is mandatory.
      3. A JOIN on the worker -- 1 and 2 both wait for pypdf to finish, so
         neither returns control on time. MEASURED 2026-08-30: a single page
         of dense text operations extracts in 0.55 s at 0.5 MB, 6.7 s at 3 MB
         and 106 s at the 10 MB MAX_HTTP_BYTES ceiling -- roughly O(n^2.3), so
         a single page crosses a 30 s budget at about 5.8 MB, well inside what
         this tier will download. A single-page overrun is reachable in
         practice, not just in principle.

    WHAT THE JOIN COSTS, STATED PLAINLY. Python cannot cancel a thread. The
    worker is a DAEMON, so it cannot hold the process open, but after a
    timeout it keeps running -- burning CPU and holding the document in
    memory -- until pypdf finishes or the process exits, whichever comes
    first. That is bounded here because fetch.py is a one-shot CLI, one
    process per URL. IF THIS EVER BECOMES A LONG-LIVED SERVICE, orphaned
    workers accumulate and this needs a subprocess instead, which can
    actually be killed. The trade is deliberate: without it the caller waits
    106 s and then gets a timeout anyway; with it the caller gets the timeout
    on time and the cascade reaches copilot, which holds the only nonzero PDF
    success rate on record.

    DEPENDENCY: `pypdf` (pure-python, BSD-3-Clause, no required runtime deps on
    3.12). It is what turns PDF bytes into text; without it this tier cannot
    read a PDF at all.

    WITHOUT IT, THE SKILL STILL WORKS. This is the one place the dependency is
    touched, and the import is deliberately INSIDE the function rather than at
    module scope, so an absent pypdf costs one tier rather than the process:
    the fetch fails with `pdf_extractor_missing` and the cascade continues to
    the tiers above. That is required, not incidental -- copilot holds the only
    nonzero PDF success rate on record (14 of 20), and a missing dependency says
    nothing about the document. Contrast extract.py, where a missing trafilatura
    IS fatal (sys.exit(2)); do not copy that handler here.

    pypdf is a tier-2 entry in deploy/dependencies/manifest.json, and a tier-2
    miss is a WARNING rather than a failure in check_dependencies.py -- so a
    machine can finish provisioning without it, and this path is the live path
    on any box the bootstrap has not reached. Guarded by
    tests/test_pdf_and_text_types.py::test_missing_pypdf_*.
    """
    try:
        import pypdf
    except ImportError:
        return "", ("pdf_extractor_missing: pypdf is not installed, so this PDF "
                    "cannot be read here; the cascade continues to the next tier. "
                    "Install with: pip install --user pypdf")
    out: dict = {}

    def _read() -> None:
        try:
            reader = pypdf.PdfReader(io.BytesIO(raw_bytes))
            # Encryption is a DIFFERENT FACT from "malformed", and the caller
            # can act on it. Folding it into pdf_unreadable is what let F2
            # read an arbitrary pypdf exception as proof the body was HTML.
            if getattr(reader, "is_encrypted", False):
                out["err"] = ("pdf_encrypted: the document is encrypted and no "
                              "password is available here")
                return
            pages = len(reader.pages)
            parts: list[str] = []
            for i, page in enumerate(reader.pages):
                if deadline is not None and time.monotonic() > deadline:
                    # Transient, like every other PDF error: running out of
                    # time says nothing about the domain, so it must not
                    # promote.
                    out["err"] = (f"pdf_extract_timeout: total_timeout exceeded "
                                  f"after {i} of {pages} page(s)")
                    return
                parts.append(page.extract_text() or "")
            out["pages"] = pages
            out["text"] = "\n\n".join(parts)
        except Exception as e:      # truncated, malformed, unsupported
            out["err"] = f"pdf_unreadable: {type(e).__name__}: {e}"

    if deadline is None:
        _read()
    else:
        worker = threading.Thread(target=_read, daemon=True,
                                  name="webfetch-pdf-extract")
        worker.start()
        worker.join(max(0.0, deadline - time.monotonic()))
        if worker.is_alive():
            # Point 3. The worker keeps running; see the docstring for what
            # that costs and why it is still the right trade.
            return "", ("pdf_extract_timeout: total_timeout exceeded inside a "
                        "single page; extraction abandoned (a pypdf call "
                        "cannot be cancelled, so the worker runs on until the "
                        "process exits)")

    if "err" in out:
        return "", out["err"]
    text = out.get("text", "")
    # Point 2. Extraction finished -- but did it finish IN TIME? Without this
    # the function returns success on a result that arrived after the budget,
    # which is the half of F1 that made it a truthfulness finding.
    if deadline is not None and time.monotonic() > deadline:
        return "", (f"pdf_extract_timeout: total_timeout exceeded; extraction "
                    f"of {out.get('pages', '?')} page(s) completed after the "
                    f"budget expired")
    if not text.strip():
        return "", (f"pdf_no_text_layer: {out['pages']} page(s), 0 extractable "
                    f"chars (likely scanned; no OCR)")
    return text, ""


# ---- Tier 0's two transports -------------------------------------------------


# The newest Chrome profile curl_cffi ships -- verified against 0.16.2, whose
# chrome* targets end at chrome150. It impersonates macOS Chrome, so anything
# downstream reading the request UA sees macOS.
IMPERSONATE_TARGET = "chrome150"

# Redirects on the impersonate path are driven by us, not by libcurl, so these
# bounds are ours to state. They are urllib's own, taken so the two transports
# agree on what "too many" and "a loop" mean:
# HTTPRedirectHandler.max_redirections = 10, max_repeats = 4.
_IMPERSONATE_MAX_HOPS = 10
_IMPERSONATE_MAX_REPEATS = 4

# Statuses urllib's redirect handler follows. 308 included: urllib gained it in
# 3.11 and this file is 3.12-only.
_REDIRECT_STATUSES = (301, 302, 303, 307, 308)

# Shared by the transport that emits it and the orchestrator that reads it back.
# "We never reached the network" is a decision the orchestrator has to make from
# a TierResult, and this prefix is the only mark that carries it.
_UNSAFE_URL_PREFIX = "unsafe URL: "


def _raw_set_cookie(headers) -> list[str]:
    """The RAW, multi-valued Set-Cookie lines, or [] when this object cannot
    give them.

    THE FLAT `{k: v for k, v in headers.items()}` IDIOM BOTH TRANSPORTS USE
    CANNOT: urllib's email.message.Message collapses duplicate names to
    last-wins and curl_cffi's Headers.items() joins them with ", ". So the
    accessor is chosen per header object -- `.get_all()` on urllib's Message
    (response and HTTPError alike), `.get_list()` on curl_cffi's Headers
    (measured on the installed 0.16.2; it is the only accessor there that
    returns the unjoined lines).

    RETURNS [] RATHER THAN A FLATTENED LINE for anything offering neither.
    Flattening is exactly the loss this exists to avoid, and an absent cookie
    can only make the gate's VENDOR_PRESENT precondition false -- i.e. no
    window. Degrading toward "" costs precision; degrading toward a joined
    string would cost a correct answer.

    THE GUARD IS AS WIDE AS THE CONTRACT ABOVE IT. Both callers sit outside
    every `try` in functions documenting "Raises nothing", which fetch.py's
    cascade relies on -- it does not wrap tier_http, so one exception escaping
    here costs the four tiers that had not run yet.
    """
    for accessor in ("get_all", "get_list"):     # urllib Message / curl_cffi Headers
        fn = getattr(headers, accessor, None)
        if callable(fn):
            try:
                return [v for v in (fn("Set-Cookie") or []) if isinstance(v, str)]
            except Exception:
                return []
    return []


def _process_http_response(*, raw: bytes, status: int, headers: dict[str, str],
                           final_url: str, requested_url: str,
                           redirect_chain: list[str], t0: float,
                           deadline: float,
                           set_cookie: list[str] | None = None,
                           hops: list[tuple[int, str, str]] | None = None,
                           ) -> TierResult:
    """Judge a fetched tier-0 body. BOTH transports end here.

    SHARED BY CONSTRUCTION, not by convention. The second attempt runs only
    because the first did not return real content, so if the two judged bodies
    differently, "the first attempt failed" would stop meaning what the
    orchestrator reads it as -- and a body urllib rejected could come back
    accepted purely because it arrived over a different socket.

    `headers` is the response's own casing, because is_real_content reads it.
    The lowercased copy is for our own lookups: header names are
    case-INSENSITIVE on the wire and servers disagree -- arxiv.org sends
    `content-type` lowercase in a response whose other headers are title-case,
    so case-sensitive lookups returned '' for it, silently disabling charset
    detection on the highest-value PDF source in this box's own log.
    """
    headers_l = {k.lower(): v for k, v in headers.items()}
    # The escalation gate's inputs, carried onto EVERY result this function
    # builds. As one dict because four returns times three fields is four
    # chances to populate three of them.
    evidence = {"headers": dict(headers),
                "set_cookie": list(set_cookie or []),
                "hops": list(hops or [])}
    total = len(raw)
    # urllib hands back only the FINAL url -- its opener follows the hops
    # inside open() -- so the chain is derived from that. The impersonate path
    # drives its own hop loop and passes the real one, which is why this is not
    # simply computed here for everyone.
    chain = list(redirect_chain) or (
        [final_url] if final_url != requested_url else [])

    # PDF. The BYTES decide and the header gets no vote -- an application/pdf
    # body that is really an HTML login interstitial must take the HTML path,
    # and a PDF served as text/html must still be read as a PDF. Until this
    # branch existed, 2 of 3 probed PDFs PASSED the content gate and returned
    # object dictionaries ("%PDF-1.7 %... 1 0 obj << /Lang (en) ...") to the
    # caller as document content, labelled extractor: trafilatura, and cached.
    #
    # WHICH bytes decide is the part round 2 got wrong. It scanned the first
    # KiB, so a documentation page containing `<pre>%PDF-1.7</pre>` entered
    # here and then had to be rescued on the way out. The rescue was keyed on
    # pdf_unreadable, which does NOT mean "not a PDF" -- it means pypdf raised,
    # and an encrypted or unsupported REAL PDF raises too. So the rescue could
    # send a real PDF into the HTML path and hand back its embedded markup.
    # Deciding by signature POSITION removes the false positive at the door,
    # and with it the need for any rescue: there is no fall-through below.
    if _pdf_signature_at_start(raw):
        if time.monotonic() > deadline:
            return TierResult(
                ok=False, tier="http", status=status, error="total_timeout exceeded",
                failure_class="timeout", bytes_=total, **evidence,
                elapsed_ms=int((time.monotonic() - t0) * 1000),
            )
        pdf_text, pdf_error = _extract_pdf_text(raw, deadline=deadline)
        # ONE TIEBREAKER, ON pdf_unreadable ONLY. A text/* or JSON document
        # whose first bytes are literally `%PDF-` satisfies the signature rule,
        # and with this branch terminal it had no way back to the text paths --
        # valid text became a PDF failure. The DECLARED TYPE breaks the tie:
        # the bytes said PDF, pypdf disagreed, and the origin says text. Two
        # against one, and only where pypdf has already refused the document.
        #
        # THIS DOES NOT REOPEN F2. F2's case is an encrypted or unsupported
        # REAL PDF, which arrives as application/pdf and now carries its own
        # pdf_encrypted error -- so it matches neither half of this condition
        # and keeps its PDF-specific failure. The round-2 rescue was unsafe
        # because it inferred "this is HTML" from the EXCEPTION; this infers
        # nothing, it defers to what the origin declared.
        # ONLY to a GATED path. The tiebreaker may hand the body onward only
        # where something downstream still judges it -- which means HTML, and
        # not the verbatim path.
        #
        # WHY: a declared header must not be able to turn pdf_unreadable into
        # SUCCESS. The verbatim path applies no content gate at all -- it
        # returns the decoded body with ok=True -- so a corrupted real PDF
        # served as text/plain came back as document content, which is binary
        # garbage presented as prose. The HTML path is gated: measured
        # 2026-08-30, corrupted PDF bytes extract to 0 chars and is_real_content
        # rejects them, so a lie about the type costs a wasted extraction
        # rather than a false success.
        #
        # NOTE THE SHAPE OF THE TEST. It cannot be "does the body still begin
        # with %PDF-": this branch is ENTERED only when the signature is at
        # offset 0, so that is true of every document reaching here, genuine
        # text included. The separating question is not what the bytes start
        # with, it is whether anything downstream will still say no.
        ct_declared = headers_l.get("content-type", "").split(";")[0].strip().lower()
        gated_text_type_declared = ct_declared in _HTML_CONTENT_TYPES
        defer_to_declared_text_type = (
            pdf_error.startswith("pdf_unreadable:") and gated_text_type_declared)
        if not defer_to_declared_text_type:
            return TierResult(
                ok=not pdf_error, tier="http", content=pdf_text, html="",
                status=status, bytes_=total, error=pdf_error, **evidence,
                # A PDF we could not read is not evidence that this domain needs
                # a higher tier: the browser rungs have made 140 attempts on PDF
                # URLs over four months with zero successes. `other` is
                # transient, so it cannot promote.
                failure_class="" if not pdf_error else "other",
                redirect_chain=chain, final_url=final_url,
                # Truthful on every other path -- pypdf DID run and DID fail,
                # so a no-text-layer, unreadable or timed-out envelope naming
                # it is correct. It is only pdf_extractor_missing that names an
                # extractor which is not installed. Cosmetic today (fetch.py
                # reads this in the success branch only) and incoherent to
                # anyone reading a failure envelope.
                extractor=("" if pdf_error.startswith("pdf_extractor_missing:")
                           else "pypdf"),
                # ANY failure in this branch skips the browser rungs. Not an
                # exception list -- one rule, because the reason is structural
                # rather than statistical.
                #
                # THE BROWSER TIERS HAVE EXACTLY ONE ROUTE TO CONTENT:
                # `document.documentElement.outerHTML` (tier_playwright and
                # tier_cdp both, and nothing else in this file reads a page).
                # outerHTML serialises the LIGHT DOM ONLY. Chrome renders a PDF
                # with the PDFium plugin inside an <embed>, so the document's
                # text is never DOM text, and the viewer's own UI lives in
                # shadow roots that outerHTML does not serialise either.
                # Verified 2026-08-30 by feeding the viewer-shaped DOM through
                # the real pipeline: 0 extracted chars, rejected by the gate.
                #
                # So it does not matter WHY our own extraction failed -- a bad
                # document, a missing dependency, or the clock. A browser tier
                # cannot return PDF text through this code path, and trying
                # puts a window on the user's screen for nothing (C-3/C-4).
                # The 0-for-140 measurement is the observable consequence of
                # that, not the reason for it.
                #
                # Missing pypdf remains a tier failure rather than a process
                # exit. Historical Copilot PDF results were 14/20, but the
                # disabled source policy now forbids that synthesis fallback.
                skip_browser_tiers=bool(pdf_error),
                elapsed_ms=int((time.monotonic() - t0) * 1000),
            )

    ct = headers_l.get("content-type", "")
    ct_main = ct.split(";")[0].strip().lower()
    charset = "utf-8"
    m = re.search(r"charset=([\w\-]+)", ct, re.IGNORECASE)
    if m:
        charset = m.group(1)
    try:
        decoded = raw.decode(charset, errors="replace")
    except LookupError:
        decoded = raw.decode("utf-8", errors="replace")

    # JSON, JavaScript, plain text and friends: the body is already the content,
    # and html_to_markdown returns "" for all of them. No content gate applies --
    # the status check has already happened, and a 20-char JSON document is a
    # complete answer.
    #
    # ONE EXCEPTION, and it is the reason this branch is not a bare return: an
    # application-level AUTHENTICATION REFUSAL served at 2xx. Its canonical form
    # is slack.com/api/conversations.list answering an unauthenticated request
    # with HTTP 200 and 33 bytes of `{"ok":false,"error":"not_authed"}`. Nothing
    # downstream can tell that apart from content -- the envelope says ok, the
    # tier says http, and the body is a well-formed document -- so an agent
    # summarises the refusal as though it were the page. A false success is
    # worse than a failure precisely because it is unfalsifiable from outside.
    #
    # `escalation_verdict` cannot be reused for this and that is measured, not
    # assumed: it computes only on a FAILED tier result, and its Rule B returns
    # CONTINUE for anything below the headless tier, so it never reaches its own
    # JSON arm at tier 0. The recognition has to be its own check, here.
    #
    # REFUSING COSTS ONE FETCH AND OPENS NOTHING *AT THIS RUNG*, and that scope
    # is exact rather than cautious. Measured 2026-09-04: the verdict computed on
    # this refusal is CONTINUE, and `_gate_allows` refuses both `headed` and
    # `cdp` on CONTINUE, so the cascade can only reach `headless` (invisible) and
    # `copilot` (no browser). Across fetches, `other` is ABSENT FROM
    # `PERSISTENT_FAILURE_CLASSES` -- which is the condition `record_failure`
    # actually enforces, NOT membership in the transient set beside it, since the
    # two are independent frozensets rather than complements -- so a streak of
    # these cannot promote the domain either.
    #
    # WHAT THIS DOES NOT PROMISE, stated because the earlier wording overclaimed
    # it: the FETCH may still reach a windowed rung later. If `headless` then
    # diagnoses NEEDS_LOGIN on its own evidence, `cdp` becomes enterable -- and
    # that is the escalation gate working as designed, not a leak. why-not a
    # fetch-wide "no visible tiers" latch carried from here: it would suppress a
    # genuine login diagnosis made one rung up on better evidence, which is the
    # whole purpose of the gate.
    if ct_main and _is_verbatim_text_type(ct_main):
        empty = not decoded.strip()
        refusal = not empty and _json_auth_refusal(decoded)
        if refusal:
            error = "authentication refusal served at HTTP %d" % status
        elif empty:
            error = f"empty {ct_main} body (0 chars)"
        else:
            error = ""
        return TierResult(
            ok=not (empty or refusal), tier="http",
            content="" if (empty or refusal) else decoded, html="",
            status=status, bytes_=total,
            error=error,
            failure_class="" if not (empty or refusal) else "other",
            redirect_chain=chain, final_url=final_url,
            extractor="verbatim", **evidence,
            elapsed_ms=int((time.monotonic() - t0) * 1000),
        )

    html = decoded
    md = html_to_markdown(html, final_url)
    ok, reason = is_real_content(md, html, status, headers)
    # Whole body, never a slice -- see classify_failure's contract.
    fc = "" if ok else classify_failure(status, html, None, extracted=md)
    return TierResult(
        ok=ok, tier="http", content=md if ok else "", html=html,
        status=status, bytes_=total, error="" if ok else reason,
        failure_class=fc, redirect_chain=chain,
        final_url=final_url, **evidence,
        elapsed_ms=int((time.monotonic() - t0) * 1000),
    )


def _tier_http_urllib(url: str, *, connect_timeout: float = 5.0, read_timeout: float = 15.0,
                      total_timeout: float = 30.0, allow_private: bool = False) -> TierResult:
    """HTTP fetch with Chrome UA via safe_opener (no FileHandler). Raises nothing.

    Tier 0's FIRST attempt, and unchanged by the second one existing: this is
    the arm that wins old.reddit.com, which 403s the impersonating client.
    """
    t0 = time.monotonic()
    deadline = t0 + total_timeout
    try:
        validate_url_safety(url, allow_private=allow_private)
    except UnsafeURL as e:
        return TierResult(ok=False, tier="http", error=f"{_UNSAFE_URL_PREFIX}{e}",
                          failure_class="other", elapsed_ms=int((time.monotonic() - t0) * 1000))
    opener = safe_opener()
    req = urllib.request.Request(url, headers=DEFAULT_HEADERS, method="GET")
    req._webfetch_allow_private = allow_private  # consumed by SafeRedirectHandler
    # THE OPENER FOLLOWS REDIRECTS INSIDE open(), so this function sees only the
    # final URL and never the chain. _SafeRedirectHandler is the one thing in
    # this process that sees each hop -- it already re-validates every one --
    # and it appends to this list as it goes. Same idiom as _webfetch_allow_private
    # above, and the same reason: a fresh opener per call means a fresh list per
    # call, so no request can see another's hops.
    hops: list[tuple[int, str, str]] = []
    req._webfetch_hops = hops
    try:
        # urllib's `timeout` parameter is per-socket-op; use min(connect + read).
        timeout = min(connect_timeout + read_timeout, max(0.1, deadline - time.monotonic()))
        resp = opener.open(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        # THE CLASSIFIER'S INPUT, and read for that purpose now. It decides
        # the NAME of the diagnosis on a 401/402/403/451 -- `origin_4xx_block`
        # or `cloudflare_challenge` -- and not the promotion, because both of
        # those are persistent. RESIDUAL, stated rather than fixed here:
        # classify_failure's contract asks for the WHOLE document and this is
        # a 2 KiB slice, so a challenge marker further down an error body is
        # read as a plain origin block. Widening it means an unbounded read on
        # a path with no deadline check, which is a different change.
        body_preview = ""
        try:
            body_preview = e.read(2048).decode("utf-8", "replace")
        except Exception:
            pass
        # THE HONEST CLASS, RECORDED. R1 is that the fetch must succeed, so a
        # domain the origin walls at tier 0 has to REACH the tier that can
        # serve it -- and blocklist promotion is the only thing that moves it.
        # This branch used to record the literal "other" instead, on the
        # ground that a browser window on the owner's screen outranked a
        # domain reaching its right tier. THE OWNER REJECTED THAT ORDERING on
        # 2026-09-04: the fetch succeeding is priority one, and where a window
        # is placed is priority two and never decides whether a tier may run.
        #
        # WHAT IT CHANGES, measured 2026-09-04 in this tree by replaying a
        # domain 4xx-walled at EVERY tier through three full cascades against
        # an in-memory blocklist. Cells are the tier the domain SITS AT after
        # fetch N, i.e. where the next fetch would start:
        #
        #   tier 0 records         | fetch 1  | fetch 2 | fetch 3
        #   `other` (the old code) | http     | http    | http
        #   `origin_4xx_block`     | headless | copilot | cdp
        #
        # record_failure counts CONSECUTIVE same-class entries from the tail of
        # recent_failures, and any different class breaks the streak. With
        # `other` at the head of every cascade the streak never reached
        # PROMOTE_THRESHOLD = 3, so the old code did not merely withhold
        # tier-0 evidence -- it STARVED THE WHOLE STREAK and the domain sat at
        # http indefinitely. (An older comment here predicted `headless |
        # headed | headed`; the row above is what this tree actually does, and
        # the difference is that the honest class now supplies three
        # consecutive entries inside a single cascade.)
        #
        # THE COST, STATED. `origin_4xx_block` is also what a 404 or a 410
        # yields, so a permanently dead URL walks the whole cascade over three
        # fetches instead of failing cheaply at http. That is the price of R1
        # and it is accepted deliberately, not overlooked. Guarded by
        # test_tier0_4xx_promotes_toward_a_tier_that_can_serve_it.
        #
        # THE HEADERS ARE READ HERE TOO, and that is a separate question from
        # the class. An error response is where the login and challenge
        # evidence actually lives -- `WWW-Authenticate` exists on no other
        # status, and the vendor cookies arrive on the 403 rather than on a
        # 200 we never got -- so a branch that read `e.code` and `e.reason`
        # and nothing else made the whole header half of the escalation gate
        # structurally unreachable.
        err_headers = {k: v for k, v in (e.headers or {}).items()}
        return TierResult(
            ok=False, tier="http", status=e.code, error=f"HTTP {e.code} {e.reason}",
            failure_class=classify_failure(e.code, body_preview),
            headers=err_headers, set_cookie=_raw_set_cookie(e.headers),
            hops=list(hops),
            elapsed_ms=int((time.monotonic() - t0) * 1000),
        )
    # http.client.HTTPException is in this tuple because it is NOT an OSError and
    # urllib does not wrap it. MEASURED: BadStatusLine, LineTooLong and
    # IncompleteRead all derive from HTTPException alone, and
    # AbstractHTTPHandler.do_open only converts OSError to URLError -- so a
    # garbage status line escaped this function and killed the whole cascade,
    # against a docstring that says "Raises nothing."
    except (UnsafeURL, ValueError, socket.timeout, urllib.error.URLError, OSError,
            http.client.HTTPException) as e:
        return TierResult(
            ok=False, tier="http", error=str(e),
            failure_class=classify_failure(None, "", type(e).__name__),
            elapsed_ms=int((time.monotonic() - t0) * 1000),
        )
    if resp is None:
        # Genuine catch-all with no cause to assert. An unsafe redirect target does
        # NOT arrive here -- validate_url_safety raises out of redirect_request
        # (safety.py) and is caught above.
        return TierResult(
            ok=False, tier="http",
            error="opener returned no response (no handler claimed the status)",
            failure_class="other",
            elapsed_ms=int((time.monotonic() - t0) * 1000),
        )
    final_url = resp.geturl()
    status = resp.status
    response_headers = {k: v for k, v in resp.headers.items()}
    # Before the read loop, because `resp.headers` is the only object that can
    # answer and the flat dict above has already lost the duplicates.
    response_set_cookie = _raw_set_cookie(resp.headers)
    headers_l = {k.lower(): v for k, v in response_headers.items()}
    content_length = headers_l.get("content-length")
    if content_length:
        try:
            if int(content_length) > MAX_HTTP_BYTES:
                resp.close()
                return TierResult(
                    ok=False, tier="http", status=status,
                    error=f"Content-Length {content_length} exceeds max {MAX_HTTP_BYTES}",
                    failure_class="other",
                    elapsed_ms=int((time.monotonic() - t0) * 1000),
                )
        except ValueError:
            pass
    chunks: list[bytes] = []
    total = 0
    try:
        while True:
            if time.monotonic() > deadline:
                resp.close()
                return TierResult(
                    ok=False, tier="http", status=status, error="total_timeout exceeded",
                    failure_class="timeout",
                    elapsed_ms=int((time.monotonic() - t0) * 1000),
                )
            chunk = resp.read(64 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_HTTP_BYTES:
                resp.close()
                return TierResult(
                    ok=False, tier="http", status=status,
                    error=f"response exceeded {MAX_HTTP_BYTES} bytes",
                    failure_class="other",
                    elapsed_ms=int((time.monotonic() - t0) * 1000),
                )
            chunks.append(chunk)
    # HTTPException for the same reason as the open() clause above; IncompleteRead
    # is the one that actually reaches this loop.
    except (socket.timeout, OSError, http.client.HTTPException) as e:
        return TierResult(
            ok=False, tier="http", status=status, error=str(e),
            # `None`, NOT `status`. `status` is always 2xx here -- every
            # non-2xx became an HTTPError before the read loop was reached --
            # so handing it over fired classify_failure's empty-body arm and a
            # snapped connection came back as `empty_content`, which IS in
            # state.PERSISTENT_FAILURE_CLASSES. VERIFIED in this tree:
            # classify_failure(200, "", "IncompleteRead") == "empty_content",
            # against "other" for the same exception with no status. A few flaky
            # resets then promote the domain toward the browser tiers, i.e.
            # windows on the operator's screen (C-3/C-4). With None the
            # EXCEPTION decides the class, which is the only evidence we have:
            # the body was never read, so its emptiness says nothing.
            failure_class=classify_failure(None, "", type(e).__name__),
            elapsed_ms=int((time.monotonic() - t0) * 1000),
        )
    finally:
        try:
            resp.close()
        except Exception:
            pass
    # KNOWN HOLE, PRE-EXISTING, RECORDED AND NOT FIXED IN THIS CHANGE. This
    # call sits outside every try -- here and at the impersonate arm's identical
    # call -- so anything _process_http_response raises (html_to_markdown, the
    # charset work, the PDF branch) leaves a function whose docstring says
    # "Raises nothing". To that extent the docstring OVERSTATES. VERIFIED
    # against HEAD: the same unguarded `md = html_to_markdown(...)` stood at
    # tiers.py:696, inline in tier_http, before this diff existed, so it is not
    # this change's defect and closing it is not in this change's scope.
    return _process_http_response(
        raw=b"".join(chunks), status=status, headers=response_headers,
        final_url=final_url, requested_url=url, redirect_chain=[],
        set_cookie=response_set_cookie, hops=hops,
        t0=t0, deadline=deadline,
    )


def _tier_http_impersonate(url: str, *, connect_timeout: float = 5.0,
                           read_timeout: float = 15.0, total_timeout: float = 30.0,
                           allow_private: bool = False) -> TierResult:
    """Tier 0's SECOND attempt: curl_cffi impersonating Chrome. Raises nothing.

    Run only when the first attempt did not return real content. It rescues a
    DataDome wall (forbes.com, 403 -> 200 with 6,231 chars) and instagram.com;
    it cannot rescue a Cloudflare JS challenge, where it gets the same 403 as
    urllib.

    WE SEND NO HEADERS OF OUR OWN -- not DEFAULT_HEADERS, not a User-Agent,
    nothing. MEASURED in both directions on the same 18-host corpus: forcing
    our three headers on top of the impersonation drops it from 5/18 to 3/18,
    below plain urllib, and destroys BOTH rescues (forbes.com back to 403,
    instagram.com back to 0 chars). Anything we set is appended AFTER the
    profile's `priority` header, a position no real Chrome produces, so three
    "correct-looking" headers are worse than none.
    """
    t0 = time.monotonic()
    deadline = t0 + total_timeout
    # EVERY curl_cffi ATTRIBUTE THIS FUNCTION NEEDS IS RESOLVED HERE, INSIDE
    # THE GUARD, and the guard is wider than ImportError. An import that
    # succeeds proves the package is PRESENT, not that its surface matches
    # ours. MEASURED 2026-08-30 by doctoring the module object in sys.modules
    # (no installed package touched): with the two attribute lookups sitting
    # BELOW the old `except ImportError`, a CurlOpt lacking PROTOCOLS_STR gave
    # `AttributeError: 'types.SimpleNamespace' object has no attribute
    # 'PROTOCOLS_STR'` straight out of this function -- and fetch.py does not
    # wrap tier_http, so a curl_cffi one version off in either direction cost
    # the four tiers that had not run yet.
    #
    # The exception CLASSES are bound here too, not left to the `except exc.X`
    # clauses below. MEASURED the same day: an AttributeError raised while
    # EVALUATING an except clause is not caught by the later clauses of that
    # same try -- a two-clause probe (`except ns.DNSError` on a namespace
    # without it, then `except Exception`) let the AttributeError escape -- so
    # the residual handler cannot contain a renamed class.
    #
    # `Exception` is the width because the alternative to a wide guard is a
    # traceback in place of tiers 1-4. A curl_cffi whose surface we cannot read
    # must degrade to "no second attempt", exactly as an absent one does.
    try:
        from curl_cffi import CurlECode, CurlOpt
        from curl_cffi import requests as curl_requests
        exc = curl_requests.exceptions
        exc_dns, exc_timeout = exc.DNSError, exc.Timeout
        exc_ssl, exc_conn = exc.SSLError, exc.ConnectionError
        code_couldnt_connect = CurlECode.COULDNT_CONNECT
        # PROTOCOL RESTRICTION, RESTORED EXPLICITLY. safe_opener() gets this
        # for free by OMITTING FileHandler and FTPHandler, so under urllib a
        # file:// or ftp:// target -- in the URL or in a Location header -- has
        # no handler that can serve it. libcurl has every protocol compiled in
        # and will follow one happily, and none of safe_opener()'s structure
        # survives the transport change. So the guarantee is re-stated as a
        # curl option, on the request and on redirects both.
        curl_options = {
            CurlOpt.PROTOCOLS_STR: "http,https",
            CurlOpt.REDIR_PROTOCOLS_STR: "http,https",
        }
    except Exception as e:
        # NOT A FAILURE, and this whole block is lazy for exactly that reason:
        # curl_cffi is not installed fleet-wide, and a machine without it --
        # or with one whose surface differs from ours -- must behave precisely
        # as it did before this attempt existed. The orchestrator returns the
        # urllib result whenever this one is not ok, so "there was no second
        # attempt" and "the second attempt lost" take the same path out.
        # `other` is transient and cannot promote.
        return TierResult(
            ok=False, tier="http",
            error=(f"curl_cffi unavailable, so tier 0 has one transport here: "
                   f"{type(e).__name__}: {e}"),
            failure_class="other",
            elapsed_ms=int((time.monotonic() - t0) * 1000),
        )

    def _elapsed() -> int:
        return int((time.monotonic() - t0) * 1000)

    def _fail(e: BaseException, surrogate_name: str) -> TierResult:
        # classify_failure keys off the EXCEPTION NAME, so each caller hands it
        # the name URLLIB produces for this condition, not curl's -- otherwise
        # the two transports would describe one network fault two ways.
        return TierResult(
            ok=False, tier="http", error=str(e) or type(e).__name__,
            failure_class=classify_failure(None, "", surrogate_name),
            elapsed_ms=_elapsed(),
        )

    current_url = url
    visited: list[str] = [url]
    hops: list[str] = []
    # BESIDE `hops`, not instead of it. `hops` feeds `redirect_chain`, whose
    # element type fetch.py's SSRF re-validation depends on; this one carries
    # the per-hop status and origin that a URL list structurally cannot, and
    # its target is the same resolved absolute URL so the two agree.
    hop_rows: list[tuple[int, str, str]] = []
    payload: tuple[bytes, int, dict[str, str], str, list[str]] | None = None
    try:
        with curl_requests.Session(curl_options=curl_options) as session:
            for _hop in range(_IMPERSONATE_MAX_HOPS + 1):
                if time.monotonic() > deadline:
                    return TierResult(
                        ok=False, tier="http", error="total_timeout exceeded",
                        failure_class="timeout", elapsed_ms=_elapsed(),
                    )
                # EVERY HOP, not just the first. The urllib path gets per-hop
                # re-validation free from safety._SafeRedirectHandler, and that
                # handler cannot see a curl_cffi request at all -- libcurl
                # follows a Location header inside the C library, where no
                # Python hook of ours runs. Without this loop a public host
                # that 302s to 127.0.0.1 or 169.254.169.254 is an SSRF hole
                # that tier 0 does not have today. This is the guarantee, not a
                # convenience, and allow_redirects=False below is what makes it
                # enforceable.
                try:
                    validate_url_safety(current_url, allow_private=allow_private)
                except UnsafeURL as e:
                    return TierResult(
                        ok=False, tier="http",
                        error=(f"unsafe redirect target {current_url}: {e}" if hops
                               else f"{_UNSAFE_URL_PREFIX}{e}"),
                        failure_class="other", elapsed_ms=_elapsed(),
                    )
                # BOTH HALVES CLAMPED TO WHAT IS LEFT OF THE BUDGET,
                # recomputed for EVERY hop.
                #
                # WHAT curl_cffi DOES WITH THIS TUPLE, measured 2026-08-30 by
                # calling the INSTALLED
                # curl_cffi.requests.utils.set_curl_options (0.16.2) with a
                # recording curl object, stream=True, and reading back the
                # options it set:
                #     (5.0, 3.0) -> CONNECTTIMEOUT_MS 5000, LOW_SPEED_LIMIT 1,
                #                   LOW_SPEED_TIME 8, TIMEOUT_MS never set
                #     (5.0, 2.0) -> 5000 / 1 / 7    (2.0, 2.0) -> 2000 / 1 / 4
                # So in stream mode there is no wall clock at all: the connect
                # half becomes a real connect timeout, and the read half only
                # feeds LOW_SPEED_TIME -- a stall detector, and one computed
                # from ceil(connect + read), the SUM. An earlier comment here
                # said LOW_SPEED_TIME was taken from the READ HALF; the numbers
                # above refute that (8 seconds for a 3.0 read half). The branch
                # is utils.py:801-810.
                #
                # WHY BOTH HALVES: clamping only the read half left the connect
                # half at whatever the caller passed, so read_timeout=15.0 with
                # total_timeout=2.0 handed curl (5.0, 2.0) and a host that
                # answers SYN and then stalls in TLS spent 5 s of a 2 s budget.
                # RESIDUAL, not closed here: LOW_SPEED_TIME is still the sum, so
                # a stalling read is bounded at ceil(2 x remaining) rather than
                # at `remaining`.
                #
                # An earlier comment also said the deadline was enforced "per
                # hop and below per chunk". REFUTED: a stalled socket yields no
                # chunks, so the per-chunk check never runs, and control never
                # returns to the top of the hop loop either -- what libcurl is
                # handed is the only bound that can fire.
                #
                # The 0.1 s floor keeps a spent budget from handing libcurl a
                # literal 0, which is its "use the default" value for both of
                # these options rather than "no time left" (libcurl's documented
                # behaviour; not measured here).
                left = deadline - time.monotonic()
                per_hop = (max(0.1, min(connect_timeout, left)),
                           max(0.1, min(read_timeout, left)))
                resp = session.get(
                    current_url, impersonate=IMPERSONATE_TARGET,
                    allow_redirects=False, stream=True, timeout=per_hop,
                )
                try:
                    status = resp.status_code
                    headers = {k: v for k, v in resp.headers.items()}
                    headers_l = {k.lower(): v for k, v in headers.items()}
                    location = headers_l.get("location")
                    if status in _REDIRECT_STATUSES and location:
                        target = urllib.parse.urljoin(current_url, location)
                        if visited.count(target) >= _IMPERSONATE_MAX_REPEATS:
                            return TierResult(
                                ok=False, tier="http", status=status,
                                error=(f"redirect loop: {target} visited "
                                       f"{_IMPERSONATE_MAX_REPEATS} times"),
                                failure_class="other", elapsed_ms=_elapsed(),
                            )
                        visited.append(target)
                        hops.append(target)
                        hop_rows.append((status, current_url, target))
                        current_url = target
                        continue
                    if not (200 <= status < 300):
                        # THE HONEST CLASS, as on the urllib path's HTTPError
                        # branch and for the same reason -- the measured table
                        # is there. The two arms are fixed together so they
                        # cannot drift apart into two different answers about
                        # one tier.
                        #
                        # THE BODY IS UNREAD HERE and there is nothing to read
                        # it from yet: this arm runs `stream=True` and nothing
                        # has consumed the response at this point. So the
                        # STATUS alone decides, and a 403 interstitial cannot
                        # be told from a plain origin block -- both come back
                        # `origin_4xx_block`, which is persistent either way,
                        # so the promotion is right and only the name is
                        # coarser than the urllib arm's.
                        #
                        # BEHAVIOURALLY INERT TODAY, and fixed anyway: when the
                        # second attempt fails `tier_http` returns the FIRST
                        # one, so this class never reaches record_failure. It
                        # is what the `sub_attempts` row on the envelope
                        # reports, and an envelope that says `other` beside a
                        # chosen result that says `origin_4xx_block` is a
                        # contradiction the reader has to resolve (R5).
                        return TierResult(
                            ok=False, tier="http", status=status,
                            error=f"HTTP {status} {resp.reason}",
                            failure_class=classify_failure(status, ""),
                            headers=headers,
                            set_cookie=_raw_set_cookie(resp.headers),
                            hops=list(hop_rows), elapsed_ms=_elapsed(),
                        )
                    content_length = headers_l.get("content-length")
                    if content_length:
                        try:
                            if int(content_length) > MAX_HTTP_BYTES:
                                return TierResult(
                                    ok=False, tier="http", status=status,
                                    error=(f"Content-Length {content_length} exceeds "
                                           f"max {MAX_HTTP_BYTES}"),
                                    failure_class="other", elapsed_ms=_elapsed(),
                                )
                        except ValueError:
                            pass
                    chunks: list[bytes] = []
                    total = 0
                    # STREAMED, and the cap is enforced INSIDE the loop. A
                    # buffered read would have the whole oversized body in
                    # memory before anything could refuse it, which is what
                    # MAX_HTTP_BYTES exists to prevent -- the ceiling is 10 MB
                    # and this tier does meet documents that large.
                    #
                    # NO chunk_size, unlike the urllib loop's explicit 64 KiB:
                    # curl_cffi ignores it and warns on every call
                    # ("chunk_size is ignored, there is no way to tell curl
                    # that" -- CurlCffiWarning, 0.16.2). libcurl hands up
                    # whatever it read, so the cap is a running total rather
                    # than a per-chunk bound either way.
                    for chunk in resp.iter_content():
                        if time.monotonic() > deadline:
                            return TierResult(
                                ok=False, tier="http", status=status,
                                error="total_timeout exceeded",
                                failure_class="timeout", elapsed_ms=_elapsed(),
                            )
                        total += len(chunk)
                        if total > MAX_HTTP_BYTES:
                            return TierResult(
                                ok=False, tier="http", status=status,
                                error=f"response exceeded {MAX_HTTP_BYTES} bytes",
                                failure_class="other", elapsed_ms=_elapsed(),
                            )
                        chunks.append(chunk)
                    # Set-Cookie is read HERE, not after the loop: `resp` is
                    # closed by the `finally` below and the flat `headers` dict
                    # has already lost the duplicates.
                    payload = (b"".join(chunks), status, headers, current_url,
                               _raw_set_cookie(resp.headers))
                    break
                finally:
                    try:
                        resp.close()
                    except Exception:
                        pass
            else:
                return TierResult(
                    ok=False, tier="http",
                    error=f"exceeded {_IMPERSONATE_MAX_HOPS} redirects",
                    failure_class="other", elapsed_ms=_elapsed(),
                )
    # ORDER: exactly ONE clause below changes an answer by sitting where it
    # does, and it is DNSError. MEASURED by deleting each clause in turn.
    # DNSError is a ConnectionError subclass, so without its own clause it falls
    # to the ConnectionError handler, which reports type(e).__name__ =
    # "DNSError" -- a name classify_failure has no arm for -- and a name that
    # does not resolve comes back as `other` instead of `dns_fail`.
    #
    # The Timeout and SSLError clauses were asserted to be load-bearing too and
    # that was REFUTED the same way: the ConnectionError and residual handlers
    # both fall back to type(e).__name__, and classify_failure keys on the
    # SUBSTRING "timeout", so ConnectTimeout and ReadTimeout still classify as
    # `timeout` with the whole Timeout clause deleted, and SSLError still
    # classifies as `other` with its own deleted. They are kept as defensive
    # statements of intent -- they pin the answer against a future rename of a
    # curl_cffi class -- not as machinery today's classification depends on.
    #
    # The ConnectionError clause IS load-bearing, but for its BODY and not its
    # position: it is the only place COULDNT_CONNECT becomes `connect_refused`.
    except exc_dns as e:
        return _fail(e, "gaierror")                     # -> dns_fail
    except exc_timeout as e:                            # ConnectTimeout + ReadTimeout
        return _fail(e, "TimeoutError")                 # -> timeout
    except exc_ssl as e:
        # `other`, deliberately and to match: urllib wraps ssl.SSLError in
        # URLError and classify_failure has no TLS arm, so a certificate
        # failure lands on `other` there too.
        return _fail(e, "SSLError")
    except exc_conn as e:
        # COULDNT_CONNECT is the refusal. RECV_ERROR, SEND_ERROR, GOT_NOTHING
        # and WEIRD_SERVER_REPLY are curl_cffi ConnectionErrors too and are NOT
        # refusals, so the curl code decides and not the class.
        refused = getattr(e, "code", None) == code_couldnt_connect
        return _fail(e, "ConnectionRefusedError" if refused else type(e).__name__)
    except Exception as e:
        # BROAD BY CONTRACT: this function documents "Raises nothing" and
        # fetch.py's cascade does not wrap the call, so anything that leaves
        # here kills the remaining four tiers with a traceback. The previous
        # `(exc.RequestException, OSError, ValueError)` did NOT deliver that.
        # MEASURED against curl_cffi 0.16.2: only RequestException is BOTH a
        # CurlError and an OSError -- CurlError.__mro__ is (CurlError,
        # Exception), with no OSError in it -- and curl_cffi applies a Session's
        # `curl_options` through set_curl_options -> Curl.setopt -> _check_error
        # -> `raise CurlError` at requests/session.py:712, OUTSIDE its own
        # `try: c.perform() except CurlError` guard at :828. Our own
        # PROTOCOLS_STR / REDIR_PROTOCOLS_STR is exactly a setopt libcurl can
        # reject (CURLOPT_PROTOCOLS_STR needs libcurl >= 7.85), so on a box
        # whose bundled libcurl is older the bare CurlError walked out of a
        # function promising it would not.
        #
        # What the width costs: one of OUR bugs comes back as a tier-0 failure
        # instead of a traceback. Accepted -- the alternative is a traceback in
        # place of the four tiers that had not run yet.
        return _fail(e, type(e).__name__)
    raw, status, headers, final_url, set_cookie = payload
    return _process_http_response(
        raw=raw, status=status, headers=headers, final_url=final_url,
        requested_url=url, redirect_chain=hops,
        set_cookie=set_cookie, hops=hop_rows, t0=t0, deadline=deadline,
    )


# First-attempt failures a different TLS stack cannot change: a name that does
# not resolve and a port that is closed answer the same way to every client.
_NO_SECOND_ATTEMPT_CLASSES = ("dns_fail", "connect_refused")


def _second_attempt_worth_it(first: TierResult, remaining: float) -> bool:
    """Is one more request worth it after `first` failed?

    Only where the ORIGIN's answer could plausibly differ. Each case below is
    one where it cannot, and each costs a DNS lookup, a TLS handshake and a
    slice of the caller's remaining budget for a foregone conclusion.
    """
    if remaining <= 0:
        return False
    if first.status in (404, 410):
        # Genuinely not there, and impersonation cannot conjure a page. 9 of
        # the 18 corpus URLs are plain 404s; both arms score identically on
        # every one of them.
        return False
    if first.skip_browser_tiers:
        # A DOCUMENT we could not read, not a wall we could not pass. The only
        # setter today is tier_http's PDF branch: the bytes ARRIVED (status 200)
        # and pypdf could not get text out of them. A second transport downloads
        # the same file -- up to MAX_HTTP_BYTES = 10 MB of it -- for pypdf to
        # fail on identically. Impersonation changes who the origin thinks we
        # are; it cannot change what the file contains.
        return False
    if first.failure_class in _NO_SECOND_ATTEMPT_CLASSES:
        return False
    if first.error.startswith(_UNSAFE_URL_PREFIX):
        # We never reached the network -- our own SSRF guard refused the URL,
        # and the second transport re-validates it and would refuse it again
        # one DNS resolution later.
        return False
    return True


def _http_sub_attempt(name: str, r: TierResult) -> dict:
    """One row of TierResult.sub_attempts, keyed exactly like fetch.py's
    tier_attempts rows so the envelope gains no new vocabulary."""
    row = {"tier": name, "ok": r.ok, "elapsed_ms": r.elapsed_ms,
           "failure_class": r.failure_class}
    if not r.ok:
        row["error"] = r.error[:512]
    return row


def tier_http(url: str, *, connect_timeout: float = 5.0, read_timeout: float = 15.0,
              total_timeout: float = 30.0, allow_private: bool = False) -> TierResult:
    """Tier 0: urllib, then curl_cffi impersonation if that did not land. Raises nothing.

    ONE TIER, two transports -- see the block comment above IMPERSONATE_TARGET
    for why the second is an ATTEMPT and not a replacement.

    When the second attempt fails, the FIRST attempt's result is what comes
    back: its error is the more representative diagnosis of the origin, and it
    is what the blocklist has recorded for this tier since the skill existed.
    The second is visible in `sub_attempts` either way.
    """
    t0 = time.monotonic()
    deadline = t0 + total_timeout
    first = _tier_http_urllib(
        url, connect_timeout=connect_timeout, read_timeout=read_timeout,
        total_timeout=total_timeout, allow_private=allow_private)
    if first.ok:
        return first
    if not _second_attempt_worth_it(first, deadline - time.monotonic()):
        return first
    second = _tier_http_impersonate(
        url, connect_timeout=connect_timeout, read_timeout=read_timeout,
        total_timeout=deadline - time.monotonic(), allow_private=allow_private)
    chosen = second if second.ok else first
    chosen.sub_attempts = [_http_sub_attempt("http(urllib)", first),
                           _http_sub_attempt("http(impersonate)", second)]
    # The tier really did take both attempts, and this is the number fetch.py
    # logs and reports for tier 0. The per-attempt figures survive in
    # sub_attempts.
    chosen.elapsed_ms = int((time.monotonic() - t0) * 1000)
    return chosen


# ---- Playwright session management (D11) -------------------------------------

_SESSIONS: dict[str, "PlaywrightSession"] = {}
_SIGNAL_HANDLERS_INSTALLED = False


def _run_pw(args: list[str], timeout: float) -> tuple[int, str, str]:
    """Run playwright-cli with args, return (returncode, stdout, stderr)."""
    # THE WORKING DIRECTORY IS RESOLVED OUT HERE, AND THE PLACEMENT IS THE
    # POINT -- inside the `try` below it would be wrong in two directions at
    # once. A `mkdir` failure raising `FileNotFoundError` would be caught by
    # that block's own arm and reported as `playwright-cli not found on PATH`,
    # naming the wrong cause entirely; and any OTHER `OSError` would propagate,
    # which `_ensure_open` reads as an INDETERMINATE launch and answers by
    # HOLDING a profile claim -- for a browser that was never spawned, because
    # this failure happens before `subprocess.run` is reached at all. Out here
    # the failure says what it is and returns an ORDINARY exit code, which is
    # the range `_ensure_open` RELEASES on. Found by cross-family review
    # 2026-09-13; the first draft of this change had it inside.
    #
    # HOW REACHABLE: barely, and that is not a reason to leave it wrong. Every
    # path through `main()` opens the database from this same root
    # (`fetch.py:1705`) before `startup_reap` (`:1718`) or any tier can spawn,
    # so reaching this arm needs the directory to become unusable MID-PROCESS.
    # An undeterminable home raises `RuntimeError` rather than `OSError` and is
    # not caught here, for the same reason: `state.open_db()` would already
    # have failed on it.
    #
    # RESIDUAL, STATED NOT FIXED, AND IT IS WIDER THAN THE FIRST DRAFT SAID.
    # The resolution above is outside the `try`, but `cwd` is CONSUMED inside
    # it, so the spawn itself can fail on the directory -- and those failures
    # are not the ones handled above:
    #   * DELETED between creation and spawn -> `FileNotFoundError`, caught by
    #     the arm below and reported as `not found on PATH`. The wrong cause,
    #     and separating it from a genuinely missing binary means reading
    #     platform-specific fields off the exception.
    #   * PERMISSIONS REVOKED, or a path component replaced by a file ->
    #     `PermissionError` / `NotADirectoryError`. Neither is caught here, so
    #     each PROPAGATES, and `_ensure_open` reads a raised `OSError` as an
    #     INDETERMINATE launch and HOLDS a profile claim -- for a browser that
    #     cannot exist, because `subprocess.run` raising means no child was
    #     ever created.
    # ACCEPTED rather than fixed, for the reason the design already gives: that
    # is the same bounded over-refusal `_ensure_open` documents for a crash
    # before the daemon started -- one slot of ten, released at process exit --
    # and converting it would mean catching `OSError` around the spawn, which
    # is precisely what `_run_pw_cleanup` exists NOT to do on the launch path.
    # The window is between two adjacent statements, and the directory was
    # created microseconds earlier by the call above.
    # Cross-family review round 2 found this; the first draft disclosed only
    # the deletion half and claimed the repair excluded both wrong outcomes.
    try:
        cwd = str(state.state_dir())
    except OSError as e:
        return 1, "", ("playwright-cli working directory unavailable: "
                       f"{type(e).__name__}: {e}")
    try:
        proc = subprocess.run(
            [PLAYWRIGHT_BIN] + args,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout,
            env={**os.environ, "NO_UPDATE_NOTIFIER": "1"},
            cwd=cwd,
        )
        return proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired:
        return -1, "", f"playwright-cli timeout after {timeout}s"
    except FileNotFoundError:
        return -1, "", "playwright-cli not found on PATH"


# ---- Navigation WITH response capture ----------------------------------------
#
# `goto` hands back no Response, so the browser tiers called is_real_content()
# with the status HARD-CODED to 200: the non-2xx branch could never fire, and
# classify_failure() could never tell origin_4xx_block from a content failure,
# so blocklist promotion was driven by a guess. `run-code` does give us the
# Response object.
#
# THE URL IS ENCODED AS CHARACTER CODES, and that is load-bearing rather than
# decoration. On Windows `playwright-cli` is a .cmd shim, so subprocess runs it
# THROUGH cmd.exe, which treats `&` as a command separator. Encode the URL as
# character codes so no URL byte can become a shell metacharacter;
# String.fromCodePoint(...) contains only numeric data and separators.
#
# fromCodePoint, NOT fromCharCode: Python's ord() yields a full code POINT,
# but fromCharCode truncates to 16 bits, so any astral character silently
# became a different one. MEASURED 2026-08-29 against the live sandbox --
# ord('U+1F600') = 128512, and fromCharCode(128512) returns U+F600, a
# private-use glyph, so the browser navigated to a DIFFERENT URL. Emoji and
# other non-BMP characters appear in ordinary URLs; no adversary required.
# Cross-model review finding, round 2.
#
# MEASURED 2026-08-29 inside run-code's sandbox: `atob` is NOT defined and
# `Buffer` is NOT defined, which is why this is fromCodePoint and not base64.
# The snippet must also be an ARROW FUNCTION -- the CLI wraps it as
# `await (fn)(page)`, so statements and bare expressions both raise SyntaxError.

_RUNCODE_MAX_ARG = 6000  # keep total argv well under cmd.exe's ~8191 limit


def _navigate_capture(session_name: str, url: str, timeout: float):
    """Navigate via `run-code`, capturing main-frame status and headers.

    Returns None when this ROUTE is unavailable for any reason -- a
    playwright-cli without `run-code`, a sandbox that rejects the snippet, an
    over-long URL, unparseable output. The caller then falls back to plain
    `goto`, which is exactly the pre-2026-08-29 behaviour. A missing capability
    must NEVER turn a working fetch into a failed one.

    Returns {"ok": False, "error": str, "url": str} when the route worked but
    the NAVIGATION failed. That is a real fetch failure and the caller must NOT
    retry with `goto` -- doing so would double the latency of every failing
    browser fetch.

    Returns {"ok": True, "status": int|None, "headers": dict, "url": str}
    on success.
    """
    try:
        codes = ",".join(str(ord(c)) for c in url)
    except Exception:
        return None
    js = (
        "async (page) => { const u = String.fromCodePoint(" + codes + "); "
        "try { const r = await page.goto(u, {waitUntil:'load'}); "
        "if (!r) return JSON.stringify({ok:true, status:null, headers:{}, url:page.url()}); "
        "let h = {}; try { h = await r.allHeaders(); } catch (e) { h = {}; } "
        "return JSON.stringify({ok:true, status:r.status(), headers:h, url:r.url()}); } "
        "catch (e) { return JSON.stringify({ok:false, error:String(e).slice(0,300), url:page.url()}); } }"
    )
    if len(js) > _RUNCODE_MAX_ARG:
        return None
    rc, out, err = _run_pw(["-s=" + session_name, "run-code", js], timeout=timeout)
    if rc != 0:
        # A TIMEOUT IS A NAVIGATION FAILURE, NOT AN ABSENT ROUTE. `_run_pw`
        # reports TimeoutExpired as rc=-1 with "timeout" in the message, and
        # returning None here would send the caller into a SECOND navigation
        # via `goto` -- doubling the cost of every slow fetch and blowing
        # through total_timeout. Cross-model review finding, 2026-08-29.
        if "timeout" in (err or "").lower():
            return {"ok": False,
                    "error": (err or "").strip() or "run-code navigation timed out",
                    "url": url}
        return None
    lines = out.splitlines()
    for i, line in enumerate(lines):
        if line.strip() == "### Result" and i + 1 < len(lines):
            try:
                # The CLI JSON-encodes our returned string: hence a double decode.
                payload = json.loads(json.loads(lines[i + 1].strip()))
            except Exception:
                return None
            if not isinstance(payload, dict):
                return None
            if not payload.get("ok"):
                return {"ok": False,
                        "error": str(payload.get("error") or "navigation failed"),
                        "url": payload.get("url") or url}
            status = payload.get("status")
            if status is not None and not isinstance(status, int):
                status = None
            headers = payload.get("headers")
            if not isinstance(headers, dict):
                headers = {}
            return {"ok": True, "status": status, "headers": headers,
                    "url": payload.get("url") or url}
    return None


# ---- Profile claim / release (D19) -------------------------------------------
#
# Tier 1/2 use --profile=<dir> for persistent cookies/login state. Chromium
# locks the user-data-dir so only ONE Chromium can use a given profile dir at a
# time. We support multiple concurrent Chromiums by scanning a numbered series
# (<base>, <base>2, ..., <base>N) and claiming the first free dir with an
# ADVISORY OS LOCK held for the lifetime of the session (msvcrt.locking /
# fcntl.flock). The kernel drops that lock when the holder exits or dies, so a
# crash needs no cleanup and there is no such thing as a stale claim. The lock
# file is never deleted (`_release_profile_dir`) and lives OUTSIDE the profile
# dir it guards, under the per-user state root (`_lock_path_for`), so deleting
# or recreating a profile dir cannot split one claim into two.


def _candidate_profile_dirs():
    """Yield `<base>`, `<base>2`, ..., `<base>N`."""
    yield PROFILE_BASE
    for i in range(2, PROFILE_MAX_INSTANCES + 1):
        yield PROFILE_BASE + str(i)


def _pid_state(pid: int) -> str:
    """Return "alive", "dead", or "unknown" for pid.

    THE THIRD STATE IS THE POINT. The previous version collapsed every failure
    to probe into "dead", so on Windows a LIVE owner whose `OpenProcess` was
    merely refused (ERROR_ACCESS_DENIED, a different token or elevation) read as
    dead -- and the orphan reaper would then close and delete that live peer's
    session mid-fetch. Only a probe that positively identifies a missing process
    may return "dead"; every other outcome is "unknown" and callers must treat
    unknown as still-running.
    """
    if pid <= 0:
        return "dead"
    if sys.platform == "win32":
        try:
            import ctypes
            # `use_last_error=True` IS REQUIRED and its absence was a real bug.
            # `ctypes.get_last_error()` reads a ctypes-maintained thread-local
            # that is only populated for handles opened this way. Read against
            # plain `ctypes.windll.kernel32` it returns a value THIS call never
            # set -- so a stale 87 from any earlier ctypes call could outrank the
            # real error and turn ACCESS_DENIED on a LIVE pid into "dead",
            # letting the reaper close and delete a live peer's session.
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            PROCESS_QUERY_LIMITED_INFO = 0x1000
            ERROR_INVALID_PARAMETER = 87   # no such pid -- the ONLY proof of death
            ERROR_ACCESS_DENIED = 5        # pid EXISTS, we may not query it
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFO, False, pid)
            if handle:
                kernel32.CloseHandle(handle)
                return "alive"
            err = ctypes.get_last_error()
            if err == ERROR_INVALID_PARAMETER:
                return "dead"
            if err == ERROR_ACCESS_DENIED:
                return "alive"
            return "unknown"
        except Exception:
            return "unknown"
    try:
        os.kill(pid, 0)
        return "alive"
    except ProcessLookupError:
        return "dead"
    except PermissionError:
        return "alive"   # exists, not ours to signal
    except OSError:
        return "unknown"


def _pid_alive(pid: int) -> bool:
    """True unless the pid is PROVABLY gone. Fails toward alive.

    The orphan reaper is the caller that matters: it treats alive as "leave it
    alone", so an unknown probe costs one un-reaped session record rather than a
    live peer's session. Profile-dir ownership no longer consults pids at all --
    the OS lock decides that.
    """
    return _pid_state(pid) != "dead"


def _lock_path_for(profile_dir: str) -> Path:
    """Path of the lock file guarding `profile_dir`.

    THE LOCK LIVES OUTSIDE THE DIRECTORY IT GUARDS, and that placement is the
    fix for a real hole. A lock file's IDENTITY -- its inode -- is the whole
    rendezvous mechanism. Keep it inside the profile dir and anything that
    deletes that dir destroys the rendezvous while holders are still running:

        A holds the lock on the old inode.
        A temp cleaner (or an `rm -rf` of the profile tree) removes the dir.
        B recreates the path, creates a NEW lock file, and locks THAT inode.
        A and B hold locks on different objects and cannot see each other, so
        both believe they own the same profile pathname.

    `state.state_dir()` is `~/.local/share/webfetch`, which is per-user on
    every platform and is not a scratch location anything sweeps. The name is
    derived from the normalised profile path so it is stable across runs and
    unique per dir; the profile dir itself may come and go beneath it without
    ever disturbing the lock.
    """
    d = state.state_dir() / "locks"
    d.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(_normalize_path(profile_dir).encode("utf-8")).hexdigest()[:16]
    return d / ("profile-%s.lock" % key)


def _lock_fd_exclusive(fd: int) -> bool:
    """Take a non-blocking exclusive lock on fd. True if acquired.

    The lock -- not the file's existence -- is what claims a profile dir, and
    that is the whole design. THE KERNEL RELEASES IT WHEN THE HOLDER DIES,
    crash included, so there is no such thing as a stale claim and nothing ever
    has to decide whether some other process's marker may be deleted.

    The scheme this replaces could not be made correct by patching. It wrote a
    pid into a file and reclaimed the dir by UNLINKING that file when the pid
    looked dead; two processes could both judge the same marker stale, and the
    second one's unlink removed the FIRST one's freshly created replacement, so
    both walked away believing they owned the dir. No amount of re-reading after
    the fact closes that -- the check and the unlink cannot be made atomic
    without a lock, so the lock is what we use.
    """
    try:
        if sys.platform == "win32":
            import msvcrt
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except (OSError, ImportError):
        return False


def _unlock_fd(fd: int) -> None:
    """Best-effort release. Closing the fd releases the lock regardless."""
    try:
        if sys.platform == "win32":
            import msvcrt
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_UN)
    except (OSError, ImportError):
        pass


def _try_claim_profile_dir(profile_dir: str,
                           sessions=_SESSIONS_UNSET) -> int | None:
    """Claim profile_dir. Returns a HELD fd on success, None if unavailable.

    The caller MUST keep the fd open for as long as it uses the dir and pass it
    to `_release_profile_dir`. Closing the fd -- deliberately, or by dying --
    is what frees the dir.

    Two guards, in order:
      1. The OS lock. Covers every live holder that is one of our processes.
      2. A playwright-session liveness check. Covers the case the lock cannot:
         the playwright DAEMON outliving the Python process that spawned it, so
         Chromium still has the dir open while no process of ours holds a lock.
    """
    try:
        marker = _lock_path_for(profile_dir)
        fd = os.open(str(marker), os.O_CREAT | os.O_RDWR)
    except OSError as e:
        # Report it rather than swallowing: on POSIX this is what a permissions
        # problem looks like, and silently returning None makes the caller
        # describe the dir as "held" -- pointing at the wrong cause.
        print(f"webfetch: cannot open profile lock for {profile_dir}: {e}",
              file=sys.stderr)
        return None
    if not _lock_fd_exclusive(fd):
        os.close(fd)
        return None
    # We hold the lock. Every exit path from here must release it or hand it
    # back -- an early `return None` that skipped this would lose the dir until
    # the process exits.
    try:
        # Cheap pre-reject against the caller's sweep snapshot, when supplied.
        if sessions is not _SESSIONS_UNSET and _playwright_session_uses_profile(
                profile_dir, sessions):
            _release_profile_dir(profile_dir, fd)
            return None
        # AUTHORITATIVE re-check, against a FRESH listing, after taking the lock.
        # A snapshot cannot license a claim, only refuse one. The interleaving
        # that makes this mandatory: the snapshot is taken; another process then
        # opens a session on this dir and DIES, dropping its OS lock while its
        # playwright daemon keeps Chromium alive on the profile. We would find
        # the lock free and the stale snapshot silent, and reuse a live profile.
        if _playwright_session_uses_profile(profile_dir):
            _release_profile_dir(profile_dir, fd)
            return None
        try:
            os.ftruncate(fd, 0)
            os.write(fd, str(os.getpid()).encode("utf-8"))
        except OSError:
            pass   # informational only; the LOCK is the claim, not the content
        return fd
    except Exception:
        _release_profile_dir(profile_dir, fd)
        raise


def _playwright_sessions_summary(*, diagnose: bool = False) -> list[dict] | None:
    """Parse `playwright-cli list` into {name, status, user_data_dir} dicts.

    Returns None when the answer is UNKNOWN, which callers must not confuse with
    "no sessions". Three ways to be unknown:
      * the command could not be RUN at all -- `subprocess.run` raised an
        OSError, e.g. PermissionError. That used to propagate out of here and
        out of every caller, all of which document None for this case;
      * the command failed (rc != 0) -- previously flattened to `[]`, which read
        as "nothing holds any dir" and licensed claiming every one of them;
      * the command succeeded but not one record yielded a status, i.e. the
        output format moved under us. Without this check a format change makes
        every record parse blank, and blank statuses would be interpreted
        wholesale -- in whichever direction `_session_is_live` defaults.

    NEVER RAISES for any of the three. See the comment on the `list` call for
    why the OSError catch lives here rather than in `_run_pw`.
    """
    # THE CONTRACT OF THIS WHOLE PATH IS None, NOT AN EXCEPTION. This function,
    # `_list_sessions`, `reap_zombies` and `startup_reap` all document None as
    # "could not enumerate", and every caller handles it. `_run_pw` catches only
    # TimeoutExpired and FileNotFoundError, so a PermissionError -- or any other
    # OSError out of `subprocess.run` -- came straight through all four and
    # crashed a `--reap-zombies` whose documented answer was None.
    #
    # THE CATCH GOES HERE AND NOT IN `_run_pw`, and the asymmetry is deliberate
    # rather than an oversight to tidy up. `_ensure_open` DEPENDS on an OSError
    # propagating out of its `open` call: that is what makes a launch
    # INDETERMINATE, which is what keeps `launched` True and holds the profile
    # claim. Widen `_run_pw` to swallow OSError and every indeterminate launch
    # becomes a reported failure -- `launched` goes False and the handler
    # releases a claim under a possibly-live browser, which is the exact defect
    # this file's `claimed_here` guard exists to prevent. One helper, two
    # callers, opposite consequences: the same trap as `_session_is_live` versus
    # `_session_is_open`.
    #
    # OSError IS THE BOUNDARY, chosen rather than defaulted to. It covers every
    # way the ENVIRONMENT can refuse to spawn -- PermissionError, NotADirectory,
    # ENOEXEC, EMFILE -- which is what "could not enumerate" means. Deliberately
    # NOT caught: `ValueError`, which `subprocess.run` raises for an invalid
    # argument combination and which could only mean WE built the call wrong,
    # so swallowing it would hide our own bug behind a shrug; `TimeoutExpired`,
    # already handled inside `_run_pw` and returned as rc=-1 through the check
    # below; and anything not deriving from Exception. No decode error can
    # arise: `_run_pw` reads with errors="replace".
    try:
        rc, out, err = _run_pw(["list"], timeout=10)
    except OSError as e:
        if diagnose:
            print("webfetch: could not list playwright sessions "
                  f"({type(e).__name__}: {str(e)[:200]}); treating as UNKNOWN",
                  file=sys.stderr)
        return None
    if rc != 0:
        if diagnose:
            print(f"webfetch: could not list playwright sessions (rc={rc}): "
                  f"{err.strip()[:200]}", file=sys.stderr)
        return None
    sessions: list[dict] = []
    current: dict | None = None
    for raw in out.splitlines():
        line = raw.rstrip()
        if not line.startswith(" ") and line.startswith("- "):
            # Session header: `- {name}:`
            if not line.endswith(":"):
                # Upstream ALWAYS writes the colon (renderBrowser:
                # `lines.push(`- ${browser.name}:`)`), so a header without
                # one means the stream was cut mid-name. Parsing it would
                # invent a session -- `- webf` becomes a session called
                # `webf`, which then reads as somebody else's and gets
                # skipped, licensing a claim on a dir the real
                # `webfetch-headless-4242` still holds.
                if diagnose:
                    print("webfetch: playwright session list ends in a "
                          "truncated header (%r); treating as UNKNOWN"
                          % line[:40], file=sys.stderr)
                return None
            if current is not None:
                sessions.append(current)
            name = line[2:].rstrip(":").strip()
            current = {"name": name, "status": "", "user_data_dir": "",
                       "truncated": False}
            continue
        if current is None:
            continue
        stripped = line.strip()
        if stripped.startswith("- status:"):
            current["status"] = stripped.split(":", 1)[1].strip()
        elif stripped.startswith("- user-data-dir:"):
            current["user_data_dir"] = stripped.split(":", 1)[1].strip()
    if current is not None:
        sessions.append(current)
    if sessions and out and not out.endswith("\n"):
        sessions[-1]["truncated"] = True
        if diagnose:
            print("webfetch: playwright session list ends mid-record "
                  "(%r); last record marked truncated"
                  % (sessions[-1].get("name"),), file=sys.stderr)
    if sessions:
        if not any((s.get("status") or "").strip() for s in sessions):
            if diagnose:
                print("webfetch: playwright session list parsed "
                      f"{len(sessions)} record(s) but not one status; the "
                      "output format moved. Treating as UNKNOWN",
                      file=sys.stderr)
            return None   # records parsed, statuses understood: none. Drift.
        return sessions
    # ZERO records parsed. That is only genuinely "no sessions" when upstream
    # SAYS so. If the header syntax moves, nothing matches our record parser and
    # we would otherwise report an empty list -- indistinguishable from an idle
    # machine, and enough to license claiming a dir a live Chromium still holds.
    if _NO_BROWSERS_MARKER in out:
        return []
    if diagnose:
        print("webfetch: playwright session list was not understood (no "
              "records parsed and no empty-list marker); treating as UNKNOWN",
              file=sys.stderr)
    return None


def _normalize_path(p: str) -> str:
    try:
        return str(Path(p).resolve()).lower() if sys.platform == "win32" else str(Path(p).resolve())
    except OSError:
        return p.lower() if sys.platform == "win32" else p


def _session_is_live(s: dict) -> bool:
    """True unless this record is PROVABLY a closed session.

    `playwright-cli list` RECOMPUTES `status` per call from a live
    `session.canConnect()` probe; it is not a stored field. Upstream emits it
    from a two-way ternary, so the vocabulary is exactly {open, closed} today.

    "closed" is the only value treated as dead. Anything else -- a state
    upstream adds later, or a blank we failed to parse -- counts as live,
    because guessing dead hands out a directory that Chromium may still have
    open. Wholesale parse failure does NOT reach this function: the listing
    returns None for that (see `_playwright_sessions_summary`), so a blank here
    is a single odd record rather than evidence the format changed.

    A PREVIOUS REVISION OF THIS DOCSTRING WAS FALSE and the code it justified
    was unsafe. It claimed this function "is only ever consulted when no marker
    exists", and used that to argue a blank status could safely mean dead. The
    claim was wrong -- the dead-pid reclaim path consulted it too -- so a live
    session whose status did not parse could have its directory taken. The
    lesson generalises: a safety argument that names a call path must be checked
    against the call graph, not against intent.
    """
    return (s.get("status") or "").strip().lower() != "closed"


def _session_is_open(s: dict) -> bool:
    """True only for a record that POSITIVELY reports an open session.

    THE OPPOSITE SAFETY POLARITY FROM `_session_is_live`, which is why this is a
    second predicate and not a reuse of that one. Both answer "is there a
    browser here", and each defaults the unknown case toward ITS OWN caller's
    cheap mistake:

      * `_session_is_live` guards a profile-dir CLAIM. There, saying "live"
        wrongly costs one refused claim; saying "dead" wrongly hands out a
        directory Chromium still holds. So anything but `closed` is live.
      * this guards ADOPTION. There, saying "open" wrongly marks the session
        usable with nothing launched and no dir claimed -- `tab-new` then fails
        and the early return repeats that verdict for the life of the process.
        Saying "not open" wrongly costs one extra launch attempt, which fails
        loudly with `Browser is already in use` if a browser really was there.

    So the permissive default is right THERE and wrong HERE, and a single
    predicate serving both call sites is the defect rather than the tidy-up.
    `_session_is_live` is unchanged; do not merge them.

    `truncated` IS NOT REDUNDANT next to the equality test, though it looks it.
    A status cut mid-write cannot equal `open` while the vocabulary is
    {open, closed} -- but upstream emitting a longer value later, `opening` say,
    reintroduces exactly that: cut it and a fragment reading `open` is
    byte-for-byte a complete one. The flag is already on the record (the parser
    marks the last record when the listing does not end in a newline), so
    carrying it costs nothing and removes a whole class rather than today's
    instance of it.
    """
    return (not s.get("truncated")
            and (s.get("status") or "").strip().lower() == "open")


def _playwright_session_uses_profile(profile_dir: str,
                                     sessions=_SESSIONS_UNSET) -> bool:
    """Return whether a live session occupies this numbered profile.

    Unknown listings and truncated or directory-less live owners refuse the claim.
    Only public headless/headed session families own numbered profiles; attached
    sessions do not. Closed records alone do not retain occupancy."""
    if sessions is _SESSIONS_UNSET:
        sessions = _playwright_sessions_summary()
    if sessions is None:
        # We could not read the session list. Report the dir as IN USE: refusing
        # to claim costs one directory, claiming a dir Chromium still has open
        # corrupts a profile and can wedge two browsers together.
        return True
    target = _normalize_path(profile_dir)
    for s in sessions:
        if not _session_is_live(s):
            continue
        if s.get("truncated") and (s.get("name") or "").startswith(
                PROFILE_OWNING_SESSION_PREFIXES):
            # OURS, live, and its record was cut mid-write, so whatever
            # directory it reports is a FRAGMENT of the real one. A
            # fragment is not evidence that the session is somewhere
            # else, and here it is positively misleading: our dirs are a
            # numbered series whose members are prefixes of one another,
            # so the truncation of one is a plausible-looking mismatch
            # against another. Refuse this pass.
            #
            # Gated on ownership for the same reason as the dir-less arm
            # below: a truncated FOREIGN record, or one of our own
            # webfetch-cdp-* ones, must stay harmless, or a single live
            # session elsewhere wedges every dir in the series.
            print("webfetch: our session %r is live and its listing "
                  "record was cut mid-write, so the directory it reports "
                  "is a fragment; no profile dir can be claimed this pass"
                  % (s.get("name"),), file=sys.stderr)
            return True
        udd = s.get("user_data_dir") or ""
        if not udd:
            # NO DIRECTORY REPORTED, and rendered text cannot always say
            # why. Upstream omits the line for an ATTACHED session by
            # design (renderBrowser puts the whole block behind
            # `if (!browser.attached)`), and a TRUNCATED record lacks it
            # too. The `(attached)` suffix does not separate them either:
            # browser-type is itself emitted only `if (browser.browserType)`,
            # so an attached session without one renders a header, a status,
            # and nothing else -- identical to a cut record.
            #
            # So decide on OWNERSHIP, which is always present in the header
            # and is what this guard is actually about: the daemon that
            # outlives its parent process is OURS.
            if not (s.get("name") or "").startswith(
                    PROFILE_OWNING_SESSION_PREFIXES):
                # NOT A NUMBERED-PROFILE OWNER. Either somebody else's
                # session, or one of our own `webfetch-cdp-*` ones, which
                # attach to the user's Chrome and are ALWAYS rendered
                # without a user-data-dir -- so matching the bare
                # `webfetch-` prefix here made one live CDP fetch wedge
                # every dir. Refusing would
                # report every candidate busy for as long as it lives, and
                # that is the measured 2026-08-23 outage (10/10 dirs
                # blocked, both browser tiers dead) -- worse than the hole
                # it would close.
                # KNOWN GAP, STATED RATHER THAN ARGUED AWAY: if a foreign
                # session did hold one of our numbered dirs and reported no
                # directory, we would claim it. Nothing in the listing can
                # rule that out, and an earlier version of this comment
                # asserted it could not happen -- that assertion was
                # unfounded and is withdrawn.
                continue
            # OURS, live, and reporting no directory: the record should
            # have carried one. We cannot say which dir it holds, so we
            # must not hand any of them out on the strength of it. This is
            # the defect this branch fixes: it used to be skipped, and
            # `_try_claim_profile_dir` could grant a dir a live Chromium
            # still had open.
            print("webfetch: our session %r is live but reports no "
                  "user-data-dir; the listing is truncated or its format "
                  "moved, so no profile dir can be claimed this pass"
                  % (s.get("name"),), file=sys.stderr)
            return True
        if _normalize_path(udd) == target:
            return True
    return False


def _close_playwright_sessions_on_profile(profile_dir: str,
                                          sessions=_SESSIONS_UNSET) -> None:
    """Best-effort: close LIVE playwright-cli sessions whose user-data-dir matches.

    Skips already-closed records for the same reason as
    `_playwright_session_uses_profile`: they outlive their session, so without
    the liveness test this spawns one pointless `close` subprocess per stale
    record -- 152 of them on the machine where this was found.
    """
    if sessions is _SESSIONS_UNSET:
        sessions = _playwright_sessions_summary()
    if sessions is None:
        return   # unknown: nothing safe to target
    target = _normalize_path(profile_dir)
    for s in sessions:
        if not _session_is_live(s):
            continue
        udd = s.get("user_data_dir") or ""
        if udd and _normalize_path(udd) == target:
            _run_pw(["-s=" + s["name"], "close"], timeout=PLAYWRIGHT_CLOSE_TIMEOUT)


def _claim_profile_dir() -> tuple[str, int]:
    """Find and claim a free profile dir. Returns (path, held_fd).

    The caller owns the fd and MUST pass it to `_release_profile_dir`.

    Raises RuntimeError if every candidate is in use.
    """
    last_err: Exception | None = None
    # One `playwright-cli list` up front, used ONLY to reject candidates cheaply
    # -- that call costs 0.38-0.47s at 200 records and was previously re-spawned
    # once per candidate. It is NOT sufficient to authorise a claim: a snapshot
    # can go stale in a way that hides a live session (see
    # `_try_claim_profile_dir`), so a candidate is re-checked against a FRESH
    # listing once its lock is held.
    #
    # COST, stated accurately because an earlier version of this comment claimed
    # "two listings per fetch" and that was false. Each candidate whose lock we
    # win costs one more listing, so a sweep that keeps winning locks and then
    # being refused by the fresh check pays per candidate -- and at the 10s list
    # timeout an unresponsive CLI could burn PROFILE_MAX_INSTANCES * 10s BEFORE
    # `fetch_html` starts its own clock. The deadline below bounds that: the
    # sweep gives up rather than grinding through every candidate.
    sweep_deadline = time.monotonic() + PROFILE_SWEEP_BUDGET
    sweep_sessions = _playwright_sessions_summary()
    for candidate in _candidate_profile_dirs():
        if time.monotonic() > sweep_deadline:
            raise RuntimeError(
                f"profile-dir sweep exceeded {PROFILE_SWEEP_BUDGET}s before finding "
                f"a free dir under {PROFILE_BASE}*. The playwright session list is "
                f"likely slow or unresponsive; try `python fetch.py --reap-zombies`, "
                f"or use --max-tier http to skip the browser tiers."
            )
        try:
            Path(candidate).mkdir(parents=True, exist_ok=True)
        except OSError as e:
            last_err = e
            continue
        try:
            fd = _try_claim_profile_dir(candidate, sweep_sessions)
        except OSError as e:
            last_err = e
            continue
        if fd is not None:
            return candidate, fd
    detail = f" (last filesystem error: {last_err})" if last_err else ""
    unreadable = (" The playwright session list could not be read, so every dir "
                  "was treated as in use." if sweep_sessions is None else "")
    # A dir-less record of ours means UNKNOWN, not held. Saying 'held' here
    # sends the operator to wait or reap when the listing is the problem.
    ambiguous = ""
    if sweep_sessions:
        # PROFILE_OWNING_SESSION_PREFIXES, not the bare webfetch- prefix:
        # the predicate ignores webfetch-cdp-* sessions, which attach to
        # the user own Chrome and are ALWAYS rendered without a
        # user-data-dir. Matching the broad prefix here announced that
        # occupancy could not be established whenever a CDP fetch was
        # live, whatever had actually exhausted the series -- one
        # vocabulary describing two different mechanisms.
        if any(not (s.get("user_data_dir") or "").strip()
               and (s.get("name") or "").startswith(
                   PROFILE_OWNING_SESSION_PREFIXES)
               and _session_is_live(s) for s in sweep_sessions):
            ambiguous = (" At least one of our sessions is live but reports no "
                         "user-data-dir, so occupancy could not be established "
                         "and every dir was treated as in use -- the session "
                         "listing, not the dirs, may be the problem.")
    raise RuntimeError(
        f"no playwright profile dir could be claimed: {PROFILE_BASE}..{PROFILE_BASE}"
        f"{PROFILE_MAX_INSTANCES}{detail}.{unreadable}{ambiguous} No candidate dir could be "
        f"claimed: each is held by a LIVE browser session or a running process, or "
        f"could not be ruled out. Either wait for those "
        f"to finish, raise WEBFETCH_PLAYWRIGHT_PROFILE_MAX, or run "
        f"`python fetch.py --reap-zombies` to close them and delete their "
        f"session records."
    )


def _release_profile_dir(profile_dir: str | None, fd: int | None = None) -> None:
    """Release our claim by closing the locked descriptor. Idempotent.

    THE LOCK FILE IS NEVER UNLINKED, AND THAT IS LOAD-BEARING. An earlier
    revision unlinked it after closing, which reintroduced the very race the
    lock was brought in to kill -- in a subtler form:

        A unlocks and closes its fd.
        B opens the SAME INODE and locks it. B now owns the dir.
        A executes its unlink and removes the PATHNAME out from under B.
        C creates a fresh file at that path and locks THAT inode -- a different
        object, so C's lock and B's lock cannot see each other.
        B and C both believe they own the same Chromium profile.

    A lock file is a rendezvous point: its identity is the whole mechanism, so
    it must be stable for the lifetime of the directory, not the claim. One
    small file per profile dir persists forever. That is the intended cost.

    `profile_dir` is retained for call-site readability and diagnostics; it is
    deliberately not used to touch the filesystem. The lock file itself lives
    outside that directory (see `_lock_path_for`), so it survives the profile
    dir being deleted and recreated.
    """
    if fd is not None:
        _unlock_fd(fd)
        try:
            os.close(fd)
        except OSError:
            pass


def _list_sessions(open_only: bool = False) -> list[str] | None:
    """Return public owned session names, or None for an unreadable listing.

    Destructive callers require all records to parse and no detected mid-line cut,
    and include closed persistent records for cleanup. open_only=True instead
    requires positive open evidence from the owned record; unrelated parsed
    records cannot veto it. None is not an empty list.

    Newline-boundary truncation is indistinguishable from a shorter valid listing;
    counts describe only listed sessions and never prove a complete sweep."""
    sessions = _playwright_sessions_summary(diagnose=True)
    if sessions is None:
        return None
    if open_only:
        # ADOPTION ASKS ABOUT ONE RECORD, so it is answered from that record and
        # the two all-record guards below are skipped entirely. `_ensure_open`
        # neither closes nor deletes anything, so the completeness bar those
        # guards reach for -- "have I seen the WHOLE listing before I destroy
        # what is in it", which they approximate rather than settle -- is not
        # its bar. Under it, one malformed FOREIGN record threw away our own
        # complete `open` record and cost the fetch.
        #
        # NO OVER-PERMISSION IS POSSIBLE HERE. `_session_is_open` requires a
        # status of exactly `open` on an untruncated record, so a statusless or
        # truncated record OF OURS still fails it -- the guards were never what
        # excluded those. Skipping them only stops records that are not ours
        # from voting on a question about ours.
        #
        # RESIDUAL, stated not fixed: THAT LAST SENTENCE HOLDS ONLY FOR
        # POST-PARSE CONDITIONS, and an earlier revision of this comment stated
        # it without the qualifier, which overstated the fix. What the split
        # removed is real but narrow:
        #
        #   * REMOVED -- an unrelated record that PARSED but carried no status
        #     no longer vetoes ours. That record exists, is judged, and loses.
        #   * STILL VETOES -- anything that stops the PARSER: a foreign header
        #     cut before its colon, a listing whose format has drifted, a
        #     listing that could not be read or run at all. Each returns None
        #     from `_playwright_sessions_summary`, above, and adoption never
        #     sees a record to judge. MEASURED against the real parser: our own
        #     complete `open` record plus one foreign header missing its colon
        #     yields None here, so `_ensure_open` falls through and claims while
        #     our session is still live.
        #
        # THE COST IS THE OVER-REFUSAL DIRECTION and it is recoverable: one
        # fetch claims a dir, `open` fails loudly against the live session, and
        # the cascade steps past that tier. It repeats for as long as the
        # malformed record persists.
        #
        # NOT FIXED HERE because the remedy changes a contract this branch is
        # not scoped to change: carry parser completeness SEPARATELY from the
        # records already parsed, so a destructive caller can still reject an
        # incomplete listing while adoption reads an exact-name, exact-`open`,
        # untruncated record out of the part that DID parse. That is a new
        # return shape for `_playwright_sessions_summary`, which every caller
        # reads.
        #
        # An unreadable listing is still None, above: `_ensure_open` falls
        # through and claims on None, which is the right answer for unknown.
        return [s["name"] for s in sessions
                if s["name"].startswith(PLAYWRIGHT_SESSION_PREFIX)
                and _session_is_open(s)]
    # EVERY record must have parsed, not merely one of them -- and this is
    # STRICTER than the summary on purpose, because this path's callers
    # CLOSE AND DELETE what they are handed while the summary's other callers
    # only inspect.
    #
    # The summary accepts a listing when ANY record carried a status, which is
    # the right bar for asking "is this dir in use". It is the wrong bar for
    # enumerating what to destroy: a listing truncated mid-stream can carry one
    # complete record and one bare header, and the complete one then vouches
    # for the whole thing. `reap_zombies` would close the sessions it could see
    # and report a COMPLETE sweep while a session past the cut kept running --
    # the exact false completeness the None/[] distinction exists to prevent.
    # (Cross-family review, 2026-08-29.)
    #
    # A statusless record is therefore UNKNOWN, never "a session with no
    # status". Erring here costs a `--reap-zombies` that declines to run and
    # says so; erring the other way costs a sweep that lies.
    incomplete = [s for s in sessions if not (s.get("status") or "").strip()]
    if incomplete:
        print("webfetch: playwright session list has "
              f"{len(incomplete)} of {len(sessions)} record(s) with no status "
              "(truncated or drifted output); treating this listing as UNKNOWN "
              "-- rerun, and if it repeats the output format has moved",
              file=sys.stderr)
        return None
    # AND A CUT LANDING MID-LINE AFTER A COMPLETE STATUS LINE, which the guard
    # above cannot see. Cut a listing inside `- user-data-dir: ...` and every
    # status present is non-blank, so `incomplete` is empty and the listing
    # vouches for itself -- while whatever followed the cut never arrived.
    # `reap_zombies` then sweeps the visible names and reports a COMPLETE sweep,
    # which is the same false completeness, reached by a route the status check
    # does not cover. The parser already marks this (it sets `truncated` on the
    # last record when the output does not end in a newline); nothing read it.
    #
    # THIS IS NOT THE OVER-REFUSAL THAT CAUSED THE 2026-08-29 OUTAGE, and the
    # objection is worth answering because it is the obvious one to raise. That
    # bug returned None for listings that were COMPLETE and HEALTHY and merely
    # held no session of ours -- it keyed on the absence of an `webfetch-` hit.
    # This keys on `truncated`, which the parser sets only when the output
    # genuinely did not end in a newline. A healthy listing always does, so this
    # cannot fire on one. Different predicates; the earlier lesson does not
    # transfer.
    #
    # RESIDUAL, stated not fixed: THIS GUARD NARROWS FALSE COMPLETENESS, IT
    # DOES NOT ELIMINATE IT. The whole signal is "the output did not end in a
    # newline", so exactly one shape is visible to it:
    #
    #   * CAUGHT -- a cut landing MID-LINE. The bytes stop partway through a
    #     field, no newline follows, and that absence is the trace this reads.
    #   * NOT CAUGHT -- a cut landing ON A NEWLINE BOUNDARY, whether between
    #     records or between two fields of ONE record (`- status: open` and its
    #     newline arrive, the rest never does). That output is byte-for-byte a
    #     SHORTER VALID LISTING: every record present is complete, every status
    #     parses, the final newline is there. Nothing distinguishes it from a
    #     listing that genuinely ended, so `reap_zombies` can still report a
    #     complete sweep over a partial listing by that route.
    #
    # NOT FIXABLE HERE, and the alternative is worse. Upstream prints records
    # and stops -- no count, no terminator, measured in its renderer on 0.1.1
    # and 0.1.17 -- so there is no completeness signal to require. Refusing
    # every listing that cannot PROVE it is complete would refuse all of them,
    # since none can, and that is `--reap-zombies` declining on healthy machines
    # -- the 2026-08-29 outage, restored. The docstring's first "TWO
    # LIMITATIONS" bullet is this same residual seen from the caller's side; it
    # states the consequence, this states the mechanism.
    #
    # THE DIAGNOSTIC BELOW SAYS ONLY WHAT THIS REFUSAL IS, as does its sibling
    # above. Neither carries the "rather than reporting a sweep that may be
    # incomplete" clause both once did, because that phrasing invites the
    # inverse reading -- that a listing which passes has been found complete. It
    # has not: passing means no cut was DETECTED. An operator reading stderr
    # needs what happened, what we did, and what to do next, and nothing in
    # either message should imply a guarantee the code cannot make.
    #
    # why-not: retry the listing once and refuse only if the cut repeats. None
    # is already this function's word for "could not enumerate" and every caller
    # handles it correctly today. A retry is machinery, and it would hide an
    # intermittent cut rather than report it.
    truncated = [s for s in sessions if s.get("truncated")]
    if truncated:
        print("webfetch: playwright session list was cut mid-write "
              f"({len(truncated)} of {len(sessions)} record(s)); treating this "
              "listing as UNKNOWN -- rerun to get a complete one",
              file=sys.stderr)
        return None
    return [s["name"] for s in sessions
            if s["name"].startswith(PLAYWRIGHT_SESSION_PREFIX)]


# Preferred channel, for BOTH modes. `--browser=chrome` reaches real Google
# Chrome directly -- the CLI documents `--browser` as "browser or chrome channel
# to use", so one argv token does it and no config file is involved.
PLAYWRIGHT_CHANNEL = "chrome"
# What we drop to on a box where that channel is not installed.
PLAYWRIGHT_FALLBACK_CHANNEL = "chromium"

# state.config keys. Named so a human can find and clear them by hand:
#   sqlite3 ~/.local/share/webfetch/state.db
#     "DELETE FROM config WHERE key LIKE 'playwright_%'"
CHANNEL_CONFIG_KEY = "playwright_browser_channel"
USER_AGENT_CONFIG_KEY_PREFIX = "playwright_user_agent_"
# How long a probed user agent stays usable. THIS cache expires where the channel
# cache deliberately does not, because it tracks a browser that updates ITSELF --
# see `_cached_user_agent` for why a permanent one decays into the tell it
# exists to remove.
USER_AGENT_MAX_AGE_S = 7 * 24 * 3600


def _config_get(key: str) -> str | None:
    """Read one key from state.config. None on ANY failure.

    Fail-soft is the point: a cache miss costs one probe, but an exception
    escaping here costs the whole tier. The DB can be locked by a concurrent
    fetch, absent on a first run, or on a read-only volume, and none of those is
    a reason not to launch a browser.
    """
    try:
        conn = state.open_db()
        try:
            value = state.Config(conn).get(key)
        finally:
            conn.close()
    except Exception:
        return None
    return value or None


def _config_set(key: str, value: str) -> None:
    """Persist one key to state.config. Silent on ANY failure (see `_config_get`)."""
    try:
        conn = state.open_db()
        try:
            state.Config(conn).set(key, value)
        finally:
            conn.close()
    except Exception:
        pass


def _resolve_browser_channel() -> str:
    """Return the persisted browser channel, preferring installed Google Chrome.

    A confirmed missing-channel launch demotes to chromium and persists that
    choice. Installing Chrome later does not automatically re-probe; clear the
    playwright_browser_channel config entry to restore the preferred probe."""
    return _config_get(CHANNEL_CONFIG_KEY) or PLAYWRIGHT_CHANNEL


def _is_channel_missing(text: str, channel: str) -> bool:
    """Match a missing-distribution failure for exactly the requested channel.

    The channel must occupy the distribution-name slot in one complete failure
    phrase. A path containing chrome or an unrelated line must not demote it."""
    return re.search(
        r"(?<![\w-])distribution\s+['\"]?" + re.escape(channel.lower())
        + r"['\"]?\s+is not found(?![\w-])",
        (text or "").lower()) is not None


def _dehead_user_agent(ua: str | None) -> str | None:
    """The headful form of `ua`, or None when there is nothing usable.

    ONE SUBSTITUTION: `HeadlessChrome/` -> `Chrome/`. That is the entire
    difference between what headless Chrome announces and what the same build
    announces headed (measured, arm H) -- version, platform and Safari token are
    already identical. Which is also why the override cannot be hardcoded: it is
    derived from the browser's own string and drifts with it, and a UA whose
    major disagrees with `navigator.userAgentData.brands` is a worse tell than
    the headless one it replaced.

    A UA carrying no Headless token is already what we want and comes back
    unchanged. An empty or non-UA string yields None so the caller ships NO
    override at all -- a mangled userAgent is louder than a headless one.
    """
    if not ua:
        return None
    ua = ua.strip()
    if "Mozilla/" not in ua:
        return None
    return ua.replace("HeadlessChrome/", "Chrome/")


def _parse_eval_string_result(stdout: str) -> str | None:
    """The JSON string printed under `### Result`, or None.

    Output shape:
      ### Result
      "Mozilla/5.0 (Windows NT 10.0; ...) HeadlessChrome/151.0.0.0 Safari/537.36"
      ### Ran Playwright code
    """
    lines = stdout.splitlines()
    for i, line in enumerate(lines):
        if line.strip() != "### Result":
            continue
        for j in range(i + 1, len(lines)):
            if lines[j].startswith("### "):
                break
            try:
                value = json.loads(lines[j].strip())
            except Exception:
                continue
            if isinstance(value, str):
                return value
    return None


def _read_user_agent(session_name: str, timeout: float) -> str | None:
    """Read this session's native user agent, with no navigation or new browser."""
    rc, out, _err = _run_pw(
        ["-s=" + session_name, "eval", "function(){return navigator.userAgent}"],
        timeout=timeout)
    if rc != 0:
        return None
    return _parse_eval_string_result(out)


def _cached_user_agent(channel: str) -> str | None:
    """Return a fresh stored de-headed user agent for this channel, or None.

    Reject absent, legacy, malformed, blank, future-dated and expired records.
    Return an accepted string unchanged. Expiry avoids permanent browser-version
    drift without paying a user-agent probe on every launch; cold/refresh launches
    use the native headless user agent and cache their result for later launches."""
    raw = _config_get(USER_AGENT_CONFIG_KEY_PREFIX + channel)
    if not raw:
        return None
    try:
        record = json.loads(raw)
        user_agent = record["ua"]
        stored_at = float(record["ts"])
    except Exception:
        return None
    if not isinstance(user_agent, str) or not user_agent.strip():
        return None
    # NaN falls out here too: every comparison against it is False.
    if not 0 <= time.time() - stored_at <= USER_AGENT_MAX_AGE_S:
        return None
    return user_agent


def _store_user_agent(channel: str, user_agent: str) -> None:
    """Persist a probed UA with the timestamp `_cached_user_agent` expires on.

    LAST WRITER WINS, DELIBERATELY. Cross-family review round 2 raised the race:
    two cold processes straddling a Chrome update, where the one that opened the
    OLD build finishes last and overwrites the new UA with a newer timestamp,
    leaving the mismatch in place for up to seven days. It is real and it is not
    fixed. why-not a compare-and-swap on an observation timestamp: reaching this
    line at all requires a cold cache, which happens about once a week per box;
    two of them must overlap within the ~5 s a launch-and-probe takes, on a
    single-user machine, AND a Chrome update must land inside that window. The
    damage is one stale major bounded by the same expiry the race is about --
    i.e. the outcome the cache already tolerates by design -- and the remedy is
    a read-modify-write protocol over SQLite for a case with no observed
    instance. Reopen this if a UA/brands mismatch is ever actually seen.
    """
    _config_set(USER_AGENT_CONFIG_KEY_PREFIX + channel,
                json.dumps({"ua": user_agent, "ts": time.time()}))


def _context_config_path(profile_dir: str) -> str:
    """Path of the per-session `--config` file, beside the profile dir it serves."""
    p = Path(profile_dir)
    return str(p.parent / (p.name + "-config.json"))


def _write_context_config(profile_dir: str, user_agent: str | None) -> str:
    """Write the per-session `--config` file and return its path.

    `contextOptions` ONLY. Three deliberate absences, each a measured refutation
    rather than an oversight:

      * NO `launchOptions.channel`. `--browser=` on the argv OVERRIDES it -- arm
        B set `channel: "chrome"` in the file, kept `--browser=chromium`, and got
        Chromium; arm E set the identical channel with NO `--browser` flag and
        got Google Chrome. This file LOOKS like the natural home for the channel
        and is not: put it here while keeping `--browser=chromium` and you have
        shipped a silent no-op whose only symptom is a brand list nobody checks.
        The channel goes on the argv -- see `_ensure_open`.
      * NO `locale`. `locale: "en-US"` cut `Accept-Language` from
        `en-US,en;q=0.9` down to `en-US`, and `navigator.languages` from
        `["en-US","en"]` to `["en-US"]` (arms B, C) -- stripping the q-weighted
        fallback that every other arm, including the shipped baseline, already
        sends. It makes us MORE distinguishable, not less.
      * NO `launchOptions.args`. `--screen-info` did not move `screen.width`
        (arm F, still 1280); `contextOptions.screen` did (arms B, C, 1920). And
        any `--disable-blink-features=<anything>` here would silently DISABLE
        upstream's own `--disable-blink-features=AutomationControlled`, which it
        adds only `if (!args.some(a => a.includes("--disable-blink-features")))`.

    `user_agent` is omitted entirely when None -- see `_ensure_open` for why the
    first process on a fresh machine has nothing to write.
    """
    context_options: dict = {}
    if user_agent:
        context_options["userAgent"] = user_agent
    context_options["viewport"] = {"width": 1280, "height": 720}
    context_options["screen"] = {"width": 1920, "height": 1080}
    path = _context_config_path(profile_dir)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(
        json.dumps({"browser": {"contextOptions": context_options}}),
        encoding="utf-8")
    return path


def _remove_context_config(profile_dir: str | None) -> None:
    """Delete the per-session config file. Idempotent, BEST-EFFORT, never raises.

    VERIFIED 2026-08-30 that this is safe to do the moment `open` returns: with
    the file already deleted, a later `tab-new` + `eval` still reported the
    overridden UA, the 1920x1080 screen and the 1280x720 viewport. `open`
    consumes the file at launch and nothing re-reads it.

    IT DOES NOT RUN ON EVERY PATH, and an earlier version of this docstring said
    it did. Cleanup runs after a normal success and after a CONFIRMED failure --
    the two cases where what happened is known. An INDETERMINATE launch
    exception deliberately RETAINS the file, for the same reason it retains the
    claim: a browser may be starting on it. That retention is harmless for the
    reason the next paragraph gives -- the following claimant truncates and
    rewrites this exact path before it launches, so it can never read a
    predecessor's content.
    (Cross-family review 2026-08-31, round 4 finding [1].)

    WHAT GUARANTEES A FRESH CONFIG IS THE WRITE, NOT THIS DELETE. An unlink can
    fail for reasons that are not "already gone" -- a scanner holding the handle,
    a read-only volume, an ACL -- and the file then stays on disk while this
    reports nothing. That is harmless, but for a reason worth naming:
    `_write_context_config` TRUNCATES AND REWRITES this exact path before every
    launch, so the next claimant of the slot reads its own content and can never
    inherit ours. Do not read the swallow below as a claim that the file is gone.
    It is a claim that nobody reads a file that is left.

    refuted: propagate anything that is not FileNotFoundError (cross-family
    review 2026-08-30 round 1 finding [1]). It would fail a launch that
    SUCCEEDED, over a temp file nothing needs again -- over-refusal, which this
    project has measured as the expensive direction: one live CDP session once
    wedged all ten profile dirs and took both browser tiers down with it.
    """
    if not profile_dir:
        return
    try:
        os.unlink(_context_config_path(profile_dir))
    except OSError:
        pass


class _PlaywrightPrerequisiteError(RuntimeError):
    """A proven pre-navigation refusal, distinct from an attempted page failure."""

    def __init__(self, error_code: str, message: str):
        super().__init__(message)
        self.error_code = error_code


class PlaywrightSession:
    """Long-lived playwright-cli session for a given mode (headless or headed)."""

    def __init__(self, mode: str):
        if mode not in ("headless", "headed"):
            raise ValueError(f"mode must be 'headless' or 'headed', got {mode!r}")
        self.mode = mode
        self.session_name = (PLAYWRIGHT_HEADED_SESSION if mode == "headed"
                             else PLAYWRIGHT_HEADLESS_SESSION)
        self._opened = False
        # D19: each session claims its own numbered profile dir on open
        # (e.g. <tmp>/playwright, playwright2, ...). Released on close.
        self._profile_dir: str | None = None
        # Held lock fd for _profile_dir. The dir is ours only while this is open.
        self._profile_fd: int | None = None
        # ONE EXTRACTION PER DOCUMENT. `fetch_html` has to extract the document
        # to evaluate the settle precondition -- `_challenge_reason` judges the
        # EXTRACTED text -- and `tier_playwright` then needs the same markdown
        # for the gate and for the result body. Trafilatura is the most
        # expensive step on tiers 1 and 2, and running it twice on every fetch
        # (including the ~60% that never enter the settle loop) spends that
        # cost inside the fetch deadline, i.e. against R1. The slot holds
        # (html, url, markdown) for the LAST document extracted; `_extract`
        # fills it and `_extracted_markdown` reads it back.
        self._extract_memo: tuple[str, str, str] | None = None

    def _extract(self, html: str, url: str) -> str:
        """`html_to_markdown(html, url)`, computed once per document.

        Keyed on the IDENTITY of the html string and on the url, not on their
        contents: hashing a megabyte to save an extraction is the wrong trade,
        and two reads of a changing document are never the same object. The url
        is part of the key because it is an argument to the extractor; a
        document re-extracted under a different url is a different call and is
        not served from here.
        """
        memo = self._extract_memo
        if memo is not None and memo[0] is html and memo[1] == url:
            return memo[2]
        md = html_to_markdown(html, url)
        self._extract_memo = (html, url, md)
        return md

    def _ensure_open(self) -> None:
        # THE OPT-OUT IS HONOURED HERE, at the launch site, and not only
        # in the cascade that usually leads here. Shaping fetch.py's tier
        # list keeps the cascade honest but stops nothing on its own: on
        # a box that has genuinely no display, a direct
        # tier_playwright(mode="headed") call -- a future caller, a test,
        # a one-liner someone pastes while debugging -- walks straight
        # past the cascade and tries to launch anyway. The guard's job is
        # to honour an explicit WEBFETCH_DISABLE_HEADED, not to
        # withhold a window from a fetch that needs one.
        #
        # IT MUST COME BEFORE THE `_opened` EARLY RETURN. Behind it, a
        # session opened while the variable was unset stayed usable after
        # it was set: `_SESSIONS` caches per mode for the life of the
        # process, so the next call returned early and went on to open a
        # tab on the live headed browser, reactivating the window. No new
        # process starts, so nothing looks like a launch -- and a test
        # that only ever builds a FRESH session cannot see it.
        # "May this machine show a window" is not answered by "have we
        # already opened one".
        # (Cross-family review 2026-08-29 round 1 finding [1], round 2
        # finding [0].)
        if self.mode == "headed" and not headed_tier_allowed():
            raise _PlaywrightPrerequisiteError("headed_disabled",
                "headed tier is disabled on this machine by "
                f"{HEADED_TIER_DISABLE_ENV}; unset it to allow the fetch "
                "to use a real browser window.")
        if self._opened:
            return
        # An unreadable listing must not read as 'session absent'. On unknown
        # we fall through and claim a dir; if the session really does exist,
        # `open` fails loudly, which beats silently adopting a session on a
        # profile dir we usually do not hold. (Usually, not always -- see the
        # two cases at the adoption branch below.)
        #
        # POSITIVELY OPEN ONLY. A record is not a session: ours are persistent
        # so upstream keeps the record after `close()`, and a `close()` whose
        # `delete-data` failed leaves one under our exact name.
        #
        # A NAME COLLISION IS NOT PROOF THE RECORD IS OURS, and an earlier
        # revision of this comment said the opposite -- "names embed our own
        # pid, so this is same-process only". False, and `startup_reap`'s own
        # docstring refutes it: a pid can be REUSED, and a reused pid produces
        # the SAME session name, which is why that function re-probes
        # immediately before acting. A persistent daemon record also outlives
        # the Python process that made it. What is actually true is narrower:
        # two SIMULTANEOUSLY LIVE owners cannot share a pid, so a record
        # reporting `open` is a live session we may adopt. A record under our
        # name can otherwise be the residue of a DIFFERENT, earlier process.
        # That is part of why the positive-open check is required rather than a
        # tidy-up: it is what makes the distinction, since the name does not.
        #
        # Adopting a stale one set `_opened` with nothing launched and no dir
        # claimed. `closed` is not the only shape that must not be adopted: a
        # status cut MID-VALUE parses (`- st`
        # of `- status: closed` yields `clo`), which is why the predicate tests
        # for `open` rather than for the absence of `closed` -- see
        # `_session_is_open` for why that is a separate predicate from
        # `_session_is_live` and not its negation.
        # A record we cannot judge -- no status, or cut mid-write -- is excluded
        # BY THE PREDICATE, one record at a time. It is not adopted, we fall
        # through and claim, which is the right answer for unknown. That outcome
        # for OUR record has never changed; the MECHANISM did. It used to be the
        # all-record completeness guard in `_list_sessions` collapsing the whole
        # listing to None, which meant an UNRELATED record's drift decided our
        # case and could veto a perfectly good record of ours. Now no PARSED
        # record outside our own has a vote -- but a foreign record that stops
        # the PARSER still takes the whole listing with it, and `_list_sessions`
        # states that residual on its `open_only` branch. A listing that could
        # not be read at
        # all is still None, and still falls through and claims.
        existing = _list_sessions(open_only=True)
        if existing is not None and self.session_name in existing:
            # TWO CASES REACH THIS BRANCH, and the claim is ours in one of them.
            # Both are correct; neither needs an action here.
            #
            #   * NO CLAIM OF OURS IS HELD (`_profile_dir` None) -- somebody
            #     else's session under our name, one an EARLIER session object
            #     of ours opened, or one belonging to a DIFFERENT process that
            #     held this pid before us. Its profile claim was made by whoever
            #     opened it, and we take none: `_opened` goes True and this
            #     object never learns which dir that session is on.
            #   * OUR OWN RETAINED ATTEMPT HAS BECOME VISIBLE (`_profile_dir`
            #     set) -- an indeterminate launch on THIS object kept its claim,
            #     and the listing now shows the session it started. We made that
            #     claim, and the dir is the one we passed as `--profile=`, so it
            #     is exactly the dir that browser is running on. Keeping it is
            #     right, and it is what lets `close()` release it afterwards:
            #     `_opened` is True from here, so `close()` no longer returns at
            #     its first line. This is the GOOD outcome of the retained-claim
            #     path -- the indeterminate launch turning out to have succeeded
            #     -- not an edge case.
            #
            # An earlier revision of this comment said flatly that the claim was
            # one "we didn't make". That is true only of the first case.
            self._opened = True
            return
        # D19: claim a numbered profile dir before opening.
        #
        # ONLY IF WE ARE NOT ALREADY HOLDING ONE. Reaching here with
        # `_profile_dir` set means a previous launch in this process raised
        # while `launched` was True and the handler below deliberately KEPT the
        # claim. Relaunching on it is right: nobody else can have taken the dir,
        # because we still hold the OS lock. Claiming a second one instead
        # overwrote `_profile_fd` and stranded the first until process exit --
        # out of a pool of ten shared across every process on the box.
        #
        # THAT RETAINED PATH IS THE ONLY WAY IN, and the reuse rests on it, so
        # it is enumerated rather than asserted. `_profile_dir` is written in
        # exactly four places: `__init__` (None), the claim below, the handler
        # (nulled, and only when `launched` is False), and `close()`. Reaching
        # this line requires `_opened` False, which rules the rest out --
        # adoption above and a successful launch both set `_opened` True and
        # return through the check at the top; `close()` succeeding nulls the
        # pair in the same breath as clearing `_opened`; `close()` FAILING
        # returns early and leaves `_opened` True, so the top check fires. What
        # remains is the handler declining to release, which is exactly the
        # retained-indeterminate case. The pair is always consistent:
        # `_claim_profile_dir` returns both values together and the handler
        # nulls both together.
        #
        # `claimed_here` DISTINGUISHES A REUSED CLAIM FROM A FRESH ONE, and the
        # release below is gated on it. Without it the reuse is a REGRESSION,
        # not a fix: call 1 raises with `launched` True and keeps dir A because
        # a browser may be live on it; call 2 reuses A, `open` returns rc != 0,
        # `launched` goes back to False -- and the handler releases A out from
        # under call 1's browser. The "rc != 0 means no browser exists"
        # reasoning is sound only for an ORDINARY CLI failure on a claim taken
        # in THIS invocation -- see the arm below for the rc side of that. On a
        # reused one it is inverted: `Browser is already in use` is precisely
        # what a live browser on that dir produces, so we would be concluding
        # "no browser" from the evidence of one.
        #
        # A reused claim stays held when relaunch fails. The kernel releases it
        # on process exit; releasing it beneath a possibly live browser would
        # allow another process to claim the same active profile.
        #
        # RESIDUAL, stated not fixed: `close()` CANNOT release such a claim. It
        # returns at its first line while `_opened` is False, and a launch that
        # raised never set it -- so the only ways out are a LATER `_ensure_open`
        # on this object that succeeds or adopts, after which `close()` releases
        # normally, and process exit, where the kernel drops the lock. A
        # long-running repeat caller that gives up after a failed launch
        # therefore keeps one pool slot -- of ten, shared across processes --
        # for the rest of its life.
        #
        # AND IT IS NOT ONLY THE SLOT. In the indeterminate POST-spawn case a
        # browser really is live, and the same early return means `close()`
        # never closes it either: the caller leaks the browser session too,
        # until a later `_ensure_open` adopts it -- which is what makes that
        # adoption path matter -- or the process exits. Accepted, and the
        # comparison is the point: the alternative is releasing a dir beneath a
        # browser that may still be on it, which costs the NEXT claimant
        # `Browser is already in use` rather than costing this one a slot it was
        # already done with. Releasing here would need positive evidence that no
        # browser remains, which is precisely what this code cannot obtain. An
        # earlier revision of this comment claimed `close()` covered it; it
        # never did.
        #
        # why-not: refuse to call `open` at all on a reused claim. It buys no
        # safety this flag does not already buy, and it gives up the case the
        # reuse exists for -- a first failure that really was pre-spawn, where
        # the retry succeeds. Pre- and post-spawn are not distinguishable from
        # here (an OSError out of `communicate()` arrives after the daemon is
        # up), so refusing would be refusing every retry to cover some of them.
        claimed_here = False
        if self._profile_dir is None:
            self._profile_dir, self._profile_fd = _claim_profile_dir()
            claimed_here = True
        # THE HANDLER BELOW IS GATED ON `launched`, and the flag is set BEFORE
        # `_run_pw` rather than after it, so that "a browser may be live" is the
        # DEFAULT state for the whole launch.
        #
        # Hold under uncertainty: the kernel drops an advisory lock when its
        # holder exits. Releasing it while a browser may still be alive can
        # instead give the next claimant a profile already in use.
        #
        # `launched` goes back to False the instant `open` REPORTS failure, and
        # WHAT THAT PROVES DEPENDS ON WHOSE CLAIM IT IS. Establish which case
        # you are in before reading any of the reasoning below.
        #
        #   * ON A FRESH CLAIM (`claimed_here`) a rc THE PROCESS ITSELF
        #     REPORTED is the BEST evidence available that no browser
        #     exists, and the arm below tests that set -- an ORDINARY exit
        #     status, `0 <= rc < 256`. It used to test `rc != -1`, which was
        #     wider still: an abnormal termination arrives as a positive
        #     NTSTATUS value and reported nothing at all.
        #
        #     IT IS NOT PROOF, AND AN EARLIER DRAFT OF THIS PARAGRAPH SAID IT
        #     WAS. `open` spawns the daemon DETACHED before the CLI finishes
        #     its own work, so a later throw in that process becomes an
        #     unhandled rejection and Node exits 1 -- an ordinary code, in
        #     range, RELEASING, with a daemon already up on this dir. The
        #     narrowing removes the abnormal-termination half of that window;
        #     it does not close the window. What remains is the same
        #     over-permission shape, reachable only through an ordinary exit,
        #     and `_try_claim_profile_dir`'s second guard is what catches it
        #     one process later. Read the arm before relying on this.
        #     Releasing here is otherwise safe BECAUSE
        #     nothing on this dir predates this invocation, and it is also
        #     necessary: an escaping
        #     exception used to leave the fd held on a session that never
        #     opened, and the next attempt then overwrote `_profile_fd` and
        #     stranded that dir until the process exited. rc == -1 is NOT such
        #     a report. It is `_run_pw` saying no exit status ever arrived, so
        #     it is INDETERMINATE and keeps the claim -- see the arm below.
        #   * ON A REUSED CLAIM it proves NOTHING. The browser that may exist
        #     came from an earlier invocation, which `launched` -- describing
        #     only this one -- cannot see, and `Browser is already in use` is
        #     exactly the non-zero rc such a browser produces. A reported
        #     failure there is INDETERMINATE and must not bypass `claimed_here`.
        #
        # So `launched` False is a necessary condition for releasing, never a
        # sufficient one. Both conjuncts, or a lock goes out from under a live
        # browser -- see the claim site above.
        #
        # KNOWN COST, accepted: an INDETERMINATE launch holds the claim even
        # where no browser was ever started, and THREE routes reach one.
        # (It was two until the release arm narrowed to an ordinary exit
        # status; the third is that narrowing's own cost.)
        #
        #   * `_run_pw` RAISING (PermissionError and every other OSError -- it
        #     catches only TimeoutExpired and FileNotFoundError). A spawn
        #     failure there means no browser exists, and it is not
        #     distinguishable from here: an OSError out of `communicate()`
        #     arrives after the daemon is already up.
        #   * `_run_pw` returning rc == -1. The timeout case genuinely may
        #     leave a browser: `subprocess.run`'s kill reaps the CLI CHILD
        #     only, and the daemon that child started is a GRANDCHILD that
        #     survives it. This process still holds the lock at that moment, so
        #     the hazard is not losing it but handing it on.
        #     `_try_claim_profile_dir`'s SECOND guard exists for the same shape
        #     one process later -- a daemon outliving the Python process that
        #     spawned it, where no lock of ours remains at all.
        #   * `_run_pw` returning an rc OUTSIDE 0..255 -- an abnormal
        #     termination. A crash that fell BEFORE the daemon spawned holds a
        #     claim with no browser anywhere, and nothing at the launch site
        #     can tell which side of the spawn it fell on. This route is the
        #     price of the arm below, taken deliberately: the same rc landing
        #     AFTER the daemon is up is the case it exists to protect.
        #
        # THE BOUND ON THAT COST DEPENDS ON THE CALLER, and an earlier version
        # of this comment gave only the reassuring half of it ("one stranded dir
        # per failed launch in a process whose browser tier is dead anyway").
        # Both halves are true and neither is honest alone.
        #
        # THROUGH THE SHIPPED CLI IT IS NOT REACHABLE. `fetch.py` takes one
        # `--url` per invocation and runs ONE cascade loop over it (`for
        # tier_idx in ...`, fetch.py:289), and `get_session` hands back a
        # SEPARATE session object per mode -- so `_ensure_open` runs at most
        # once per mode per process. The ceiling is two held claims, in a
        # process that lives seconds, and the kernel drops both at exit.
        #
        # FOR A REPEAT CALLER IT IS REACHABLE, AND IT USED TO ACCUMULATE.
        # `_SESSIONS` caches one session per mode for the life of the process,
        # and the gate comment below names the class exactly: a future caller, a
        # test, a one-liner someone pastes while debugging. Each retry past an
        # OSError claimed another dir and overwrote `_profile_fd`, stranding the
        # previous one until the process exited. THE POOL IS SHARED ACROSS
        # PROCESSES -- ten dirs is the whole series -- so a process accumulating
        # that way took the browser tiers away from every other process on the
        # box, not only from itself.
        #
        # FIXED, at the claim above: a retry REUSES the claim it already holds
        # rather than taking a second one, so the ceiling is one claim per
        # session object however many times the launch fails. What is still true
        # is the first half -- one held claim per failed launch, dropped by the
        # kernel at process exit.
        # (Cross-family review 2026-08-31, round 4 finding [0].)
        #
        # rc == -1 IS INDETERMINATE AND KEEPS THE CLAIM. `_run_pw` returns it
        # for a timeout and for a missing binary, and the missing-binary half
        # cannot cost a claim on EVERY fetch on a box without the binary, which
        # is what the trade recorded here before this change had it doing. The
        # handler below fires only where `claimed_here` is set; that is set only
        # where `_claim_profile_dir()` RETURNED; and `_claim_profile_dir` takes
        # no cached listing -- it runs `_playwright_sessions_summary()` itself.
        # With no binary that returns None, every candidate dir then reads as in
        # use, and the claim RAISES above this `try` and outside it, so the
        # spawn is never reached.
        #
        # IT IS A RACE, NOT AN IMPOSSIBILITY, and the line is worth spending:
        # the binary can be removed BETWEEN that listing and the `open`
        # microseconds later. rc == -1 then lands on a fresh claim with no
        # browser anywhere and we hold one dir for the life of the process.
        # That is over-refusal -- the cheap direction, bounded by the paragraph
        # below -- and not the every-fetch leak.
        #
        # What holding does cost is the bound stated above and nothing more:
        # one pool slot per session object, at most two per process through the
        # shipped CLI, dropped by the kernel at exit. `close()` cannot give one
        # back -- it returns early while `_opened` is False -- so a repeat
        # caller that keeps timing out keeps its slot until the process ends.
        launched = False
        try:
            # THE CHANNEL GOES ON THE ARGV, for both modes, and must NOT be
            # moved into the --config file. MEASURED: `--browser=` OVERRIDES
            # `launchOptions.channel` there, so a config-file channel next to
            # `--browser=chromium` is a silent no-op (see
            # `_write_context_config`).
            channel = _resolve_browser_channel()
            user_agent = None
            # AT MOST TWO PASSES. The retry guard fires only while `channel` is
            # not already the fallback, and the one thing it does is set it, so
            # this cannot spin.
            while True:
                # The UA cache is keyed by CHANNEL and EXPIRES: real Chrome
                # and bundled Chromium announce different versions, and the
                # override has to agree with the brand list of the browser
                # actually running -- which moves under us every time Chrome
                # updates itself.
                user_agent = _cached_user_agent(channel)
                config_path = _write_context_config(self._profile_dir, user_agent)
                args = [
                    "-s=" + self.session_name, "open",
                    "--browser=" + channel,
                    "--profile=" + self._profile_dir,
                    "--config=" + config_path,
                ]
                if self.mode == "headed":
                    args.append("--headed")
                # From here a browser may exist. See the gate above the `try`.
                launched = True
                rc, out, err = _run_pw(args, timeout=PLAYWRIGHT_OPEN_TIMEOUT)
                if rc == 0:
                    break
                if 0 <= rc < 256:
                    launched = False
                # EACH STREAM ON ITS OWN, NEVER CONCATENATED. `err + out` puts
                # the tail of one against the head of the other and can
                # manufacture the phrase across the seam: `...diagnostic:
                # distribution ` followed by `chrome is not found in page`
                # matches while NEITHER stream contains it, and the result is a
                # permanent silent demotion off an unrelated failure. Both
                # streams are still read, which is what the daemon's crash dump
                # needs -- which one carries it is not ours to guarantee.
                channel_missing = (_is_channel_missing(err, channel)
                                   or _is_channel_missing(out, channel))
                if channel != PLAYWRIGHT_FALLBACK_CHANNEL and channel_missing:
                    channel = PLAYWRIGHT_FALLBACK_CHANNEL
                    _config_set(CHANNEL_CONFIG_KEY, channel)
                    continue
                if channel_missing or (rc == -1 and err == "playwright-cli not found on PATH"):
                    raise _PlaywrightPrerequisiteError(
                        "browser_unavailable",
                        f"playwright-cli open failed: {err.strip() or out.strip()}")
                raise RuntimeError(
                    f"playwright-cli open failed: {err.strip() or out.strip()}")
        except BaseException:
            if not launched and claimed_here:
                _remove_context_config(self._profile_dir)
                _release_profile_dir(self._profile_dir, self._profile_fd)
                self._profile_dir = None
                self._profile_fd = None
            # With `launched` set we keep BOTH: the claim, because a live
            # browser may be holding the dir, and the config file with it. A
            # config file left behind is harmless -- the next claimant of that
            # slot overwrites it before it launches.
            #
            # `claimed_here` keeps them for a SECOND reason, and it is not a
            # duplicate of the first: on a REUSED claim the browser that may be
            # live came from an EARLIER invocation, so `launched` -- which only
            # ever describes this one -- cannot see it. See the claim site.
            raise

        # THE BROWSER IS LIVE AND THE CLAIM IS OURS. Record it BEFORE anything
        # else runs, and keep everything that follows OUTSIDE the handler above.
        #
        # Round 1 put the user-agent probe inside that try and caught `Exception`
        # around it. That was not enough, and round 2 said why: a
        # KeyboardInterrupt or SystemExit is not an `Exception`, so it reached
        # the handler, which released the profile dir out from under a running
        # browser -- and the SIGINT path could not clean up after itself either,
        # because `close()` returns early while `_opened` is False.
        #
        # WHAT HOLDS IS NOT "no path can release the claim". An earlier revision
        # of this comment claimed exactly that and it was never true: an
        # interrupt can land between `_run_pw` returning 0 and this assignment,
        # and a two-bytecode window cannot be closed in Python. What holds is
        # that the release is GATED ON POSITIVE EVIDENCE that no browser was
        # launched, so an interrupt in that residual window leaves the claim
        # HELD. (`launched` is one of the gate's TWO conjuncts, and the one this
        # window turns on; `claimed_here` is the other, and covers the case
        # `launched` structurally cannot see -- a browser from an earlier
        # invocation. Neither alone is the gate.) That is the safe direction:
        # the kernel releases a held lock at process exit. Premature release
        # can expose a profile still owned by a live browser to a new claimant.
        self._opened = True
        _remove_context_config(self._profile_dir)

        if user_agent is None:
            # COLD CACHE: this process launched with the browser's own UA, so it
            # announced `HeadlessChrome/...` for its whole life. That is a
            # one-time, bounded leak on a fresh machine (or after the cache
            # expires), accepted deliberately -- the alternative is closing and
            # reopening the browser mid-launch to apply an override we could
            # only have learned by opening it. Read it once, store the de-headed
            # form, and every later process on the box gets it from the cache.
            #
            # The containment stays, for a different reason than it had before:
            # an ORDINARY failure here (`_run_pw` raising PermissionError or any
            # other OSError -- it catches only TimeoutExpired and
            # FileNotFoundError) must not fail a fetch whose browser is open and
            # working. The UA is an optimisation; losing it costs one probe next
            # time. A BaseException still propagates, and now correctly: the
            # claim stays held and `close()` works, because `_opened` is set.
            try:
                probed = _dehead_user_agent(
                    _read_user_agent(self.session_name, PLAYWRIGHT_EVAL_TIMEOUT))
                if probed:
                    _store_user_agent(channel, probed)
            except Exception:
                pass

    def _eval_page_observation(self, deadline: float) -> tuple[str, str, list[dict]]:
        """Read HTML, URL and access UI in one task, including each settle poll."""
        rc, out, err = _run_pw(
            ["-s=" + self.session_name, "eval", _CDP_READ_PAGE_OBSERVATION],
            timeout=min(PLAYWRIGHT_EVAL_TIMEOUT, max(1, deadline - time.monotonic())),
        )
        if rc != 0:
            raise RuntimeError(f"eval failed: {err.strip() or out.strip()}")
        result = re.search(r"^### Result\r?\n(.*?)(?=^### |\Z)", out,
                           re.MULTILINE | re.DOTALL)
        if result is None:
            raise _CDPError("the page read returned no result")
        try:
            value = json.loads(result.group(1).strip())
        except (TypeError, ValueError) as e:
            raise _CDPError("the page read returned invalid JSON") from e
        return _parse_page_observation(value)

    def fetch_html(self, url: str, total_timeout: float) -> tuple[
            str, str, int | None, dict[str, str] | None, list[dict]]:
        """Open a tab, observe its final document, then close the tab.

        Returns (final_url, html, status, headers, access_gate_candidates).
        Status/headers are None when capture is unavailable or no longer paired
        with the observed document. Raises on transport or observation failure.
        """
        self._ensure_open()
        deadline = time.monotonic() + total_timeout
        # tab-new with URL returns before document is ready in some playwright-cli
        # builds; split into tab-new (empty) + goto (waits for load). D19.
        rc, out, err = _run_pw(
            ["-s=" + self.session_name, "tab-new"],
            timeout=min(PLAYWRIGHT_OPEN_TIMEOUT, max(1, deadline - time.monotonic())),
        )
        if rc != 0:
            raise RuntimeError(f"tab-new failed: {err.strip() or out.strip()}")
        nav_status = None
        nav_headers = None
        nav_url = None
        try:
            nav = _navigate_capture(
                self.session_name, url,
                timeout=min(PLAYWRIGHT_OPEN_TIMEOUT, max(1, deadline - time.monotonic())),
            )
            if nav is None:
                # run-code route unavailable -> pre-2026-08-29 path, verbatim.
                rc, out, err = _run_pw(
                    ["-s=" + self.session_name, "goto", url],
                    timeout=min(PLAYWRIGHT_OPEN_TIMEOUT, max(1, deadline - time.monotonic())),
                )
                if rc != 0:
                    raise RuntimeError(f"goto failed: {err.strip() or out.strip()}")
            elif not nav["ok"]:
                # Route worked, navigation failed. Do NOT retry with `goto`.
                raise RuntimeError(f"goto failed: {nav['error']}")
            else:
                nav_status = nav["status"]
                nav_headers = nav["headers"]
                nav_url = nav["url"]
            html, seen, candidates = self._eval_page_observation(deadline)
            final_url = seen or nav_url or url
            if nav_url and final_url != nav_url:
                nav_status = None
                nav_headers = None
            # A JS CHALLENGE CLEARS ITSELF AFTER `load`, and one read cannot see
            # that happen. `waitUntil:'load'` fires on the INTERSTITIAL -- a
            # Cloudflare managed challenge, or a Turnstile in non-interactive
            # mode, then swaps the real document in several seconds later -- so
            # a single eval returns the wall every time and the tier fails a
            # fetch that was seconds from succeeding. Re-read until the document
            # stops being positively a challenge.
            #
            # THE PRECONDITION IS THE WHOLE COST CONTROL. A page that is not a
            # challenge on its first read falls straight through: no sleep, no
            # second eval, no wall-clock at all. Only a document that IS one
            # pays.
            #
            # THE SAME PREDICATE THE GATE WILL USE. `is_real_content`, one layer
            # up, judges this document with `_challenge_reason`; deciding
            # "settled" by any other test would let the loop hand up a page the
            # gate then rejects, or give up on one it would have accepted.
            #
            # BOTH MODES, not headed only. The headless tier is the one expected
            # to satisfy a non-interactive challenge, and it cannot do that
            # without this wait; one code path is smaller than a mode-conditional
            # and costs an ordinary page nothing.
            #
            # AND `page_served` IS PART OF THAT PREDICATE, so it has to be the
            # gate's own value rather than a constant. tier_playwright computes
            # `status_captured = isinstance(nav_status, int)` and hands
            # is_real_content `status_is_real=status_captured`, which forwards
            # it as `page_served` -- but only PAST its own non-2xx return, so
            # the gate's effective value is "captured AND 2xx". Hard-coding
            # False here made the loop scan _CHALLENGE_MARKUP PLUS
            # _EMBEDDABLE_CHALLENGE_MARKUP on a document the gate scans with
            # _CHALLENGE_MARKUP alone, i.e. the two disagreed in exactly the
            # direction that costs a SUCCEEDED fetch its time. Measured on this
            # repo's own documented false positive -- a captured 200 article of
            # 32,399 extracted chars carrying one cf-turnstile mount on its
            # contact form -- the loop entered, burned the whole 12 s budget and
            # ~12 extra evals, and the gate then accepted the page anyway.
            page_served = isinstance(nav_status, int) and 200 <= nav_status < 300
            if _challenge_reason(html, self._extract(html, final_url).strip(),
                                 page_served=page_served):
                settle_until = min(deadline,
                                   time.monotonic() + CHALLENGE_SETTLE_BUDGET)
                while time.monotonic() < settle_until:
                    time.sleep(CHALLENGE_POLL_S)
                    # A FAILED POLL MUST NOT DESTROY THE DIAGNOSIS (R5).
                    # `_eval_page_observation` raises RuntimeError on rc != 0 and
                    # `_run_pw` reports a subprocess timeout as rc=-1, so ONE
                    # slow or flaky eval used to propagate out of fetch_html
                    # carrying the interstitial ALREADY IN HAND with it:
                    # tier_playwright's blanket `except` then returned empty
                    # html, status None and no headers, escalation_verdict
                    # answered CONTINUE at its empty-headers guard, and both
                    # the captcha class and the CDP ask were lost -- a failure
                    # carrying no actionable instruction, which is the defect
                    # R5 names. Losing the IMPROVEMENT a later read might have
                    # brought is acceptable; losing the evidence is not.
                    #
                    # `html` is therefore replaced only by a poll that
                    # completed BOTH halves -- the read and the judgement -- so
                    # what is kept is always the last document a read actually
                    # returned, and never a half-processed one.
                    try:
                        settled, settled_url, settled_candidates = self._eval_page_observation(deadline)
                        settled_url = settled_url or final_url
                        settled_page_served = page_served and settled_url == final_url
                        reason = _challenge_reason(
                            settled, self._extract(settled, settled_url).strip(),
                            page_served=settled_page_served)
                    except _CDPError:
                        # Invalid UI metadata is not evidence that the gate is absent.
                        raise
                    except Exception as e:
                        print(f"webfetch: challenge settle read failed for "
                              f"{self.session_name}: {str(e)[:200]}; keeping "
                              f"the last document read", file=sys.stderr)
                        break
                    html = settled
                    if settled_url != final_url:
                        nav_status = None
                        nav_headers = None
                    final_url = settled_url
                    candidates = settled_candidates
                    page_served = settled_page_served
                    if not reason:
                        # CLEARED. WHAT THE GATE IS TOLD ABOUT THE STATUS MUST
                        # BE WHAT THIS LOOP JUDGED THE SETTLED DOCUMENT BY --
                        # `page_served` -- or the two disagree about the same
                        # document, in the one direction that costs a fetch
                        # that had already succeeded.
                        #
                        # A CAPTURED 2xx STAYS. The challenge swapped the real
                        # document in under that same response; no second
                        # status was ever sent, so the origin's own 2xx is
                        # still the honest answer, and it is exactly what
                        # `page_served=True` meant when the poll above declared
                        # this document settled. Clearing it made
                        # tier_playwright compute status_captured=False, a
                        # placeholder eff_status=200 and status_is_real=False
                        # -- and status_is_real=False WITHDRAWS the
                        # embeddable-marker exemption, so `is_real_content`
                        # additionally scanned _EMBEDDABLE_CHALLENGE_MARKUP
                        # over a document this loop had just cleared with that
                        # scan switched off. MEASURED on a 4,918-char article
                        # carrying one contact-form `class="cf-turnstile"`
                        # mount behind a Turnstile interstitial served at 200:
                        # the loop waited out the challenge, got the article,
                        # and the gate then rejected it. The loop defeated
                        # itself on exactly the documents it exists to rescue.
                        #
                        # A CAPTURED NON-2xx GOES, and None is the honest value
                        # rather than a rescue: a 403 belongs to the
                        # interstitial the origin sent, not to the document now
                        # in the tab, and `is_real_content` rejects on status
                        # before it looks at anything else. None becomes
                        # tier_playwright's documented placeholder 200 with
                        # status_is_real=False, which scans BOTH marker tuples
                        # -- and that is the same page_served=False this loop
                        # used on every read of this document, so the two still
                        # agree. Nothing is re-armed that the captured status
                        # had exempted: on a non-2xx no exemption was ever
                        # granted.
                        #
                        # A status the capture route never read is already None
                        # and stays None -- page_served was False there too.
                        if not page_served:
                            nav_status = None
                            nav_headers = None
                        break
                # BUDGET EXHAUSTED -> the last read and the ORIGINAL status
                # stand, the tier fails on its own gate, and the verdict routes
                # the URL onward. That is the right end for an INTERACTIVE
                # captcha -- click-the-images -- which no amount of waiting
                # solves and which only the CDP ask can.
            return final_url, html, nav_status, nav_headers, candidates
        finally:
            _run_pw(["-s=" + self.session_name, "tab-close"], timeout=PLAYWRIGHT_CLOSE_TIMEOUT)

    def close(self) -> None:
        if not self._opened:
            return
        rc, _out, err = _run_pw(["-s=" + self.session_name, "close"],
                                timeout=PLAYWRIGHT_CLOSE_TIMEOUT)
        if rc != 0:
            # CLOSE FAILED -- e.g. it timed out while Chromium is still running
            # on our profile dir. Deleting the record and dropping the lock here
            # would erase every trace of ownership and let the next process
            # claim a directory that is still in use. Keep both. We stay
            # `_opened`, so a later close can retry, and if this process dies
            # the kernel drops the lock for us.
            print(f"webfetch: close failed for {self.session_name} "
                  f"(rc={rc}): {err.strip()[:200]}; keeping profile claim",
                  file=sys.stderr)
            return
        # `close` alone LEAKS a session record, permanently. Upstream's
        # `_stopDaemon` skips `deleteSessionConfig()` when `cli.persistent` is
        # set, and we launch persistent -- so without this, every fetch this
        # process ever ran leaves a `.session` file behind for good. 211 had
        # accumulated where this was found, and `list` cost grows with them.
        drc, _dout, derr = _run_pw(["-s=" + self.session_name, "delete-data"],
                                   timeout=PLAYWRIGHT_CLOSE_TIMEOUT)
        if drc != 0:
            # The session IS closed, so the profile dir is safe to hand back --
            # but say so, because an undeleted record accumulates and that is
            # how the original outage was built.
            print(f"webfetch: could not delete session record "
                  f"{self.session_name}: {derr.strip()[:200]}", file=sys.stderr)
        self._opened = False
        # D19: release our profile dir claim so other processes/sessions can use it.
        if self._profile_dir or self._profile_fd is not None:
            _release_profile_dir(self._profile_dir, self._profile_fd)
            self._profile_dir = None
            self._profile_fd = None


def _parse_eval_html(stdout: str) -> str:
    """Extract HTML from playwright-cli eval stdout.

    Output shape:
      ### Result
      "<<<WEBFETCH-START>>><html...>...</html><<<WEBFETCH-END>>>"
      ### Ran Playwright code
      ...
    """
    m = re.search(r"<<<WEBFETCH-START>>>(.*?)<<<WEBFETCH-END>>>", stdout, re.DOTALL)
    if m:
        body = m.group(1)
        # Re-wrap as a JSON string and parse to undo \\n, \\\", \\u escapes.
        try:
            return json.loads('"' + body + '"')
        except Exception:
            return body
    # Fallback: parse the line right after "### Result" as JSON.
    lines = stdout.splitlines()
    for i, line in enumerate(lines):
        if line.strip() == "### Result" and i + 1 < len(lines):
            for j in range(i + 1, len(lines)):
                if lines[j].startswith("### "):
                    break
                try:
                    return json.loads(lines[j])
                except Exception:
                    continue
    return ""


def _parse_final_url(stdout: str) -> str | None:
    m = re.search(r"^- Page URL:\s*(\S+)", stdout, re.MULTILINE)
    return m.group(1) if m else None


def _install_signal_handlers() -> None:
    global _SIGNAL_HANDLERS_INSTALLED
    if _SIGNAL_HANDLERS_INSTALLED:
        return
    _SIGNAL_HANDLERS_INSTALLED = True

    def _shutdown(*_args):
        for s in list(_SESSIONS.values()):
            try:
                s.close()
            except Exception:
                pass

    atexit.register(_shutdown)
    prev_sigint = signal.getsignal(signal.SIGINT)

    def _sigint(signum, frame):
        _shutdown()
        if callable(prev_sigint):
            prev_sigint(signum, frame)
        else:
            sys.exit(130)

    try:
        signal.signal(signal.SIGINT, _sigint)
    except (ValueError, OSError):
        pass
    if hasattr(signal, "SIGTERM"):
        try:
            signal.signal(signal.SIGTERM, lambda *a: (_shutdown(), sys.exit(143)))
        except (ValueError, OSError):
            pass


def get_session(mode: str) -> PlaywrightSession:
    if mode not in _SESSIONS:
        _SESSIONS[mode] = PlaywrightSession(mode)
        _install_signal_handlers()
    return _SESSIONS[mode]


def _run_pw_cleanup(args: list[str]) -> tuple[int, str, str]:
    """`_run_pw` for the CLEANUP loops, with an unspawnable command reported as
    a non-zero rc instead of an exception.

    FOR `reap_zombies` AND `startup_reap` ONLY. Never call this on the launch
    path. `_ensure_open` DEPENDS on an OSError propagating out of `_run_pw`:
    that is what makes a launch INDETERMINATE, which is what keeps `launched`
    True and holds the profile claim. Convert the raise there and the handler
    releases a claim under a possibly-live browser -- the defect `claimed_here`
    exists to prevent. Same helper, two callers, opposite consequences, which is
    why the conversion lives HERE and not inside `_run_pw`.

    THE CLEANUP LOOPS WANT THE OPPOSITE, and that is what makes this safe. Both
    already treat a non-zero rc as "this step did not happen": a failed `close`
    means the record is NOT deleted, because it may be the only evidence a live
    Chromium holds a dir, and a failed `delete-data` counts as an undeleted
    record, which is the original outage's precondition. An OSError means the
    step did not happen either -- we could not even spawn the command -- so
    routing it to the same rc != 0 branch gives both properties by construction
    rather than by four hand-written except blocks that can drift apart.

    rc is -2 rather than -1 to keep it distinguishable in a log from `_run_pw`'s
    own timeout/not-found rc; nothing branches on the value, only on != 0.
    """
    try:
        return _run_pw(args, timeout=PLAYWRIGHT_CLOSE_TIMEOUT)
    except OSError as e:
        return -2, "", f"could not run playwright-cli: {type(e).__name__}: {e}"


def reap_zombies() -> tuple[int, int, int] | None:
    """Close and delete the listed public webfetch-* session records.

    This explicit admin action includes live sessions; normal startup reaping
    requires a provably dead owner PID instead. It never adopts private session
    names or removes persistent Chromium profiles under PROFILE_BASE.

    Return (closed, deleted, problems), or None for an unreadable listing. Counts
    cover listed names only: a newline-boundary listing truncation is undetectable.
    Nonzero problems means incomplete cleanup, not success."""
    names = _list_sessions()
    if names is None:
        return None   # could not enumerate: say so, do not report a clean sweep
    closed = 0
    deleted = 0
    failed = 0
    undeleted = 0
    for name in names:
        rc, _out, _err = _run_pw_cleanup(["-s=" + name, "close"])
        if rc != 0:
            # Do NOT delete the record of a session we could not confirm closed:
            # the record may be the only evidence a live Chromium holds a dir.
            # An unspawnable `close` lands here too -- see `_run_pw_cleanup`.
            # "Could not confirm closed" is the same verdict whether the CLI
            # refused or never ran.
            failed += 1
            continue
        closed += 1
        rc, _out, _err = _run_pw_cleanup(["-s=" + name, "delete-data"])
        if rc == 0:
            deleted += 1
        else:
            # A record we could not delete is a record that keeps accumulating,
            # which is the ORIGINAL OUTAGE'S PRECONDITION. Silence here let the
            # command report a clean sweep while rebuilding the exact condition
            # it exists to clear. An unspawnable `delete-data` is undeleted too.
            undeleted += 1
    if failed:
        print(f"webfetch: {failed} session(s) could not be closed; their "
              f"records were left in place deliberately", file=sys.stderr)
    if undeleted:
        print(f"webfetch: {undeleted} session record(s) could not be deleted; "
              f"they will keep counting against the session list",
              file=sys.stderr)
    return closed, deleted, failed + undeleted


# Startup orphan reaper (D11; iter-2 HIGH 1 amended). Idempotent. Called by
# fetch.main() after open_db.
#
# v3 behavior (iter-2 HIGH 1): session names embed the owning Python PID
# (e.g. `webfetch-headless-1234`, `webfetch-cdp-1234-1`). The reaper parses
# the trailing PID from any `webfetch-*` session name; closes only sessions
# whose embedded PID is dead. Preserves THIS process's sessions and other
# concurrent processes' live sessions.
_STARTUP_REAPED = False

# Regex extracts the trailing -<digits> from `webfetch-<mode>-<pid>` or
# `webfetch-<mode>-<pid>-<counter>`. The owning Python PID is the FIRST
# numeric group after the mode name.
_SESSION_PID_RE = re.compile(r"^webfetch-[a-z]+-(\d+)(?:-\d+)?$")


def _session_owner_pid(name: str) -> int | None:
    m = _SESSION_PID_RE.match(name)
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


def startup_reap() -> int:
    """On-startup orphan reaper. Closes `webfetch-*` sessions whose embedded
    Python PID is provably dead, then deletes their records.

    CONCURRENCY CAVEAT, stated because the previous wording ("only orphans go")
    claimed a guarantee this cannot make. Session names embed the owner pid, so
    a REUSED pid produces a colliding name: between judging a pid dead and
    acting on it, the OS can hand that pid to a new peer whose session is then
    indistinguishable from the orphan. The pid is re-probed immediately before
    the close to keep the window small, and a non-matching name is skipped
    entirely, but the residual is real. Closing it would mean putting a unique
    token in every session name -- a migration of the naming scheme that
    `_SESSION_PID_RE`, this function and every on-disk session record depend on,
    judged a larger risk than the residual it removes.
    """
    global _STARTUP_REAPED
    if _STARTUP_REAPED:
        return 0
    _STARTUP_REAPED = True
    killed = 0
    my_pid = os.getpid()
    names = _list_sessions()
    if names is None:
        return 0   # cannot enumerate: reap nothing rather than guess
    for name in names:
        if not name.startswith(PLAYWRIGHT_SESSION_PREFIX):
            continue
        owner_pid = _session_owner_pid(name)
        if owner_pid is None:
            # Doesn't match the per-PID schema (legacy session from older
            # tiers.py, or hand-created session). Leave alone; conservative.
            continue
        if owner_pid == my_pid:
            continue
        if _pid_alive(owner_pid):
            continue
        # Re-check immediately before acting. The probe above and the close
        # below are separated by other work, and a pid can be REUSED in that
        # window -- the reused pid produces the same session name, so a stale
        # verdict would delete a live peer's record. This narrows the window; it
        # does not close it. See the note below.
        if _pid_state(owner_pid) != "dead":
            continue
        rc, _out, _err = _run_pw_cleanup(["-s=" + name, "close"])
        if rc != 0:
            continue   # unconfirmed close: leave the record alone. An
                       # unspawnable `close` is unconfirmed too -- see
                       # `_run_pw_cleanup` for why the catch is not in `_run_pw`.
        # Delete the record too, else the orphan is closed but still counted
        # by every future `list` -- see PlaywrightSession.close.
        drc, _dout, _derr = _run_pw_cleanup(["-s=" + name, "delete-data"])
        if drc == 0:
            killed += 1
    return killed


# ---- Tier 1/2: Playwright ----------------------------------------------------

def _extracted_markdown(session: object, html: str, url: str) -> str:
    """Markdown for `html`, reusing `fetch_html`'s own extraction of it.

    `getattr` rather than an attribute access, and a plain call rather than
    `session._extract`: the session here is whatever `get_session` returned,
    and several tests replace it with a double that has neither. A missing memo
    is a cache miss, never an error.
    """
    memo = getattr(session, "_extract_memo", None)
    if memo is not None:
        try:
            m_html, m_url, m_md = memo
        except (TypeError, ValueError):
            return html_to_markdown(html, url)
        if m_html is html and m_url == url:
            return m_md
    return html_to_markdown(html, url)


def tier_playwright(url: str, mode: str, *, total_timeout: float = 30.0,
                    allow_private: bool = False) -> TierResult:
    """Fetch HTML via long-lived playwright session, then extract markdown."""
    t0 = time.monotonic()
    tier_name = "headless" if mode == "headless" else "headed"
    try:
        validate_url_safety(url, allow_private=allow_private)
    except UnsafeURL as e:
        return TierResult(ok=False, tier=tier_name, error=f"unsafe URL: {e}",
                          failure_class="other", elapsed_ms=int((time.monotonic() - t0) * 1000))
    try:
        session = get_session(mode)
        final_url, html, nav_status, nav_headers, candidates = session.fetch_html(
            url, total_timeout=total_timeout)
    except _PlaywrightPrerequisiteError as e:
        return TierResult(ok=False, tier=tier_name, error=str(e),
                          error_code=e.error_code, failure_class="other",
                          elapsed_ms=int((time.monotonic() - t0) * 1000))
    except Exception as e:
        return TierResult(
            ok=False, tier=tier_name, error=str(e),
            failure_class=classify_failure(None, "", type(e).__name__),
            elapsed_ms=int((time.monotonic() - t0) * 1000),
        )
    # NOT a second html_to_markdown call. `fetch_html` already extracted this
    # exact document to evaluate its settle precondition, and trafilatura is the
    # most expensive step on this tier -- paying for it twice on every fetch
    # spends the fetch deadline itself, which is an R1 cost. `_extracted_markdown`
    # returns that extraction when the session kept one for this document and
    # this url, and runs a real extraction otherwise (a test double with no memo,
    # or a `final_url` the origin redirected to, which is a different call).
    md = _extracted_markdown(session, html, final_url)
    status_captured = isinstance(nav_status, int)
    # The 200 on the fallback path is a PLACEHOLDER for a status we never read,
    # not a claim the page returned one -- _navigate_capture returns None
    # whenever the run-code route is unavailable, and a missing capability must
    # not turn a working fetch into a failed one, so the non-2xx branch stays
    # out of the way. But it buys nothing further: status_is_real=False
    # withholds the embeddable-marker exemption, which is earned by a 2xx the
    # ORIGIN sent and not by one we supplied. On the captured path the status is
    # the origin's own and the exemption stands.
    eff_status = nav_status if status_captured else 200
    ok, reason = is_real_content(md, html, eff_status, nav_headers,
                                 status_is_real=status_captured)
    # Whole body, never a slice -- see classify_failure's contract. The status
    # is None wherever the 200 is ours, which is the same judgement the line
    # above makes.
    #
    # THE TWO JUDGES NOW DISAGREE ABOUT THIS DOCUMENT, DELIBERATELY. The
    # sentence that used to sit here said they could not, and the change is
    # exactly the repeal of it. is_real_content is handed status_is_real=False
    # and scans BOTH marker tuples, so it still REJECTS a cf-turnstile mount;
    # classify_failure is handed status_unobserved=True and scans only
    # _CHALLENGE_MARKUP, so it no longer calls that mount a challenge.
    #
    # THE SPLIT IS THE POINT, because the two answers are not paid for out of
    # the same pocket. A VERDICT costs THIS FETCH, which then climbs to the next
    # rung. A CLASS costs the whole DOMAIN its tier 0 -- the only tier that reads
    # a PDF or returns a JSON body verbatim -- for every later fetch. So evidence
    # too thin to spend the second on can still be worth spending the first on,
    # and a marker a SITE may mount on its own contact form is exactly that thin.
    #
    # MEASURED on this tier's own turnstile fixture: the same document returns
    # "other" at an observed 200 and returned "cloudflare_challenge" when the
    # status could not be read, so the class turned on OUR capture rather than on
    # anything the origin did.
    fc = "" if ok else classify_failure(eff_status if status_captured else None,
                                        html, None, extracted=md,
                                        status_unobserved=not status_captured)
    redirect_chain = [final_url] if final_url and final_url != url else []
    # EMPTY WHEN THE CAPTURE ROUTE WAS UNAVAILABLE, never invented. `eff_status`
    # above is already a documented PLACEHOLDER 200 on that path; a fabricated
    # header dict beside it would let the escalation gate open a window on
    # evidence nobody collected. Missing evidence must read as missing.
    captured_headers = dict(nav_headers) if nav_headers else {}
    # Playwright's `allHeaders()` FLATTENS duplicate Set-Cookie into one
    # newline-joined value, so splitting it back is the most that can be
    # recovered here -- there is no `.get_all()` on the far side of the CLI.
    # That is a precision loss and not a safety one: a cookie we fail to see can
    # only make the gate's VENDOR_PRESENT precondition false, i.e. no window.
    # Case-insensitive because the key's casing crosses a JSON boundary, and
    # isinstance for the same reason -- `_navigate_capture` type-guards the
    # status and the dict itself on the way in but not the values inside it.
    _sc = next((v for k, v in captured_headers.items()
                if k.lower() == "set-cookie" and isinstance(v, str)), "")
    set_cookie = [line for line in _sc.split("\n") if line.strip()]
    return TierResult(
        ok=ok, tier=tier_name, content=md if ok else "", html=html,
        status=eff_status, status_is_real=status_captured,
        bytes_=len(html.encode("utf-8", "replace")),
        error="" if ok else reason, failure_class=fc, redirect_chain=redirect_chain,
        final_url=final_url, access_gate_candidates=candidates,
        # `hops` stays empty: the capture route reports the FINAL response only,
        # so the chain that reached it is not observable from this tier.
        headers=captured_headers, set_cookie=set_cookie,
        elapsed_ms=int((time.monotonic() - t0) * 1000),
    )


# ---- Tier 3: Copilot ---------------------------------------------------------

def tier_copilot(url: str, *, search_path: str, model: str = DEFAULT_COPILOT_MODEL,
                 total_timeout: float = 60.0, allow_private: bool = False) -> TierResult:
    """Refuse the disabled synthesis tier before URL checks or subprocesses."""
    return TierResult(ok=False, tier="copilot", error=state.COPILOT_DISABLED_REASON,
                      error_code="disabled", failure_class="other", extractor="-")



# ---- Tier 4: CDP, spoken directly over the DevTools protocol -----------------
#
# THE TRANSPORT IS A WEBSOCKET AND THERE IS NO `playwright-cli` IN THIS TIER
# ANY MORE. That is the whole of the 2026-09-04 rebuild, and it is worth stating
# what it bought, because each line was a separate measured defect that is now
# structurally absent rather than repaired:
#
#   - `tab-select` reached `Page.bringToFront` and RAISED THE USER'S WINDOW.
#     There is no such verb here.
#   - `open --config=` ran `page.goto('about:blank')` against `contexts()[0]`,
#     which under `isolated:false` is the operator's own context -- it DESTROYED
#     whatever page was in front of them. We never touch the default context.
#   - the URL went through a `.cmd` shim to `cmd.exe`, which splits on `&`; seven
#     fetches died that way in this box's own `fetch_log`. There is no shell.
#   - `cdp-config.json` lived on a `.gitignore`d path, so it shipped to no box.
#     No config file is needed.
#   - a tab was identified by its INDEX into a list shared with every other
#     client of that browser. Closing any earlier tab splices the list, a later
#     tab inherits our index, and every index check still passes: measured, one
#     agent received another's page, reported ok, and closed the other's tab.
#     Ownership is now the `targetId` the browser itself returns.
#   - one fetch cost 12 subprocess spawns, each cmd.exe -> node, measured at
#     ~14 s against ~437 ms for the same pages over raw CDP.
#
# WHY THE DEFAULT CONTEXT, AND WHY THE ID IS THE OWNERSHIP. This first shipped
# creating the target in a DISPOSABLE browser context, reasoning that a context
# we made cannot reach a page the owner made. Review round 1 called that
# critical and the measurement agreed: a disposable context is off-the-record
# and inherits NO cookies. Three arms, headless Chrome, cookie set in the
# default context -- the default context sees it, a second default-context
# target sees it, the disposable context does not. For a tier entered ONLY on a
# NEEDS_LOGIN verdict that is fatal: every such page opens logged out, and the
# tier hands back the login page it was summoned to get past.
#
# The safety that context was bought for is already paid for by the ID. Teardown
# closes exactly the `targetId` the browser returned, and an id names one target
# for that target's life -- unlike an index, which a closing tab reassigns to
# somebody else's page. Ownership was never the context's job.
#
# What the context DID genuinely provide was cover against Chrome exiting when
# its last page closes, which has bitten this project before. That is now an
# explicit check in `close()` rather than a side effect of the structure.
#
# WHY AN ORDINARY TARGET AND NOT A HIDDEN ONE. Hidden targets never paint: 1
# animation frame in 4 s against 241 for an ordinary one, no paint event ever,
# observers silent -- while `visibilityState` still reads `visible` and the
# serialized DOM still looks plausible, so the failure returns EMPTY rather than
# erroring. That is the shape this project calls the worst available, and it is
# what killed the previous version of this design. Measured 2026-08-30 against
# real Chrome 151, paired with an ordinary-target control in every arm.
#
# `background: True` asks Chrome not to foreground the new tab. Measured, an
# ordinary target in a RESTORED window takes no focus (3/3); the one arm that
# steals focus is a MINIMIZED window, which un-minimizes (2/2) -- which is the
# independent reason this project's standing rule is never to minimize the
# escalation browser.

CDP_PORT = 9222
CDP_HTTP_TIMEOUT = 5.0
# Cap on one `Runtime.evaluate` result. The default in `websockets` is 1 MiB and
# a real page's serialized DOM exceeds that routinely, so it must be raised or
# the transport tears the connection down mid-read on ordinary content.
CDP_MAX_FRAME = 64 * 1024 * 1024
# Teardown's OWN budget, independent of the fetch deadline -- see `close()`.
CDP_TEARDOWN_BUDGET = 5.0
# What `navigate()`'s load wait holds back for the work that FOLLOWS it: the
# combined DOM-and-location read and the `Page.getFrameTree` that pairs the
# status with the document -- TWO round trips, having been three until the two
# reads were merged into one to stop a navigation landing between them. Named
# because two places must agree on it -- the wait may not START inside it, and no
# single read inside the wait may ASK FOR time that reaches into it. They were
# one bare literal and one omission, which is the whole of
# `M-CDP-NOLOAD-BUDGET`. ASK FOR, not guarantee: a timeout bounds the request and
# not the socket's actual return, so a read that comes back late enough spends
# the reserve anyway -- see the load wait, where that residual is stated and
# why it still ships. Two loopback round trips are milliseconds; 0.5s is
# already generous against punctual returns, which is what makes the residual
# a tail rather than the common case.
CDP_POST_LOAD_RESERVE = 0.5

# GUARDED IMPORT, and the tier degrades rather than the process dying -- the
# same shape `pypdf` uses above and for the same reason: `websockets` is a
# tier-2 entry in deploy/dependencies/manifest.json, and tier 2 is declared a
# WARNING rather than a fatal miss, so a correctly-provisioned box can still be
# without it. A missing package must cost this one tier, never the fetch.
try:
    from websockets.sync.client import connect as _ws_connect
except ImportError:  # pragma: no cover - covered by the stubbed-absence test
    _ws_connect = None


class _CDPError(Exception):
    """Any failure of the raw-CDP transport, reported as a tier failure.

    A private exception type rather than reusing OSError, because the tier has
    to tell "the browser answered and said no" apart from "the browser is not
    there" -- and because `tier_cdp`'s boundary handler converts anything that
    escapes into a failed tier, so a type that says where it came from is worth
    more in the error string than a generic one.
    """


# Both browser transports use one synchronous DOM task. The array is serialized
# by the transport, not by a page-owned JSON.stringify/toJSON hook. Candidates
# are observations of modal/form UI only, never instructions or a wall verdict.
# Keep the shared expression single-line and free of inner double quotes or
# shell boolean operators: Playwright's Windows entrypoint is a .cmd shim.
_CDP_READ_PAGE_OBSERVATION = (
    "(() => { "
    "const visible = (element) => { "
    "const rects = Array.from(element.getClientRects()); "
    "if (!rects.some(rect => rect.width > 0 ? rect.height > 0 : false)) return false; "
    "const visibility = window.getComputedStyle(element).visibility; "
    "if (['hidden','collapse'].includes(visibility)) return false; "
    "for (let node = element; node; node = node.parentElement) { "
    "const style = window.getComputedStyle(node); "
    "if (style.display === 'none') return false; "
    "if (style.contentVisibility === 'hidden') return false; "
    "if (Number(style.opacity) === 0) return false; "
    "} return true; }; "
    "const candidates = Array.from(document.querySelectorAll("
    "'dialog,[role=dialog],[role=alertdialog],[aria-modal=true],form'"
    "), element => ({ "
    "visible: visible(element), "
    "text: typeof element.innerText === 'string' ? element.innerText : '', "
    "controls: Array.from(element.querySelectorAll("
    "'button,a,input[type=button],input[type=submit],input[type=reset],[role=button]'"
    ")).filter(visible).map(control => { "
    "const text = typeof control.innerText === 'string' ? control.innerText.trim() : ''; "
    "if (text) return text; "
    "const label = control.getAttribute('aria-label'); "
    "if (label) return label; "
    "if (control.tagName === 'INPUT') return control.value; "
    "return ''; }) })); "
    "return [document.documentElement.outerHTML, window.location.href, candidates]; "
    "})()"
)


def _parse_page_observation(value: object) -> tuple[str, str, list[dict]]:
    """Require the complete current-document observation; never discard bad UI data."""
    if (not isinstance(value, list) or len(value) != 3
            or not isinstance(value[0], str) or not isinstance(value[1], str)
            or not isinstance(value[2], list)):
        raise _CDPError("the page read returned an unexpected shape")
    for candidate in value[2]:
        if (not isinstance(candidate, dict)
                or set(candidate) != {"visible", "text", "controls"}
                or not isinstance(candidate["visible"], bool)
                or not isinstance(candidate["text"], str)
                or not isinstance(candidate["controls"], list)
                or not all(isinstance(control, str) for control in candidate["controls"])):
            raise _CDPError("the page read returned invalid access-gate candidates")
    return value[0], value[1], value[2]


def _cdp_browser_ws_url(port: int, timeout: float) -> str:
    """Discover the browser-level websocket over PLAIN HTTP.

    `/json/version` is one of the few CDP endpoints with an HTTP form, and it is
    the documented way in. It is deliberately NOT `/json/list`: that enumerates
    the user's open tabs, which we neither need nor want to read.

    `/json/activate` is the HTTP analogue of the `tab-select` this rebuild
    removed -- it foregrounds a tab. Do not reach for it.
    """
    url = "http://127.0.0.1:%d/json/version" % port
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8", "replace"))
    except Exception as e:
        raise _CDPError("no DevTools endpoint on :%d (%s)" % (port, e)) from e
    ws_url = payload.get("webSocketDebuggerUrl")
    if not isinstance(ws_url, str) or not ws_url:
        raise _CDPError("DevTools on :%d returned no webSocketDebuggerUrl" % port)
    return ws_url


class _CDPSession:
    """One websocket, one target in the owner's own context -- owned by id.

    NOT A GENERAL CDP CLIENT, and deliberately not: it speaks the methods this
    tier needs and nothing else. Five commands must never be sent to a browser
    shared with other clients, because each one breaks every other client on it
    -- `Browser.close`, auto-attach with wait-for-debugger, `Fetch.enable`,
    target discovery (`Target.setDiscoverTargets`, the SUBSCRIPTION -- not the
    one-shot `Target.getTargets`), and Playwright's connect-over-CDP.

    WHAT ENFORCES THAT IS THE CALL SITES, NOT THE CLASS. `call()` takes any
    method string and puts it on the wire, so it IS a generic passthrough; an
    earlier version of this docstring claimed the opposite and review round 1
    was right to call that out. The guarantee is therefore a property of the
    fixed, private set of callers above -- each one is in this file and none
    sends a forbidden verb -- and `tests/test_cdp_transport.py` asserts it over
    the methods actually sent. A caller added later must re-earn it.

    why-not an allowlist inside `call()`: nothing today can send one, this class
    is private, and every call site is in this file. A guard for a caller that
    does not exist is machinery for a hypothetical, and the false sentence was
    the whole defect.
    """

    def __init__(self, ws_url: str, deadline: float) -> None:
        if _ws_connect is None:  # pragma: no cover - guarded by the caller
            raise _CDPError("the `websockets` package is not installed")
        self._deadline = deadline
        self._next_id = 0
        self._target_id: str | None = None
        self._session_id: str | None = None
        # Document responses that arrived before this navigation's identity was
        # known, as (loaderId, frameId, status). See `_on_event`.
        #
        # PER-INSTANCE ON PURPOSE, AND THE REASON IS OWNERSHIP RATHER THAN A
        # CURRENT LEAK. The neighbouring per-navigation state
        # (`_document_status`, `_loaded`, `_frame_id`, `_loader_id`) is declared
        # at CLASS level, which is safe only because all four are immutable
        # scalars -- assigning to `self.x` rebinds on the instance. A list
        # written in that same visible idiom would be genuinely shared, because
        # `append` MUTATES rather than rebinds, so entries from every
        # `_CDPSession` in the process would accumulate in one list.
        #
        # It would NOT, in today's flow, let one fetch resolve another's status:
        # `navigate()` assigns `self._pending_docs = []` before `Page.navigate`,
        # and that assignment rebinds on the instance, so a navigation's own
        # buffer is instance-owned by the time any response is collected.
        # MEASURED against both idioms directly -- an earlier version of this
        # comment claimed that leak and a review round was spent correcting it.
        #
        # So the case for putting it here is that the safety above depends on
        # reset SEQUENCING: it holds only while the reset runs before responses
        # arrive. A caller that buffered before `navigate()`, or a change that
        # moved the reset, would start sharing state with nothing to show it.
        self._pending_docs: list[tuple[str | None, str | None, int]] = []
        # `proxy=None` is NOT tidiness. The default is `True`, which honours the
        # environment's proxy settings -- and a loopback DevTools socket routed
        # through a corporate proxy fails in a way that reads as "the browser is
        # not there". `ping_interval=None` because these connections live for one
        # fetch; keepalive traffic on them buys nothing and adds frames to read.
        try:
            self._ws = _ws_connect(
                ws_url, max_size=CDP_MAX_FRAME, open_timeout=self._left(),
                ping_interval=None, proxy=None,
            )
        except Exception as e:
            raise _CDPError("cannot open the DevTools websocket (%s)" % e) from e

    def _left(self) -> float:
        """Left."""
        return max(0.1, self._deadline - time.monotonic())

    def call(self, method: str, params: dict | None = None,
             *, on_session: bool = False) -> dict:
        """Send one command and return its result, discarding events.

        Events and command replies share the socket, so a reply is identified by
        its `id` and everything else is either buffered (when a caller asked for
        it) or dropped. A command that errors raises rather than returning a
        result nobody checked.
        """
        self._next_id += 1
        msg_id = self._next_id
        msg: dict = {"id": msg_id, "method": method, "params": params or {}}
        if on_session:
            if self._session_id is None:
                raise _CDPError("%s needs a target session and none is attached" % method)
            msg["sessionId"] = self._session_id
        try:
            self._ws.send(json.dumps(msg))
        except Exception as e:
            raise _CDPError("%s could not be sent (%s)" % (method, e)) from e
        while True:
            # THE DEADLINE IS CHECKED HERE, NOT ONLY IN THE RECV TIMEOUT. `_left`
            # floors at 0.1s so a blocking call always gets a usable timeout --
            # which means that once the budget is spent it hands out 0.1s
            # forever. A page emitting a continuous `Network.*` stream (ads,
            # video, long-polling: not exotic) then satisfies every recv inside
            # that floor and this loop never exits. Found by re-reading against
            # the review brief's own question about busy-spinning.
            if time.monotonic() >= self._deadline:
                raise _CDPError("%s exceeded the fetch budget" % method)
            frame = self._recv()
            if frame.get("id") != msg_id:
                self._on_event(frame)
                continue
            if "error" in frame:
                err = frame["error"] or {}
                raise _CDPError("%s failed: %s" % (method, err.get("message", err)))
            return frame.get("result") or {}

    def _recv(self, timeout: float | None = None) -> dict:
        """Read one frame. `timeout` BOUNDS WHAT THIS READ MAY SPEND.

        The default is the whole remaining budget, which is right for `call()`:
        it is waiting for a reply it cannot proceed without, so there is nothing
        to hold back for. It is wrong for a caller that has already reserved part
        of the budget for work of its own -- `navigate()`'s load wait -- because
        the reserve is then spendable by the very read it was set aside from.

        `None` here means "use the budget", never "block forever": the socket's
        own `None` DOES block forever, and it is never reached from this method.
        A zero or negative `timeout` needs no clamp -- `websockets` documents it
        as a non-blocking poll that raises `TimeoutError` when nothing is already
        buffered, which is the right floor and is what a caller asking for a
        window that has closed should get.
        """
        try:
            raw = self._ws.recv(
                timeout=self._left() if timeout is None else timeout)
        except TimeoutError as e:
            raise _CDPError("the browser did not answer within the fetch budget") from e
        except Exception as e:
            raise _CDPError("the DevTools connection failed (%s)" % e) from e
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", "replace")
        try:
            frame = json.loads(raw)
        except ValueError as e:
            raise _CDPError("the browser sent a frame that is not JSON") from e
        if not isinstance(frame, dict):
            raise _CDPError("the browser sent a frame that is not an object")
        return frame

    # Events worth keeping while we wait for OUR page to load.
    #
    # CORRELATION IS THE POINT, and getting it wrong was critical in review
    # round 1. `type == "Document"` does NOT mean "the page we asked for":
    # EVERY IFRAME IS ALSO A DOCUMENT. Iframes load after the main frame, so a
    # "last one wins" rule does not pick the main document -- on any page with
    # an iframe it reliably picks the IFRAME. A main 404 carrying a 200 iframe
    # would have reached the content gate, the classifier and the caller as 200.
    #
    # A response counts only when it belongs to the navigation `Page.navigate`
    # told us about, matched on `loaderId` (unique to that navigation) and
    # falling back to `frameId`. Redirects share a loaderId, so "last matching
    # wins" is still right WITHIN our navigation and now means the hop we
    # actually landed on.
    #
    # `Page.loadEventFired` carries no frame, so it cannot be correlated at all
    # and a late one from the initial `about:blank` could end the wait before our
    # page had loaded. `Page.frameStoppedLoading` carries `frameId`, so the wait
    # keys on that instead.
    def _on_event(self, frame: dict) -> None:
        method = frame.get("method")
        params = frame.get("params") or {}
        if method == "Network.responseReceived" and params.get("type") == "Document":
            status = (params.get("response") or {}).get("status")
            if not self._loader_id and not self._frame_id:
                # THE IDENTITY IS NOT YET KNOWN, WHICH IS NOT THE SAME AS THE
                # RESPONSE NOT BEING OURS -- and conflating the two is what made
                # this tier judge every page on a manufactured 200.
                #
                # MEASURED against Chrome 151 over ten navigations -- two fast
                # 200s, two real 404s, a 403, a large page, TWO IFRAME-BEARING
                # pages and a repeat navigation on one session:
                # the main document's response arrives one wire frame BEFORE
                # `Page.navigate`'s reply, every time, because that reply is sent
                # at navigation COMMIT and the response necessarily precedes
                # commit. The reply is the only place we learn `loaderId`, so
                # judging here reaches `_is_our_navigation`'s fail-closed tail
                # with nothing to compare and discards a status whose loaderId
                # then turns out byte-identical to the one we are handed next.
                #
                # So DEFER rather than decide: keep the ids and resolve in
                # `_resolve_pending` once we have our own. The comparison itself
                # is unchanged, which is the point -- the iframe guarantee below
                # is inherited rather than re-argued.
                #
                # WHAT CLOSES THIS WINDOW is `Page.navigate`'s reply setting at
                # least one of the two ids, and that is an ASSUMPTION worth
                # naming rather than guarding: `frameId` is a required field of
                # that reply, and both were present on every measured arm. If a
                # browser ever returned neither, this would keep buffering for
                # the rest of the pump loop -- costing memory bounded by the
                # fetch deadline, and no wrong answer, because a navigation we
                # cannot identify is one whose status we must not claim.
                if isinstance(status, int):
                    self._pending_docs.append(
                        (params.get("loaderId"), params.get("frameId"), status))
                return
            # A SAME-FRAME RENAVIGATION IS STILL OURS, and rejecting it is how a
            # STALE status gets reported as the final one -- review round 4's
            # critical. `_is_our_navigation`'s first clause fires on the loaderId
            # and RETURNS, so a new loader on our own frame never reaches the
            # frameId fallback. Meanwhile `Page.frameStoppedLoading` below
            # accepts by frameId, so the wait ends on that second navigation and
            # `_tier_cdp_impl` evaluates the NEW document. Without this branch the
            # caller gets the new document's content and URL carrying the old
            # document's status -- and, now that the capture actually works,
            # carrying `status_is_real=True`.
            #
            # Real pages do this: a meta refresh, a JS `location =`, an SSO
            # bounce. This tier is entered on sign-in walls, so it meets them.
            #
            # AN IFRAME CANNOT ENTER HERE. It carries a DIFFERENT frameId, so it
            # falls through to the unchanged predicate below and is rejected
            # exactly as before -- the anti-iframe property is keyed on the frame
            # identity the browser gave us for our own navigation.
            ev_frame = params.get("frameId")
            ev_loader = params.get("loaderId")
            if (self._frame_id and ev_frame == self._frame_id
                    and ev_loader and ev_loader != self._loader_id):
                self._loader_id = ev_loader
                if isinstance(status, int):
                    self._document_status = status
                return
            if not self._is_our_navigation(params):
                return
            if isinstance(status, int):
                self._document_status = status
        elif method == "Page.frameStoppedLoading":
            if self._frame_id and params.get("frameId") == self._frame_id:
                self._loaded = True

    def _resolve_pending(self) -> None:
        """Judge the responses that arrived before we knew which navigation was ours.

        Called once, immediately after `Page.navigate`'s reply supplies the ids.
        Uses `_is_our_navigation` UNCHANGED, so a buffered response is held to
        exactly the same standard as a live one and the anti-iframe property is
        inherited rather than restated. A response that matches nothing is
        dropped, which leaves the honest manufactured 200 -- deferring the
        comparison must never turn "I could not tell" into "I will take it".

        LAST MATCHING WINS, which is the shipped rule: redirects share a
        loaderId, so the final hop is the one we landed on.
        """
        pending, self._pending_docs = self._pending_docs, []
        for loader_id, frame_id, status in pending:
            if self._is_our_navigation({"loaderId": loader_id,
                                        "frameId": frame_id}):
                self._document_status = status

    def _is_our_navigation(self, params: dict) -> bool:
        """Does this event belong to the navigation we started?

        FAILS CLOSED: with no frame or loader to compare against, we cannot tell
        our main document from an iframe, so nothing counts. A missing status
        costs one manufactured 200 that is honestly labelled
        `status_is_real=False`; a WRONG status is read as the origin's own.
        """
        if self._loader_id and params.get("loaderId"):
            return params["loaderId"] == self._loader_id
        if self._frame_id and params.get("frameId"):
            return params["frameId"] == self._frame_id
        return False

    _document_status: int | None = None
    _loaded: bool = False
    _frame_id: str | None = None
    _loader_id: str | None = None

    def open_target(self) -> None:
        """Create one ordinary target IN THE DEFAULT CONTEXT, owned by its id.

        NOT a disposable browser context. That was critical finding #1 in review
        round 1, and it was MEASURED before being accepted, because a reviewer is
        a second model rather than an authority. Three arms, headless Chrome,
        cookie set in the default context: the default context sees it, a second
        default-context target sees it, THE DISPOSABLE CONTEXT DOES NOT. A
        disposable context is off-the-record and inherits no cookies or storage.

        That is fatal for THIS tier in particular. It is entered only on a
        NEEDS_LOGIN verdict and its whole reason to exist is that the dedicated
        profile is logged in. In a disposable context every such page would open
        LOGGED OUT, so the tier would fetch the login page it was summoned to get
        past and hand it back as content.

        THE SAFETY THAT CONTEXT WAS BOUGHT FOR IS ALREADY PAID FOR BY THE ID.
        Teardown closes exactly the `targetId` the browser returned. An id names
        one target for that target's life and names nothing else -- unlike an
        index, which a closing tab reassigns to somebody else's page. Ownership
        was never the context's job; the context added isolation, which was
        mistaken for safety, and isolation is what broke the tier.
        """
        res = self.call("Target.createTarget", {
            "url": "about:blank",
            "background": True,
        })
        self._target_id = res.get("targetId")
        if not self._target_id:
            raise _CDPError("the browser created no target")
        # `flatten: True` multiplexes the target's session onto this same
        # socket, keyed by sessionId. The alternative -- nested
        # `Target.sendMessageToTarget` -- is deprecated and its replies are
        # harder to correlate.
        res = self.call("Target.attachToTarget",
                        {"targetId": self._target_id, "flatten": True})
        self._session_id = res.get("sessionId")
        if not self._session_id:
            raise _CDPError("could not attach to our own target")

    def _is_last_page(self) -> bool:
        """Would closing our target leave the browser with no pages at all?

        `Target.getTargets` is a ONE-SHOT QUERY and is NOT the forbidden "target
        discovery" -- that is `Target.setDiscoverTargets`, a SUBSCRIPTION that
        makes the browser stream target lifecycle events to every client, which
        is what breaks the others. This changes no browser state and no other
        client can observe it.

        Fails toward NOT-last on any doubt, because the costs are asymmetric: a
        wrong "not last" strands one empty tab, a wrong "last" would have closed
        the owner's browser.
        """
        try:
            targets = self.call("Target.getTargets").get("targetInfos") or []
        except _CDPError:
            return False
        return len([t for t in targets if t.get("type") == "page"]) <= 1

    def navigate(self, url: str) -> int | None:
        """Navigate and wait for load. Returns the real status, or None.

        `Network.enable` is scoped to OUR session and therefore to our target,
        so it observes our own request and nothing of the user's. It is not
        `Fetch.enable`, which is browser-wide interception and is on the
        never-send list.
        """
        self.call("Page.enable", on_session=True)
        self.call("Network.enable", on_session=True)
        # RESET BEFORE NAVIGATING, never after: the target begins life at
        # about:blank, which is itself a document that loads and stops loading,
        # so anything observed before this point belongs to a page nobody asked
        # for.
        #
        # THE IDENTITY IS RESET HERE TOO, and leaving it out was a SECOND and
        # separate way to lose the status. Measured 2026-09-06: on a second
        # `navigate()` over one session, the response arrived carrying its own
        # loaderId while these fields still held the FIRST navigation's, so the
        # comparison ran, genuinely mismatched, and dropped a status that was
        # perfectly observable. `_tier_cdp_impl` builds a fresh session per fetch
        # so nothing reaches this today -- but the gate below is written as
        # "identity not yet known", and stale identity is not unknown identity.
        #
        # It must be HERE and not earlier: `Page.enable`/`Network.enable` above
        # also pump events, and a stale `about:blank` frame can arrive during
        # them -- clearing after those calls is what makes the buffer start empty
        # for the navigation we are about to ask for.
        self._document_status = None
        self._loaded = False
        self._frame_id = None
        self._loader_id = None
        self._pending_docs = []
        res = self.call("Page.navigate", {"url": url}, on_session=True)
        if res.get("errorText"):
            raise _CDPError("navigation failed: %s" % res["errorText"])
        # The identity of OUR navigation; every event is matched against these.
        self._frame_id = res.get("frameId")
        self._loader_id = res.get("loaderId")
        # Now that we know which navigation is ours, judge whatever arrived
        # before we did. On a real browser this is where the status is
        # recovered: the response beat this reply to the socket.
        self._resolve_pending()
        while not self._loaded:
            left = self._left()
            if left <= CDP_POST_LOAD_RESERVE:
                break
            try:
                self._on_event(self._recv(timeout=left - CDP_POST_LOAD_RESERVE))
            except _CDPError:
                break
        return self._document_status

    @property
    def paired_loader_id(self) -> str | None:
        """The document the status from `navigate()` belongs to.

        Read once, immediately after `navigate()` returns, and held by the
        caller. It must NOT be re-read later: `_on_event` adopts a new loader
        when it sees our own frame renavigate, so the live field can move to the
        very document we are trying to detect a move to.
        """
        return self._loader_id

    def live_loader_id(self) -> str | None:
        """ASK THE BROWSER which document our top frame is showing, right now.

        THIS REPLACES COUNTING REPLACEMENTS, AND THE DIFFERENCE IS THE WHOLE
        POINT. Counting is observational, and an observation can be missed: first
        because the navigation's identity had not arrived yet, then because
        `call()` returns on its matching reply and never drains what follows, so
        a response frame landing after the last evaluation is simply never read.
        Two review rounds found one defect through those two holes. A question
        put to the browser has neither, because it does not depend on having seen
        anything.

        RETURNS None WHEN THE BROWSER COULD NOT BE ASKED, and the caller must
        read that as "not the document I paired with". Fail closed: silence is
        not confirmation.

        IT CANNOT RAISE, and that is load-bearing rather than defensive. It runs
        AFTER the page has been read, so an exception escaping here would convert
        a completed fetch into a tier failure -- exactly what the cold-cache
        user-agent probe did once, raising out from under an open browser and
        releasing its profile claim. Losing a status costs one honestly labelled
        manufactured 200; losing the fetch costs the fetch.

        `Page.getFrameTree` is scoped to OUR session and therefore to our own
        target, and it is a one-shot read that changes no browser state -- the
        same standing as `Target.getTargets`, and unlike the browser-wide
        subscriptions on the never-send list.
        """
        try:
            tree = self.call("Page.getFrameTree", on_session=True)
        except _CDPError:
            return None
        frame = (tree.get("frameTree") or {}).get("frame") or {}
        loader = frame.get("loaderId")
        return loader if isinstance(loader, str) and loader else None

    def evaluate(self, expression: str) -> str:
        """Evaluate in our target and return a string result."""
        value = self.evaluate_value(expression)
        return value if isinstance(value, str) else ""

    def evaluate_value(self, expression: str) -> object:
        """Evaluate and return the DESERIALIZED value, whatever shape it has.

        `evaluate()` coerces anything non-string to "", which is right for the
        callers that want text and WRONG for a caller reading a structured pair --
        a shape problem would arrive as an empty string and be judged as an empty
        page. So the structured caller gets the value and does its own checking.
        """
        res = self.call("Runtime.evaluate", {
            "expression": expression,
            "returnByValue": True,
            "awaitPromise": False,
        }, on_session=True)
        if res.get("exceptionDetails"):
            raise _CDPError("evaluation failed in the page")
        return (res.get("result") or {}).get("value")

    def close(self) -> None:
        """Best-effort teardown. CANNOT RAISE -- it runs in a `finally`.

        An exception raised in a `finally` REPLACES the one in flight, so a
        cleanup failure here would mask the real tier error and defeat the
        containment at the tier boundary that exists to report it.

        WE CLOSE EXACTLY THE TARGET WE CREATED, BY THE ID THE BROWSER GAVE US.
        That is the safety property an index could never provide: closing an
        earlier tab splices the list and a later tab inherits our index, which is
        how one agent came to close another's tab. An id names one target for
        that target's life and names nothing else, ever.

        AND WE DO NOT TAKE THE BROWSER DOWN WITH US. Chrome exits when its last
        page closes. If the owner closes his own tabs while a fetch is running,
        ours becomes the last page and closing it would end his session --
        review round 1's must-fix, and this project has already lost his browser
        once from the other direction. So when ours is the only page left we
        navigate it to about:blank and leave it standing. That strands one empty
        tab in a rare case; the alternative strands his whole browser.
        """
        # TEARDOWN GETS ITS OWN BUDGET, AND IT MUST. `call()` refuses once the
        # fetch deadline has passed -- the guard that stops a chatty page
        # spinning the read loop -- so a fetch that used its whole budget would
        # arrive here, raise on the first command, land in the `except` below
        # and SILENTLY SKIP CLOSING ITS OWN TARGET. That leaks a tab in the
        # owner's browser exactly when the fetch was slowest, which is the
        # common case rather than a rare one. Review round 1 finding #4 named
        # this shape, and the deadline check added for its first half is what
        # created this half.
        self._deadline = time.monotonic() + CDP_TEARDOWN_BUDGET
        try:
            if self._target_id:
                if self._is_last_page():
                    self.call("Page.navigate", {"url": "about:blank"},
                              on_session=True)
                else:
                    self.call("Target.closeTarget", {"targetId": self._target_id})
        except Exception:
            pass
        try:
            self._ws.close()
        except Exception:
            pass


def tier_cdp(url: str, *, total_timeout: float = 30.0,
             allow_private: bool = False, port: int = CDP_PORT) -> TierResult:
    """Tier 4 entry point: `_tier_cdp_impl` with a tier-boundary catch.

    WHY THIS EXISTS, and it outlived the transport it was written for. When this
    tier drove `playwright-cli`, `_run_pw` raised OSError on a spawn failure at
    nine unguarded sites, and that exception left the tier, left `cascade_fetch`
    (which sits in no try) and exited `main` as a traceback: one unrunnable
    binary killed the WHOLE fetch instead of one tier. The transport is now a
    websocket and the failure modes are different -- a refused connection, a
    torn socket, a browser that stops answering -- but the boundary property is
    the same one and is if anything more load-bearing, because a network peer
    has more ways to fail than a subprocess did.

    `except Exception`, not `BaseException`: a KeyboardInterrupt must still get
    out. Cleanup is unaffected -- `_tier_cdp_impl`'s `finally` has already run
    by the time anything reaches this handler, and `_CDPSession.close` cannot
    raise, so a cleanup failure can no longer replace the real error in flight.
    """
    t0 = time.monotonic()
    try:
        return _tier_cdp_impl(url, total_timeout=total_timeout,
                              allow_private=allow_private, port=port)
    except Exception as e:
        return TierResult(
            ok=False, tier="cdp", error=str(e),
            failure_class=classify_failure(None, "", type(e).__name__),
            elapsed_ms=int((time.monotonic() - t0) * 1000),
        )


def _tier_cdp_impl(url: str, *, total_timeout: float = 30.0,
                   allow_private: bool = False, port: int = CDP_PORT) -> TierResult:
    """Tier 4: drive the dedicated CDP Chrome over the DevTools protocol.

    The browser is the user's DEDICATED one, not the browser they read mail in,
    and it cannot be otherwise: Chrome 136+ refuses `--remote-debugging-port` on
    the default profile, so the separation is enforced by Chrome as well as by
    us. The corollary that keeps being rediscovered is that this tier CANNOT
    inherit the user's ordinary cookies -- "CDP defeats bot walls because it is
    logged in" was refuted; what it holds is whatever the user logged into in
    THAT profile. That is what the escalation gate admits it for, and it admits
    it on TWO occasions and not one: a NEEDS_LOGIN verdict, where a signed-in
    profile is the only thing that can win at all, AND after the headed tier has
    already run and failed, which is the owner's "if it cannot solve the captcha
    itself, ask me for CDP".

    Prerequisite: that Chrome is running with `--remote-debugging-port`. WE DO
    NOT START IT -- WE ASK. The owner wants to be asked and to place the window
    himself, on a second screen or a second desktop where it costs him nothing;
    the cascade emits that ask when it runs out of tiers. Nothing here could
    place it for him in any case: launching is measured to steal focus in every
    placement that exists -- `--window-position` forces `show_state=kNormal`,
    which calls `Activate()`; `--start-minimized` does not exist; Windows
    refuses to let one process move another's window to a second desktop.
    """
    t0 = time.monotonic()
    deadline = t0 + total_timeout
    try:
        validate_url_safety(url, allow_private=allow_private)
    except UnsafeURL as e:
        return TierResult(ok=False, tier="cdp", error="unsafe URL: %s" % e,
                          failure_class="other",
                          elapsed_ms=int((time.monotonic() - t0) * 1000))
    if _ws_connect is None:
        return TierResult(
            ok=False, tier="cdp",
            error=("cdp needs the `websockets` package, which is not installed "
                   "on this machine"),
            failure_class="other",
            elapsed_ms=int((time.monotonic() - t0) * 1000))
    try:
        ws_url = _cdp_browser_ws_url(port, min(CDP_HTTP_TIMEOUT, total_timeout))
        sess = _CDPSession(ws_url, deadline)
    except _CDPError as e:
        return TierResult(ok=False, tier="cdp", error="cdp unavailable: %s" % e,
                          failure_class="other",
                          elapsed_ms=int((time.monotonic() - t0) * 1000))
    html = ""
    final_url = url
    nav_status: int | None = None
    try:
        sess.open_target()
        nav_status = sess.navigate(url)
        paired_loader = sess.paired_loader_id
        # HTML, location and access UI belong to the same synchronous DOM task.
        # The loader check still governs status provenance, not those paired values.
        raw = sess.evaluate_value(_CDP_READ_PAGE_OBSERVATION)
        html, seen, candidates = _parse_page_observation(raw)
        if seen:
            final_url = seen
        if sess.live_loader_id() != paired_loader:
            nav_status = None
    except _CDPError as e:
        return TierResult(ok=False, tier="cdp", error="cdp: %s" % e,
                          failure_class="other",
                          elapsed_ms=int((time.monotonic() - t0) * 1000))
    finally:
        sess.close()
    md = html_to_markdown(html, final_url)
    # THE STATUS IS REAL WHEN WE OBSERVED IT AND MANUFACTURED WHEN WE DID NOT,
    # and the two must never be handed to the content gate as the same thing --
    # which is the mistake this file already made once, hard-coding a 200 that
    # no origin ever sent. `Network.responseReceived` gives us the main
    # document's own status now, so the honest answer is usually available; when
    # it is not, `status_is_real=False` says the 200 is ours and grants it no
    # evidentiary weight, and both challenge-marker tuples are scanned because
    # the embeddable-marker exemption is granted on an ORIGIN's 2xx and on
    # nothing else. Same shape `tier_playwright` already uses.
    status_captured = isinstance(nav_status, int)
    eff_status = nav_status if status_captured else 200
    ok, reason = is_real_content(md, html, eff_status, status_is_real=status_captured)
    # Whole body, never a slice -- see classify_failure's contract. The status
    # passed here is the observed one or `None`, never the manufactured 200.
    # MEASURED on an empty body, classify_failure(200, ...) returns
    # "empty_content", which IS in state.PERSISTENT_FAILURE_CLASSES, off a 2xx we
    # invented; classify_failure(None, ...) returns the transient "other". That
    # measurement is unchanged: the `empty_content` arm requires a real 2xx, so
    # the manufactured one still cannot reach it.
    #
    # WHAT DID CHANGE is the sentence that used to follow -- that one line must
    # not treat an invented status as worthless while the next treats it as
    # evidence. The two lines now differ ON PURPOSE. is_real_content gets
    # status_is_real=False and keeps both marker tuples, so its VERDICT is
    # untouched and a cf-turnstile mount still rejects the document.
    # classify_failure gets status_unobserved=True and drops the embeddable
    # tuple, so the CLASS is no longer persistent. A verdict is spent on this
    # fetch; a class is spent on the whole domain, for every fetch after it.
    # Same call shape as tier_playwright, deliberately -- these two are the only
    # callers that mean this kind of absence, and they must not drift apart.
    fc = "" if ok else classify_failure(
        nav_status if status_captured else None, html, None, extracted=md,
        status_unobserved=not status_captured)
    redirect_chain = [final_url] if final_url and final_url != url else []
    return TierResult(
        ok=ok, tier="cdp", content=md if ok else "", html=html,
        status=eff_status, status_is_real=status_captured,
        bytes_=len(html.encode("utf-8", "replace")),
        error="" if ok else reason, failure_class=fc,
        redirect_chain=redirect_chain, final_url=final_url,
        access_gate_candidates=candidates,
        elapsed_ms=int((time.monotonic() - t0) * 1000),
    )
