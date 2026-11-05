"""fetch.py - main entry + cascade orchestrator + CLI for webfetch.

Per project rev-6 plan + decisions D1-D12.
"""

from __future__ import annotations

import contextlib
import importlib
import io
import argparse
import json
import sys
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

# Operational siblings are deliberately imported only by their entrypoints.

# D11/D24: Windows stdout convention - reconfigure to utf-8 in main() to avoid
# cp1252 encoding crashes on non-ASCII content.

SKILL_DIR = Path(__file__).resolve().parent
DEFAULT_BLOCKLIST = SKILL_DIR / "default_blocklist.json"

HEADED_SKIP_WARNING = (
    "headed tier skipped: this machine sets WEBFETCH_DISABLE_HEADED; "
    "unset it to let the fetch use a real browser window")

# THE ASK, and it is addressed to a person rather than to a shell. Reached when
# the cascade needs the browser holding the user's own logins and nothing is
# answering on the DevTools port. An agent relays this sentence verbatim, so it
# has to say what was hit, what to start, that the window may live anywhere the
# user likes, and that the fetch is retried afterwards.
#
# ONE literal, for the reason the constant it replaced gave: the wording is what
# a human acts on, so two copies drifting apart would be a real defect and not a
# cosmetic one.
CDP_ASK = (
    "ASK THE USER: this request stopped at {reason}. Start the dedicated Chrome "
    "with --remote-debugging-port=9222 and leave it unminimized where you want "
    "it. Sign in or complete the challenge there if needed, then tell the agent "
    "to continue. The agent will not move, minimize or activate the browser.")

# The next step for the exits that never reach the cascade at all, keyed by the
# prefix of the error they return. Read by `_make_envelope`, so an exit added
# tomorrow inherits the guarantee instead of relying on its author to remember
# a line.
_NEXT_STEP_BY_PREFIX = (
    # ORDER MATTERS -- first match wins, so the SPECIFIC row precedes the
    # general one. `unsafe_url:` is one exception class covering two unrelated
    # situations: an address that is private or loopback, and a hostname DNS
    # did not answer for. A typo, a dead domain and a machine that lost DNS all
    # take the second route, and --allow-private cannot help any of them.
    ("unsafe_url: DNS resolution failed",
     "refused before any request was made: that hostname did not resolve. "
     "Check the URL for a typo, check the domain still exists, and check this "
     "machine's own DNS -- no tier and no browser reaches a name DNS does not "
     "answer for. --allow-private does not apply to this one."),
    ("unsafe_url:",
     "refused before any request was made: that URL resolves to a private, "
     "loopback or otherwise unsafe address. Pass --allow-private only if you "
     "meant to reach a host on this machine or LAN; otherwise check the URL."),
    ("invalid --start-tier",
     "pass --start-tier one of: http, headless, headed, cdp; Copilot is disabled."),
    ("invalid --max-tier",
     "pass --max-tier one of: http, headless, headed, cdp; Copilot is disabled."),
    ("no_runnable_tiers:",
     "the flags left no tier to run: --max-tier sets the ceiling, and cdp is "
     "listed only when a browser answers on the DevTools port. Raise --max-tier; "
     "Copilot synthesis remains disabled."),
)


def _cdp_ask(reason: str) -> str:
    """The CDP ask with the thing that stopped the cascade named inside it."""
    return CDP_ASK.format(reason=reason)


def _cdp_reason(verdict: str | None, login_seen: bool, captcha_seen: bool,
                *, headed_ran: bool = False) -> str:
    """Describe actual evidence without claiming an unexecuted headed run."""
    if verdict == "NEEDS_LOGIN" or login_seen:
        return "a login wall"
    if captcha_seen:
        return ("a captcha the headed browser could not clear" if headed_ran
                else "a captcha")
    return "a page no unattended browser could get"


# Tier 2 (headed) is ENABLED. It is a rung of the cascade like any other, and it
# is the rung that wins 26 of 320 distinct URLs on nothing lower (tiers.py's own
# measurement) -- the fetch succeeding comes first. A machine may OPT OUT with
# WEBFETCH_DISABLE_HEADED; that predicate AND the guard that enforces it
# both live in tiers.py, at the launch site -- see tiers.headed_tier_allowed.
# What happens below only SHAPES the cascade so the tier list and the
# warning are right; it is deliberately not a second copy of the policy.


# ---- Helpers -----------------------------------------------------------------



def _make_envelope(*, url: str, original_url: str | None, tier: str, extractor: str,
                   synthesized: bool, content: str, bytes_: int, elapsed_ms: int,
                   pages_read, from_cache: bool, blocklist_action,
                   tier_attempts: list, warnings: list,
                   redirect_chain: list[str], status: int | None = None,
                   status_is_real: bool = True,
                   error: str | None = None,
                   failure_class: str | None = None,
                   login_url: str | None = None,
                   retry_after: str | None = None,
                   next_step: str | None = None,
                   error_code: str | None = None) -> dict:
    env = {
        "url": url,
        "tier": tier,
        "extractor": extractor,
        "synthesized": synthesized,
        "rewritten_from": original_url if original_url and original_url != url else None,
        "content": content,
        "bytes": bytes_,
        "elapsed_ms": elapsed_ms,
        "pages_read": pages_read,
        "from_cache": from_cache,
        "blocklist_action": blocklist_action,
        "tier_attempts": tier_attempts,
        "warnings": warnings,
    }
    # Emitted TOGETHER, deliberately: a marker with no status beside it
    # would be a third state nobody asked for, and the absence of `status`
    # already means "none was available" on the cache-replay path.
    #
    # Browser tiers may substitute 200 when status capture is unavailable.
    # Preserve that value only with status_is_real=False; it is not origin proof.
    if status is not None:
        env["status"] = status
        env["status_is_real"] = status_is_real
    if error:
        env["error"] = error
        # R5 -- NEVER FAIL SILENTLY. Derived HERE, not at the five `return`s,
        # so the guarantee is structural: an exit added later cannot ship
        # without a next step, because there is no line for its author to
        # forget. A caller that knows better passes its own and this is a
        # no-op.
        if next_step is None:
            next_step = _derive_next_step(error, tier_attempts)
    if error_code:
        env["error_code"] = error_code
    if next_step:
        env["next_step"] = next_step
    if failure_class:
        env["failure_class"] = failure_class
    if login_url:
        env["login_url"] = login_url
    # The origin's own wait, verbatim. Falsy means the origin named none, and
    # the field is then ABSENT rather than defaulted: a number we invented is
    # worse than no number, because a caller cannot tell the two apart.
    if retry_after:
        env["retry_after"] = retry_after
    if redirect_chain:
        env["redirect_chain"] = redirect_chain
    return env


def _copilot_disabled_envelope(url: str) -> dict:
    """One refusal shape for explicit API and CLI requests, without effects."""
    from state import COPILOT_DISABLED_REASON
    return _make_envelope(
        url=url, original_url=None, tier="copilot", extractor="-",
        synthesized=False, content="", bytes_=0, elapsed_ms=0,
        pages_read=None, from_cache=False, blocklist_action=None,
        tier_attempts=[{
            "tier": "copilot", "ok": False, "elapsed_ms": 0,
            "error": COPILOT_DISABLED_REASON, "error_code": "disabled",
            "failure_class": "other",
        }], warnings=[], redirect_chain=[], error=COPILOT_DISABLED_REASON,
        error_code="disabled", failure_class="other",
        next_step="Use the raw http/headless/headed tiers or evidence-gated CDP; "
                  "Copilot diagnostics require a future authorized change.",
    )


def _derive_next_step(error: str, tier_attempts: list) -> str:
    """The next step for an error whose caller named none.

    The prefix table answers the exits that never reached the cascade. The
    fallback answers everything else by naming the highest rung that actually
    ran and pointing at the evidence -- without addressing the user
    imperatively, because a 404-shaped failure must not page a human.

    IT OFFERS NO BROWSER, and used to offer the CDP one unconditionally. This
    function is reached only where the CALLER named no next step, so it has no
    verdict, no sticky flag and no ceiling to reason from -- and `_gate_allows`
    admits the cdp rung on a login wall or a diagnosed captcha and on nothing
    else. "Start the CDP browser and retry" from here is therefore a retry that
    is byte-identical to the run that just failed, and on a box where that
    browser is already up it also names something already running. The
    exhaustion block below knows the run and says what is true of it; this
    fallback does not know it, so it does not claim.
    """
    for prefix, step in _NEXT_STEP_BY_PREFIX:
        if error.startswith(prefix):
            return step
    ran = [a.get("tier") for a in (tier_attempts or []) if "ok" in a]
    where = (f"the highest tier that ran was {ran[-1]}" if ran else "no tier ran")
    return (f"the fetch failed and {where}. Read `error` for what stopped it "
            "and `tier_attempts` for what each rung returned; a dead host, a "
            "wrong URL, or an exit taken before the cascade ran is not fixed "
            "by any browser.")


def _header(headers: dict | None, name: str) -> str:
    """One header out of a dict that carries the ORIGIN's own casing.

    `TierResult.headers` deliberately does not normalise, and header names are
    case-insensitive on the wire, so a plain `headers["content-type"]` finds
    nothing on most origins and every content-type-gated arm of the gate dies
    silently. `name` is given already lowercased.
    """
    for key, value in (headers or {}).items():
        if str(key).lower() == name:
            return value
    return ""


def _gate_allows(tier_name: str, verdict: str | None, *,
                 headed_attempted: bool = False,
                 captcha_seen: bool = False) -> bool:
    """Rule A -- the escalation gate filters tier ENTRY (plan S2, spec S3.1).

        enter headed   iff last_verdict != NEEDS_LOGIN
        enter copilot  never (source-owned disabled policy)
        enter cdp      iff last_verdict == NEEDS_LOGIN,
                        or (a captcha was diagnosed AND headed has had its turn)

    THE FETCH SUCCEEDING COMES FIRST, and a verdict is EVIDENCE ABOUT THE PAGE
    -- not a licence to spend one of the user's resources. So a verdict removes
    a rung only where that rung provably cannot help:

    * `headed` is refused on NEEDS_LOGIN alone. A throwaway profile has no
      session cookie for the site, so it cannot pass a sign-in wall however
      long it waits; the route there is the CDP ask. On every other verdict --
      including CONTINUE -- headed is entered, which is also what stops R1
      depending on the precision of captcha detection: a challenge the verdict
      failed to NAME still reaches the tier that clears it.
    * `copilot` is unconditionally disabled, independently of page evidence.
    * `cdp` is admitted on NEEDS_LOGIN, and otherwise once a captcha has been
      diagnosed in this run AND headed has had its turn. That is the owner's
      rule 4 in full -- "if #3 [the headed attempt at the captcha] fails, OR
      for a login requirement, the agent may request the user for CDP" -- and
      BOTH halves of the conjunction are load-bearing.

      `headed_attempted` ALONE IS NOT ENOUGH, and briefly was. headed now
      enters on every non-login verdict, so keyed on that flag alone every
      exhausted cascade reached cdp: a 404, a dead host and a timeout all drove
      the browser holding the user's logins, silently, with no captcha anywhere
      in the run. That buys R1 nothing -- no browser turns a 404 into a page --
      and spends the user's own session to do it.

      `captcha_seen` IS STICKY, WHICH IS WHY IT IS NOT `verdict ==
      "CAPTCHA_CHALLENGE"`. headed can legitimately drive the verdict to
      CONTINUE by clearing the challenge markers without ever getting the page;
      read off the LAST verdict, that success at diagnosing would read as
      "nothing to escalate" and lose the ask. Read off "a captcha was seen at
      any point in this run", it does not.

    HARD_BLOCK never reaches this function: the loop breaks on it before the
    next iteration exists. RATE_LIMITED DOES reach it, and did not until
    2026-09-04 -- see the loop for the measurement that stopped it breaking the
    cascade. `headed` remains admitted except on NEEDS_LOGIN. `cdp` still
    requires a login verdict or a diagnosed captcha after headed's opportunity.

    `headless` is deliberately ungated: it is the tier that satisfies a
    non-interactive JS challenge, and Rule B means no verdict exists below it.

    `verdict is None` means no lower rung produced a verdict. This alone never
    authorizes CDP; the same login/captcha evidence is still required.
    """
    if tier_name == "headed":
        return verdict != "NEEDS_LOGIN"
    if tier_name == "cdp":
        return verdict == "NEEDS_LOGIN" or (captcha_seen and headed_attempted)
    if tier_name == "copilot":
        return False
    return True


_INSPECTION_TIERS = ("http", "headless", "headed", "cdp")
_VERDICTS = frozenset({"CONTINUE", "CAPTCHA_CHALLENGE", "NEEDS_LOGIN",
                       "HARD_BLOCK", "RATE_LIMITED"})
_SKIP_REASONS = frozenset({"headed_disabled", "browser_unavailable", "ceiling",
                           "policy_disabled"})
_ACCESS_REQUIREMENTS = frozenset({"payment_required", "subscription_required"})
_OBSERVATION_KEYS = frozenset({"tier", "executed", "skip_reason", "verdict",
                               "login_url", "retry_after", "access_requirement"})
_MAX_INSPECTION_BYTES = 8 * 1024 * 1024
_MAX_BODY_BYTES = 4 * 1024 * 1024


class InspectionUnavailable(RuntimeError):
    """The shared classifier dependency could not be loaded."""


def _inspection_extract():
    # extract's historical dependency diagnostic prints and exits at import.
    # Inspection is a JSON-only boundary, including that failure path.
    bytecode = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return importlib.import_module("extract")
    except (ImportError, SystemExit) as exc:
        raise InspectionUnavailable("response inspection dependency unavailable") from exc
    finally:
        sys.dont_write_bytecode = bytecode


def _http_url(value: object) -> bool:
    if type(value) is not str or not value.strip():
        return False
    try:
        parsed = urlparse(value)
        parsed.port  # Validate the optional numeric port without resolving DNS.
        return (parsed.scheme.lower() in ("http", "https")
                and bool(parsed.hostname)
                and not any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value))
    except ValueError:
        return False


def _inspection_response(response: dict) -> dict:
    required = {"tier", "executed", "ok", "requested_url", "final_url", "body"}
    defaults = {"status": None, "status_is_real": False, "headers": {},
                "set_cookie": [], "hops": [], "skip_reason": None,
                "blocked_access": None, "access_gate_candidates": []}
    if (type(response) is not dict or not required <= response.keys()
            or response.keys() - required - defaults.keys()):
        raise ValueError("invalid response schema")
    r = {**defaults, **response}
    if (r["tier"] not in _INSPECTION_TIERS or type(r["tier"]) is not str
            or type(r["executed"]) is not bool or type(r["ok"]) is not bool
            or not _http_url(r["requested_url"]) or not _http_url(r["final_url"])
            or type(r["body"]) is not str or type(r["status_is_real"]) is not bool):
        raise ValueError("invalid response fields")
    try:
        if len(r["body"].encode("utf-8")) > _MAX_BODY_BYTES:
            raise ValueError("response body too large")
    except UnicodeError as exc:
        raise ValueError("invalid response body encoding") from exc
    if r["status"] is not None and (type(r["status"]) is not int or not 100 <= r["status"] <= 599):
        raise ValueError("invalid response status")
    if (type(r["headers"]) is not dict
            or any(type(k) is not str or type(v) is not str for k, v in r["headers"].items())
            or type(r["set_cookie"]) is not list
            or any(type(v) is not str for v in r["set_cookie"])):
        raise ValueError("invalid response metadata")
    if type(r["hops"]) is not list:
        raise ValueError("invalid redirect hops")
    for hop in r["hops"]:
        if (type(hop) not in (list, tuple) or len(hop) != 3
                or type(hop[0]) is not int or not 100 <= hop[0] <= 599
                or not _http_url(hop[1]) or type(hop[2]) is not str or not hop[2].strip()):
            raise ValueError("invalid redirect hop")
    skip = r["skip_reason"]
    if (skip is not None and (type(skip) is not str or skip not in _SKIP_REASONS)):
        raise ValueError("invalid skip reason")
    if (skip is not None and r["executed"]) or (r["ok"] and not r["executed"]):
        raise ValueError("inconsistent response execution")
    blocked = r["blocked_access"]
    if blocked is not None and (type(blocked) is not dict
            or blocked.keys() != {"requirement", "provenance"}
            or blocked["requirement"] != "subscription_required"
            or blocked["provenance"] not in ("explicit_user_report", "visible_blocking_ui")):
        raise ValueError("invalid blocked access")
    candidates = r["access_gate_candidates"]
    if type(candidates) is not list:
        raise ValueError("invalid access candidates")
    for candidate in candidates:
        if (type(candidate) is not dict or candidate.keys() != {"visible", "text", "controls"}
                or type(candidate["visible"]) is not bool or type(candidate["text"]) is not str
                or type(candidate["controls"]) is not list
                or any(type(c) is not str for c in candidate["controls"])):
            raise ValueError("invalid access candidate")
    if not r["status_is_real"]:
        r["status"] = None
    return r


def _inspection_history(history: tuple[dict, ...]) -> tuple[dict, ...]:
    if type(history) not in (list, tuple):
        raise ValueError("invalid inspection history")
    for item in history:
        if type(item) is not dict or item.keys() != _OBSERVATION_KEYS:
            raise ValueError("invalid history schema")
        if (type(item["tier"]) is not str or item["tier"] not in _INSPECTION_TIERS
                or type(item["executed"]) is not bool
                or (item["skip_reason"] is not None and
                    (type(item["skip_reason"]) is not str or item["skip_reason"] not in _SKIP_REASONS))
                or (item["verdict"] is not None and
                    (type(item["verdict"]) is not str or item["verdict"] not in _VERDICTS))
                or (item["login_url"] is not None and not _http_url(item["login_url"]))
                or (item["retry_after"] is not None and type(item["retry_after"]) is not str)
                or (item["access_requirement"] is not None and
                    (type(item["access_requirement"]) is not str
                     or item["access_requirement"] not in _ACCESS_REQUIREMENTS))):
            raise ValueError("invalid history fields")
        if ((item["skip_reason"] is not None and item["executed"])
                or (not item["executed"] and any(item[k] is not None for k in
                    ("verdict", "login_url", "retry_after", "access_requirement")))):
            raise ValueError("inconsistent history execution")
    return tuple(history)


def _inspection_error(code: str) -> dict:
    unavailable = code == "inspection_unavailable"
    return {"verdict": None, "failure_class": "other", "access_requirement": None,
            "next_action": "stop", "next_step": (
                "Install the declared extraction dependency before inspecting a response."
                if unavailable else "Supply a valid inspection request using the documented schema."),
            "login_url": None, "retry_after": None,
            "error": "Response inspection unavailable." if unavailable else "Invalid inspection input.",
            "error_code": code, "observation": None}


def inspect_response(response: dict, *, history: tuple[dict, ...] = (),
                     headed_allowed: bool, cdp_available: bool | None,
                     max_tier: str = "cdp", entitlement: str = "unknown") -> dict:
    """Classify one actual observation; no state, transport, probing or I/O.

    Reclassify after a CDP probe with the SAME response and prior history.
    The caller appends the returned observation only once, after reclassification.
    An accepted transport/content observation is not proof of an interaction.
    """
    r = _inspection_response(response)
    prior = _inspection_history(history)
    if (type(headed_allowed) is not bool
            or (cdp_available is not None and type(cdp_available) is not bool)
            or type(max_tier) is not str or max_tier not in _INSPECTION_TIERS
            or type(entitlement) is not str or entitlement not in ("unknown", "confirmed", "denied")):
        raise ValueError("invalid inspection options")
    extract = _inspection_extract()
    tier = r["tier"]
    verdict = (extract.escalation_verdict(
        tier=tier, status=r["status"], headers=r["headers"], set_cookie=r["set_cookie"],
        hops=r["hops"], body=r["body"], content_type=_header(r["headers"], "content-type"),
        requested_url=r["requested_url"], final_url=r["final_url"])
        if r["executed"] else None)
    access = None
    if r["executed"]:
        if r["blocked_access"] is not None or (tier != "http" and extract.visible_access_gate(r["access_gate_candidates"])):
            access = "subscription_required"
        elif r["status"] == 402:
            access = "payment_required"
    current_login = r["final_url"] if verdict == "NEEDS_LOGIN" else None
    current_wait = (_header(r["headers"], "retry-after") or None) if verdict == "RATE_LIMITED" else None
    observation = {"tier": tier, "executed": r["executed"], "skip_reason": r["skip_reason"],
                   "verdict": verdict, "login_url": current_login,
                   "retry_after": current_wait, "access_requirement": access}
    observations = (*prior, observation)
    actual = [o for o in observations if o["executed"]]
    executed = {o["tier"] for o in actual}
    captcha = any(o["verdict"] == "CAPTCHA_CHALLENGE" for o in actual)
    logins = [o for o in actual if o["verdict"] == "NEEDS_LOGIN"]
    login_seen = bool(logins)
    login_url = next((o["login_url"] for o in reversed(logins) if o["login_url"]), None)
    rates = [o for o in actual if o["verdict"] == "RATE_LIMITED"]
    rate = next((o for o in rates if o["retry_after"]), rates[0] if rates else None)
    wait = rate["retry_after"] if rate else None
    ceiling = _INSPECTION_TIERS.index(max_tier)
    headed_opportunity = ceiling >= 2 and any(
        o["tier"] == "headed" and (o["executed"] or o["skip_reason"] in
            ("headed_disabled", "browser_unavailable")) for o in observations)
    headed_ran = "headed" in executed
    leading = ("HARD_BLOCK" if any(o["verdict"] == "HARD_BLOCK" for o in actual)
               else "NEEDS_LOGIN" if login_seen else "RATE_LIMITED" if rate
               else "CAPTCHA_CHALLENGE" if captcha else verdict)
    out = {"verdict": leading, "failure_class": None, "access_requirement": access,
           "next_action": "stop", "next_step": "No permitted unexecuted stage remains.",
           "login_url": login_url, "retry_after": wait, "error": None,
           "error_code": None, "observation": observation}

    def decide(action: str, step: str, *, error: str | None = None,
               code: str | None = None) -> dict:
        out.update(next_action=action, next_step=step, error=error, error_code=code)
        return out

    if leading == "HARD_BLOCK":
        out["failure_class"] = "hard_block"
        out["retry_after"] = None
        step = "The origin refused access outright. A browser cannot clear a hard or legal block; use another legitimate source."
        if login_seen or captcha:
            step += " Earlier login/challenge evidence remains recorded, but does not override this block."
        return decide("stop", step, error="Hard or legal access block.")
    if access:
        out["failure_class"] = access
        if entitlement == "unknown":
            return decide("check_access", "ASK THE USER: do you already have legitimate access to this publisher's paid content? Wait for the answer; do not purchase, bypass the wall or report full content.", error="Legitimate access must be checked.")
        if entitlement == "denied":
            return decide("stop", "No legitimate entitlement is available. Use a public or publisher-supported source; do not bypass or purchase access.", error="Paid access is unavailable.")
        if tier == "http" or verdict not in ("NEEDS_LOGIN", "CAPTCHA_CHALLENGE"):
            return decide("stop", "Confirmed entitlement does not remove the observed wall. Use the publisher-supported access route; a fresh observation must show the wall gone.", error="Paid access wall remains visible.")
    hint = (r["executed"] and tier == "http" and r["ok"] and
            extract.access_gate_hint(r["body"], _header(r["headers"], "content-type")))
    if hint:
        if ceiling >= 1 and "headless" not in executed:
            return decide("headless", "Confirm the possible HTTP access gate with one owned headless browser observation.")
        out["failure_class"] = "other"
        return decide("stop", "The HTTP response may be an access wall; browser visibility could not be verified within the ceiling.", error="HTTP access state is unverified.", code="access_unverified")
    if (r["executed"] and tier != "http" and r["ok"] and verdict == "CONTINUE"
            and not r["headers"] and extract._login_form(r["body"])):
        # Structural login evidence cannot manufacture missing metadata or
        # grant CDP, but it must not be returned as full content.
        if tier == "cdp" and login_seen:
            out["failure_class"] = "needs_login"
            return decide("ask_user", "The CDP browser is running but is not signed in; sign in there, then tell the agent to continue.", error="Login access remains unresolved in the CDP browser.")
        out["failure_class"] = "other"
        return decide("stop", "The browser document has a sign-in form but response metadata is unavailable. Access remains unverified; use the publisher-supported sign-in route.", error="Browser access state is unverified.", code="access_unverified")
    if (r["executed"] and r["ok"] and verdict == "CONTINUE" and not access):
        out.update(verdict=verdict, failure_class=None, access_requirement=None,
                   login_url=None, retry_after=None)
        return decide("accept", "Content/transport is usable; verify the requested interaction separately.")
    out["failure_class"] = (leading.lower() if leading and leading != "CONTINUE" else None)
    if "cdp" in executed:
        if login_seen:
            return decide("ask_user", "The CDP browser is running but is not signed in; sign in there, then tell the agent to continue.", error="Login wall remains in the CDP browser.")
        if captcha:
            return decide("ask_user", "ASK THE USER: complete the challenge in the CDP browser already running on :9222, then tell the agent to continue. No automatic navigation loop or solver will run.", error="Challenge remains in the CDP browser.")
    browser_login = any(o["tier"] != "http" and o["verdict"] == "NEEDS_LOGIN" for o in actual)
    eligible = _gate_allows("cdp", "NEEDS_LOGIN" if browser_login else "CONTINUE",
                            headed_attempted=headed_opportunity, captcha_seen=captcha)
    if eligible and ceiling >= 3 and "cdp" not in executed:
        reason = _cdp_reason(verdict, login_seen, captcha, headed_ran=headed_ran)
        suffix = (f" First wait {wait} as the origin asked; the rate limit is also recorded." if wait else "")
        if cdp_available is None:
            return decide("probe_cdp", "Probe only the fixed dedicated endpoint 127.0.0.1:9222 once; reclassify this observation without appending it yet.")
        if not cdp_available:
            return decide("ask_user", _cdp_ask(reason) + suffix, error="Dedicated CDP browser unavailable.")
        return decide("cdp", "Use only an exactly owned target in the already-running dedicated CDP browser." + suffix)
    if "headless" not in executed and ceiling >= 1 and tier == "http":
        return decide("headless", "Try the owned headless stage once.")
    if ("cdp" not in executed and "headed" not in executed and ceiling >= 2 and headed_allowed
            and not any(o["tier"] == "headed" and o["skip_reason"] is not None for o in observations)
            and _gate_allows("headed", "NEEDS_LOGIN" if browser_login else verdict)):
        return decide("headed", "Try one owned headed browser, without moving, minimizing or activating its window.")
    if browser_login or captcha:
        step = "Login/challenge evidence remains, but no permitted CDP stage is reachable."
        if ceiling < 3:
            step += f" This run's --max-tier {max_tier} ceiling excludes CDP."
        elif not headed_opportunity and not browser_login:
            step += " Headed has neither executed nor been explicitly skipped as disabled/unavailable."
        return decide("stop", step, error="Login/challenge remains unresolved.")
    if rate:
        step = (f"The origin is rate limiting this client; wait {wait} as requested, then retry."
                if wait else "The origin is rate limiting this client; wait before a later retry.")
        return decide("stop", step, error="Rate limited.")
    return decide("stop", "No sign-in wall or interactive challenge was diagnosed. Ordinary exhaustion, a dead host or missing content does not authorize CDP.", error="No permitted stage produced usable content.")


# ---- Cascade -----------------------------------------------------------------

def cascade_fetch(url: str, *, raw_required: bool = False, no_rewrite: bool = False,
                  no_cache: bool = False, allow_private: bool = False,
                  start_tier: str | None = None, max_tier: str = "cdp",
                  require_cdp: bool = False,
                  connect_timeout: float = 5.0, read_timeout: float = 15.0,
                  total_timeout: float = 30.0,
                  copilot_model: str | None = None,
                  search_path: str | None = None,
                  conn=None) -> dict:
    """Orchestrate the cascade. Returns the output envelope dict.

    Any non-None copilot_model, including an empty string, is an explicit
    Copilot request and returns disabled before URL safety, DNS or cache work.
    """
    if start_tier == "copilot" or max_tier == "copilot" or copilot_model is not None:
        return _copilot_disabled_envelope(url)

    import safety
    import state
    import tiers

    t0 = time.monotonic()
    warnings: list[str] = []
    tier_attempts: list[dict] = []
    # ONE IDENTIFIER PER FETCH, minted here and passed to every blocklist write
    # this call makes. `state.py` used to INFER where one fetch ended and the
    # next began, from a rising tier number inside a 15-minute window, so two
    # concurrent fetches of one host -- which CC, Codex and omp produce by
    # design -- interleaved into four apparent cascades and cast twice the votes
    # toward promotion. This is the fact it was guessing at.
    cascade_id = uuid.uuid4().hex[:16]

    # Stage 1: safety
    try:
        safety.validate_url_safety(url, allow_private=allow_private)
    except safety.UnsafeURL as e:
        return _make_envelope(
            url=url, original_url=None, tier="-", extractor="-",
            synthesized=False, content="", bytes_=0,
            elapsed_ms=int((time.monotonic() - t0) * 1000),
            pages_read=None, from_cache=False, blocklist_action=None,
            tier_attempts=[], warnings=[], redirect_chain=[], error=f"unsafe_url: {e}",
        )

    # Stage 2: rewrites (path-scoped)
    rewritten_url, original_url = (url, None)
    if not no_rewrite:
        rewritten_url, original_url = state.apply_rewrites(conn, url)
        if original_url:
            warnings.append(f"rewrote {original_url} -> {rewritten_url}")

    domain = urlparse(rewritten_url).hostname or ""

    # Stage 3: cache lookup (composite key)
    allow_synthesis = False
    cache_key = state.compute_cache_key(rewritten_url, raw_required, max_tier, allow_synthesis)
    if not no_cache and not safety.has_sensitive_params(rewritten_url):
        cached = state.Cache(conn).get(cache_key)
        if cached and cached["tier"] != "copilot" and not cached["synthesized"]:
            return _make_envelope(
                url=rewritten_url, original_url=original_url,
                tier=cached["tier"], extractor="trafilatura",
                synthesized=False,
                content=cached["content"], bytes_=len(cached["content"]),
                elapsed_ms=int((time.monotonic() - t0) * 1000),
                pages_read=None, from_cache=True, blocklist_action=None,
                tier_attempts=[], warnings=warnings, redirect_chain=[],
            )

    # Stage 4: starting tier from blocklist
    bl = state.Blocklist(conn)
    bl_entry = bl.get(domain)
    # DID AN UNLOCKED BLOCKLIST ROW DECIDE WHERE THIS RUN STARTS? Recorded
    # here because this is the only place that knows WHO chose the start tier.
    # The exhaustion block a thousand lines below cannot reconstruct it: by
    # then `start_idx` is a number, and --start-tier, a locked row and an
    # automatic promotion all produce the same one.
    #
    # UNLOCKED ONLY, and the two exclusions are not symmetry for its own sake.
    # A locked row is the operator's own `UPDATE ... locked=1`, which README
    # documents as forcing a domain's tier PERMANENTLY; --start-tier is their
    # direct instruction for this fetch. Advising someone to override what they
    # set by hand is not what this sentence is for. A row the promotion machine
    # wrote by itself is nobody's instruction, which is the whole difference.
    #
    # IT RECORDS THE SOURCE AND NOTHING ELSE -- no `start_idx > 0` test, which
    # this flag carried for one draft and which a mutation sweep showed earns
    # nothing. `_clamp` returns 0 for a hint of 0 because tier 0 is in `allowed`
    # unconditionally, so a post-clamp start above http already implies a
    # pre-clamp one, and the exhaustion block tests the post-clamp fact off
    # `run_idxs` directly. A conjunct no reachable input can falsify is not a
    # guard, it is a second copy of its neighbour.
    start_from_unlocked_row = False
    if start_tier:
        try:
            start_idx = state.TIERS.index(start_tier)
        except ValueError:
            return _make_envelope(
                url=rewritten_url, original_url=original_url, tier="-", extractor="-",
                synthesized=False, content="", bytes_=0,
                elapsed_ms=int((time.monotonic() - t0) * 1000),
                pages_read=None, from_cache=False, blocklist_action=None,
                tier_attempts=[], warnings=warnings, redirect_chain=[],
                error=f"invalid --start-tier: {start_tier!r}",
            )
    elif bl_entry:
        # A PROMOTION MAY NEVER START A FETCH ABOVE headless (plan S3.3). A
        # start tier is not "entered" -- it is where the loop begins -- so Rule
        # A never sees it, and a domain promoted to headed would open a window
        # with no captcha diagnosis and no verdict computed at all, because
        # nothing below it ran. The cap is here rather than inside `_clamp`
        # precisely so the `if start_tier:` arm above is untouched: an explicit
        # --start-tier is the operator's direct instruction, a promotion is not.
        #
        # Capped at headless rather than merely below the windowed rungs: a
        # domain promoted to copilot would otherwise start THERE and produce no
        # verdict either, bypassing Rule A's copilot condition -- the condition
        # that stops a login wall being answered by a synthesized guess. The
        # price is re-running headless on a promoted domain -- and LOSING http,
        # which is the opposite of re-running it. This sentence said "re-running
        # http and headless" until 2026-09-11, which is false in the one
        # direction that matters: the clamp floors at headless, so index 0 never
        # enters `run_idxs` and the rung that reads a PDF is gone for the run.
        # The exhaustion block now says so in `next_step`.
        #
        # A LOCKED ENTRY IS ON THE INSTRUCTION SIDE OF THAT SAME LINE, so the
        # cap does not touch it. `locked=1` is not a state the promotion machine
        # can reach -- `record_failure` and `record_success` both refuse a locked
        # row -- so it is only ever a human running the UPDATE that README
        # documents as forcing a domain's tier PERMANENTLY. Capping it would
        # make that documented command mean something other than what it says.
        #
        # The consequence, stated rather than discovered: a locked headed or cdp
        # entry may therefore start at a windowed rung with no verdict computed
        # at all. That is the same latitude --start-tier already has, it is the
        # operator's explicit hand-written choice, and it is not the automatic
        # promotion this cap exists to stop. The reserved Copilot hint is the
        # exception: its disabled rung must fall back onto an unwindowed tier,
        # never forward into the user's authenticated CDP browser.
        start_idx = int(bl_entry["tier_idx"])
        if not bl_entry["locked"]:
            start_idx = min(start_idx, state.TIERS.index("headless"))
            start_from_unlocked_row = True
    else:
        start_idx = 0
    try:
        max_idx = state.TIERS.index(max_tier)
    except ValueError:
        return _make_envelope(
            url=rewritten_url, original_url=original_url, tier="-", extractor="-",
            synthesized=False, content="", bytes_=0,
            elapsed_ms=int((time.monotonic() - t0) * 1000),
            pages_read=None, from_cache=False, blocklist_action=None,
            tier_attempts=[], warnings=warnings, redirect_chain=[],
            error=f"invalid --max-tier: {max_tier!r}",
        )

    # Probe availability only after an eligible policy action, never on HTTP
    # success or ordinary exhaustion. Copilot remains a reserved gap.
    cdp_available: bool | None = None
    headed_ok = tiers.headed_tier_allowed()
    headed_idx = state.TIERS.index("headed")
    cdp_idx = state.TIERS.index("cdp")
    # THE RUNG A PROMOTION TAKES AWAY. Named beside the other two because the
    # exhaustion block asks whether it is in `run_idxs`, and `0` written there
    # as a literal says nothing about which tier it means.
    http_idx = state.TIERS.index("http")
    # THE TWO RUNGS A CLAMP MAY NOT LAND ON: `headed` opens a real browser
    # window, `cdp` drives the Chrome holding the user's logins. Both are
    # ordinary rungs of the cascade and both are ENABLED -- what they are not is
    # somewhere to START, because a start tier is never "entered" and so arrives
    # with no verdict computed and no lower tier tried. Named once so the clamp
    # cannot disagree with itself about which rungs those are.
    windowed_idxs = frozenset({headed_idx, cdp_idx})

    def _allowed_for(headed_permitted: bool) -> list[int]:
        """Tier indices this contract would run, given a headed permission."""
        out: list[int] = []
        for idx, name in enumerate(state.TIERS):
            if idx > max_idx:
                break
            if name == "copilot":
                continue
            if name == "headed" and not headed_permitted:
                # A GAP, not a ceiling: evidence-gated cdp stays reachable.
                continue
            out.append(idx)
        return out

    def _clamp(hint: int, allowed: list[int], headed_permitted: bool) -> int:
        """Move a start hint onto a tier that is actually in `allowed`.

        A missing headed rung falls downward when the machine opts out.
        Other missing hints, including the reserved disabled Copilot slot,
        must land on an allowed unwindowed rung: a clamp is not authority to
        start headed or the user's authenticated CDP browser without evidence.
        """
        if hint in allowed:
            return hint
        if (not headed_permitted) and hint == headed_idx:
            below = [i for i in allowed if i < hint]
            # `below` is empty only when NOTHING is allowed beneath headed.
            # http leaves `allowed` only via --max-tier, and a --max-tier below
            # headed drops headed itself, so `hint == headed_idx` could not
            # have survived the clamp's own guard. The arm stays as a guard
            # against a future exclusion, not as a live path -- do not write a
            # test claiming to cover it, which is what happened once already.
            # (An earlier version of this comment said `below` always holds
            # http AND headless. False: --max-tier http leaves just [0].)
            return max(below) if below else min(allowed)
        # A CLAMP MAY NOT LAND ON A WINDOWED RUNG. Same reasoning as the
        # promotion cap at the blocklist arm above, reached from the other
        # direction: the landing tier was named by NOBODY -- the caller named
        # `hint`, this function picked the landing -- and a start tier is not
        # "entered", so Rule A never sees it. Landing on headed or cdp would
        # therefore open a window, or drive the logged-in Chrome, with no
        # verdict computed and no tier beneath it run.
        #
        # THE DOCUMENTED LATITUDES DO NOT COME THROUGH HERE. `--start-tier
        # headed`, and a locked blocklist row AT a windowed rung, both leave on
        # the `hint in allowed` line above; this arm only ever fires for a hint
        # that named some OTHER tier. Measured 2026-09-03 over the 10,560-cell
        # flag/blocklist grid: this removes 720 ungated windowed starts and
        # leaves all 768 documented ones byte-identical.
        #
        # So the caller's floor gives way rather than the run STARTING at one
        # of those rungs. Not because a window is expensive -- it is not, and
        # the cascade opens one whenever it needs to -- but because a landing
        # here was chosen by this function rather than named by anyone, and a
        # rung reached that way runs with no verdict and nothing tried beneath
        # it. The cascade still climbs to both rungs in the ordinary way.
        above = [i for i in allowed if i > hint and i not in windowed_idxs]
        if above:
            # NOT A LIVE PATH TODAY -- a guard against a future exclusion, and
            # the same status the `below` arm above has. Excluding the windowed
            # rungs leaves only http, headless and copilot as landings, and each
            # is unreachable from a hint below it: http is in `allowed`
            # unconditionally, so it is never the missing hint; headless is
            # removed only by a --max-tier that also removes everything above
            # it; and copilot sits above headed, the only hint that can be
            # missing while copilot is present.
            #
            # THAT LAST CLAUSE NEEDS TWO CASES, NOT ONE, and an earlier version
            # of this comment gave only the first -- the conclusion held, the
            # stated reason did not, which is worse than no reason in a comment
            # whose whole job is to stop the next reader writing a test.
            # `hint == headed` with headed NOT permitted is answered by the
            # machine branch above and never arrives here. With headed
            # PERMITTED it does arrive -- and then headed being absent from
            # `allowed` means --max-tier is below 2, which removes copilot too,
            # so `above` is empty by a different route.
            #
            # Traced over 52,800 runs (10,560 flag/blocklist cells x 5
            # verdicts), 2026-09-03: 0 hits. Re-traced independently over 2,640
            # runs, 2026-09-04: the line is REACHED 1,604 times and `above` is
            # empty every time. DO NOT WRITE A TEST CLAIMING TO COVER IT.
            return min(above)
        unwindowed = [i for i in allowed if i not in windowed_idxs]
        # Never empty: tier 0 is excluded by no flag and by no ceiling, so it
        # is in `allowed` whenever `allowed` is -- and the caller returned
        # already if it was not.
        return max(unwindowed)

    allowed_idxs = _allowed_for(headed_ok)
    if not allowed_idxs:
        return _make_envelope(
            url=rewritten_url, original_url=original_url, tier="-", extractor="-",
            synthesized=False, content="", bytes_=0,
            elapsed_ms=int((time.monotonic() - t0) * 1000),
            pages_read=None, from_cache=False, blocklist_action=None,
            tier_attempts=[], warnings=warnings, redirect_chain=[],
            error="no_runnable_tiers: contract excludes every tier "
                  "(check --max-tier / --raw-required / cdp)",
        )
    requested_idx = start_idx
    start_idx = _clamp(start_idx, allowed_idxs, headed_ok)

    # WOULD PERMITTING HEADED HAVE CHANGED WHERE THIS RUN STARTS? That is the
    # honest question, and "did the clamp move us" is not it. Two inputs prove
    # the difference, and each broke one of the two earlier attempts:
    #
    #   --start-tier headed, no ceiling, leaves [0, 1, 3]. The machine branch
    #   moves the run down to headless -- and with headed permitted it would
    #   have started ON headed. Policy moved the run, so it must say so.
    #
    #   --start-tier headed --max-tier http leaves [0] either way. The machine
    #   branch fires here too, but the caller's own ceiling had already excluded
    #   headed, so permitting headed would change nothing -- and we promised
    #   it would.
    #
    # Planning it both ways answers both without a special case for either.
    # (Cross-family review 2026-08-29, round 4 findings [0] and [1].)
    #
    # THE FIRST EXAMPLE IS A REPLACEMENT, NOT THE ORIGINAL. It used to read:
    # `--start-tier copilot --raw-required --max-tier copilot` leaves [0, 1],
    # the CALLER excluded copilot so the machine branch never runs, yet with
    # headed permitted the fallback would have landed ON headed. The
    # windowed-landing exclusion in `_clamp` killed that walk the same day it
    # was written -- the fallback now lands on headless whichever way the
    # variable is set, so `alt_start == headed_idx` is false and the warning
    # cannot fire on that input at all.
    #
    # WHAT STILL REACHES IT, MEASURED 2026-09-03 over the 200-cell start_tier x
    # max_tier x --raw-required x cdp-reachable x headed-permitted grid, with
    # every tier stubbed to SUCCEED so the late `headed_skip_matters` path
    # cannot contribute a warning: 12 cells, every one of them
    # `requested_idx == headed`. Before the exclusion it was 26 -- the other 14
    # being requested_idx copilot (7) and cdp (7), i.e. exactly the walk-up
    # landings the exclusion removed. A locked blocklist row is the only other
    # way `requested_idx` reaches headed, and that row is on the instruction
    # side of the promotion cap, so it warns for the same true reason.
    #
    # SO IS THE COUNTERFACTUAL STILL EARNING ITS COMPLEXITY, now that it has
    # collapsed to one requested tier? JUDGED 2026-09-03: KEPT, and here is what
    # it still buys. Over that same grid it is now exactly equivalent to the
    # direct test `not headed_ok and requested_idx == headed_idx and headed_idx
    # <= max_idx` (0 mismatches in 200 cells), so the question is fair. What the
    # direct form loses is that its third conjunct is a HAND-COPY of
    # `_allowed_for`'s membership rule with nothing keeping the two in
    # agreement, where this form calls the real `_allowed_for` and the real
    # `_clamp`. That is not hypothetical: the landing rule changed UNDER this
    # code and it needed no edit -- the reachable set went from 26 cells to 12
    # by itself -- while a hand-copy written the day before would now be stating
    # something false with nothing to make it fail.
    started_below_headed_by_policy = False
    if not headed_ok:
        alt_allowed = _allowed_for(True)
        if alt_allowed:
            alt_start = _clamp(requested_idx, alt_allowed, True)
            started_below_headed_by_policy = (alt_start == headed_idx
                                              and start_idx != headed_idx)

    # Whether the skip is worth announcing at all is otherwise decided LATER, at
    # the point the cascade actually steps over the rung -- see the loop. Decided
    # here it fired on every run where headed merely SAT in range, including the
    # ~60% that succeed at http and never go near a browser.
    headed_skip_matters = (not headed_ok) and start_idx <= headed_idx <= max_idx
    headed_skip_warned = False
    if started_below_headed_by_policy:
        warnings.append(HEADED_SKIP_WARNING)
        headed_skip_warned = True

    fetch_log = state.FetchLog(conn)
    blocklist_action = None
    last_result = None
    rate_result = None
    history: tuple[dict, ...] = ()
    policy = None
    run_idxs = [i for i in allowed_idxs if i >= start_idx]
    tier_name = state.TIERS[start_idx]
    cascade_exhausted = False
    format_error = ""

    def classify(response: dict, prior: tuple[dict, ...]) -> dict:
        nonlocal cdp_available
        decision = inspect_response(response, history=prior,
                                    headed_allowed=headed_ok,
                                    cdp_available=cdp_available, max_tier=max_tier)
        if decision["next_action"] == "probe_cdp":
            cdp_available = safety.cdp_port_reachable()
            # Reclassify against the original history; append only once.
            decision = inspect_response(response, history=prior,
                                        headed_allowed=headed_ok,
                                        cdp_available=cdp_available, max_tier=max_tier)
        return decision

    while True:
        if tier_name == "cdp" and cdp_available is None:
            # Explicit/locked starts are operator-selected, not auto escalation.
            cdp_available = safety.cdp_port_reachable()
            if not cdp_available:
                observation = {"tier": "cdp", "executed": False,
                    "skip_reason": "browser_unavailable", "verdict": None,
                    "login_url": None, "retry_after": None, "access_requirement": None}
                policy = {"verdict": None, "failure_class": None,
                    "access_requirement": None, "next_action": "ask_user",
                    "next_step": _cdp_ask("the unavailable dedicated browser"),
                    "login_url": None, "retry_after": None,
                    "error": "CDP unreachable at :9222", "error_code": None,
                    "observation": observation}
                history = (*history, observation)
                tier_attempts.append({"tier": "cdp", "skipped": "no CDP target on :9222"})
                break
        tier_idx = state.TIERS.index(tier_name)
        if tier_name == "http":
            result = tiers.tier_http(rewritten_url, connect_timeout=connect_timeout,
                read_timeout=read_timeout, total_timeout=total_timeout, allow_private=allow_private)
        elif tier_name in ("headless", "headed"):
            result = tiers.tier_playwright(rewritten_url, mode=tier_name,
                total_timeout=total_timeout, allow_private=allow_private)
        else:
            result = tiers.tier_cdp(rewritten_url, total_timeout=total_timeout,
                                    allow_private=allow_private)

        unsafe_redirect = False
        for redirect_target in result.redirect_chain or []:
            try:
                safety.validate_url_safety(redirect_target, allow_private=allow_private)
            except safety.UnsafeURL as exc:
                result.ok = False
                result.error = f"unsafe redirect target {redirect_target}: {exc}"
                result.failure_class = "other"
                unsafe_redirect = True
                break

        skip_reason = result.error_code if result.error_code in ("headed_disabled", "browser_unavailable") else None
        response = {"tier": tier_name, "executed": skip_reason is None,
                    "ok": result.ok and skip_reason is None, "skip_reason": skip_reason,
                    "requested_url": rewritten_url, "final_url": result.final_url or rewritten_url,
                    "body": result.html, "status": result.status,
                    "status_is_real": result.status_is_real,
                    "headers": result.headers, "set_cookie": result.set_cookie,
                    "hops": result.hops, "access_gate_candidates": result.access_gate_candidates,
                    "blocked_access": result.blocked_access}
        if unsafe_redirect:
            policy = _inspection_error("invalid_input")
            policy.update(error=result.error, error_code=None,
                          next_step="The unsafe redirect was refused; do not navigate another tier.")
        else:
            try:
                policy = classify(response, history)
            except (ValueError, InspectionUnavailable) as exc:
                policy = _inspection_error("inspection_unavailable" if isinstance(exc, InspectionUnavailable) else "invalid_input")
        if policy["observation"] is not None:
            history = (*history, policy["observation"])
        accepted = policy["next_action"] == "accept" and not unsafe_redirect
        if not accepted:
            result.ok = False
            result.error = result.error or policy["error"] or "Response inspection requires another stage."
            result.failure_class = policy["failure_class"] or result.failure_class or "other"
        attempt = {"tier": tier_name, "ok": accepted, "elapsed_ms": result.elapsed_ms,
                   "failure_class": result.failure_class}
        if result.error_code:
            attempt["error_code"] = result.error_code
        if not accepted:
            attempt["error"] = result.error[:512]
        if result.sub_attempts:
            attempt["sub_attempts"] = result.sub_attempts
        tier_attempts.append(attempt)
        fetch_log.append(safety.redact_sensitive_params(rewritten_url), tier_name,
                         accepted, bytes_=result.bytes_, elapsed_ms=result.elapsed_ms,
                         error=None if accepted else result.error[:512])
        if accepted:
            blocklist_action = bl.record_success(domain, tier_idx, cascade_id=cascade_id)
            final = result.final_url or rewritten_url
            # The cache schema cannot reproduce redirects, pages_read or an
            # extractor other than trafilatura. Never cache lossy envelopes.
            cacheable = (final == rewritten_url and not result.redirect_chain
                         and not result.pages_read and result.extractor == "trafilatura")
            if cacheable and not no_cache and not safety.has_sensitive_params(final):
                state.Cache(conn).put(cache_key, result.content, tier_name, synthesized=False)
            envelope = _make_envelope(url=final, original_url=original_url,
                tier=tier_name, extractor=result.extractor, synthesized=False,
                content=result.content, bytes_=result.bytes_,
                elapsed_ms=int((time.monotonic() - t0) * 1000), pages_read=result.pages_read,
                from_cache=False, blocklist_action=blocklist_action,
                tier_attempts=tier_attempts, warnings=warnings,
                redirect_chain=result.redirect_chain, status=result.status,
                status_is_real=result.status_is_real)
            envelope.update(next_action="accept", inspection_history=list(history))
            return envelope

        blocklist_action = bl.record_failure(domain, tier_idx, result.failure_class or "other",
                                            reason=result.error[:200], cascade_id=cascade_id)
        last_result = result
        observation = policy["observation"]
        if observation and observation["verdict"] == "RATE_LIMITED":
            if rate_result is None or (observation["retry_after"] and not rate_result[0]):
                rate_result = (observation["retry_after"], result)
        if (result.skip_browser_tiers and policy["next_action"] in ("headless", "headed", "stop")
                and policy["verdict"] != "HARD_BLOCK" and not policy["access_requirement"]
                and policy["error_code"] is None):
            format_error = result.error
            for skipped in ("headless", "headed"):
                skipped_idx = state.TIERS.index(skipped)
                if skipped_idx > tier_idx and skipped_idx in allowed_idxs:
                    tier_attempts.append({"tier": skipped,
                        "skipped": f"document-level failure at a lower tier: {format_error[:120]}"})
            policy.update(next_action="stop", next_step=(
                f"This URL returned a document our extractors could not turn into text: {format_error}. "
                "Browser stages read the page DOM, not a PDF plugin's text; no further "
                "browser stages will be tried. Copilot synthesis is disabled. Use a source "
                "that publishes text, or extract the file yourself."))

        # Record an actual policy skip instead of inferring opportunity from a
        # missing tier. Neither a ceiling nor document failure grants it.
        if (not headed_ok and headed_idx <= max_idx and tier_name in ("http", "headless")
                and policy["next_action"] == "stop" and not policy["access_requirement"]
                and policy["verdict"] != "HARD_BLOCK" and not result.skip_browser_tiers
                and policy["error_code"] is None and not unsafe_redirect
                and not any(o["tier"] == "headed" for o in history)):
            skipped_response = {"tier": "headed", "executed": False, "ok": False,
                "requested_url": rewritten_url, "final_url": response["final_url"],
                "body": "", "skip_reason": "headed_disabled"}
            policy = classify(skipped_response, history)
            history = (*history, policy["observation"])
            tier_attempts.append({"tier": "headed", "skipped": "headed_disabled"})
            if not headed_skip_warned:
                warnings.append(HEADED_SKIP_WARNING)
                headed_skip_warned = True

        action = policy["next_action"]
        if action in ("headless", "headed", "cdp"):
            tier_name = action
            continue
        cascade_exhausted = action == "stop" and policy["verdict"] != "HARD_BLOCK" and not policy["access_requirement"]
        break

    next_step = policy["next_step"]
    if next_step.startswith("ASK THE USER"):
        warnings.append(next_step)
    if headed_skip_matters and not headed_skip_warned and cascade_exhausted and not format_error:
        warnings.append(HEADED_SKIP_WARNING)
    # Promote the whole selected rate response, not selected fields paired
    # with a later rung's status, URL or redirect chain.
    reported = (rate_result[1] if rate_result and policy["failure_class"] == "rate_limited"
                else last_result)
    err = format_error or (reported.error if reported else policy["error"]) or "no tiers attempted"
    if reported and policy["failure_class"] in ("rate_limited", "hard_block"):
        err = f"{policy['failure_class']} at {reported.tier}: {err}"

    # R5 AGAIN, FOR A RUNG THAT WAS NEVER OFFERED A TURN. An unlocked blocklist
    # row raises the start tier, the clamp above floors that at HEADLESS rather
    # than at http, and `run_idxs` is built once and never mutated -- so tier 0
    # is absent from the run and there is no way back down inside it. Tier 0 is
    # the only rung that extracts a PDF, returns a NON-HTML textual body
    # verbatim -- JSON, JavaScript, XML, plain text -- or reports that no browser
    # can read the document. NOT `text/*`: `_is_verbatim_text_type` rejects
    # `text/html` and `application/xhtml+xml` BEFORE its `startswith("text/")`
    # test, so the commonest `text/*` type on the web is the one type that does
    # NOT take this path. Until this
    # sentence existed an exhausted run said none of that: the fetch failed,
    # the envelope never mentioned the rung it had skipped, and `--start-tier
    # http` works by hand and is measured to work. A failure carrying no
    # instruction the user can act on is the defect R5 names.
    #
    # THE CAUSE AND THE EFFECT ARE TESTED SEPARATELY AND BOTH ARE LOAD-BEARING.
    # `start_from_unlocked_row` is the CAUSE, recorded at the derivation site
    # because only that site knows who chose the tier. `http_idx not in
    # run_idxs` is the EFFECT, read off the run itself -- because `_clamp` can
    # put the run back on tier 0 (`--max-tier http` leaves `[0]` as the only
    # landing), and a note claiming a rung was skipped on a run that ran it is
    # the false instruction R5 rates worse than none.
    #
    # `cascade_exhausted`, NOT `not gate_stop`. The question is how the LOOP
    # ended, and a promoted rate limit sets `gate_stop` AFTER the loop has
    # already run out of tiers, so reading it would delete a note that is true.
    # Measured 2026-09-08 on HEADED_SKIP_WARNING, which asks the same question
    # one screen above and is where that reading was refuted.
    #
    # APPENDED AFTER THE ASK MIRROR, deliberately. The mirror copies
    # `next_step` into `warnings` unless that exact string is already there,
    # and the in-loop cdp ask can have put the bare ask there already. Appended
    # BEFORE it, the suffixed string is "not in warnings" and the ask lands a
    # second time as a near-identical copy -- the duplicate the mirror's own
    # comment exists to prevent. After it, `warnings` carries the ask once and
    # unchanged, and `next_step` carries the ask plus this note.
    if (cascade_exhausted and start_from_unlocked_row
            and http_idx not in run_idxs):
        # THE RECORDED TIER IS DERIVED FROM `tier_idx`, NOT READ OFF THE `tier`
        # TEXT COLUMN. `tier_idx` is the row's recorded START HINT -- NOT the
        # tier this run began at, which is `run_idxs[0]` after the promotion cap
        # and the clamp have both had their say. An unlocked row recorded at
        # copilot starts the run at headless, and the two are named separately
        # in the sentence below for exactly that reason. The column beside
        # `tier_idx` is a label written at the same time and nothing re-checks
        # the pair, so naming the label could disagree with the number the run
        # actually used. (This comment asserted the opposite until 2026-09-11,
        # while the code and its test already drew the distinction correctly.)
        #
        # AND IT IS STATED AS HISTORY, NOT AS CURRENT STATE. `bl_entry` is
        # snapshotted before the loop, and this run's own failures reach
        # `record_failure`, which can promote the row further before this
        # sentence is composed. "carries a row at X" would then report a tier
        # the database no longer holds; "recorded at X when this run began" is
        # true whatever the run wrote afterwards, and it is also the fact that
        # explains where the cascade started. Cross-family review round 2.
        recorded = state.TIERS[int(bl_entry["tier_idx"])]
        # WHAT THE FLAG DOES, AND WHAT THE RETRY STILL DOES -- two different
        # claims, and an earlier draft made only the first while sounding like
        # both. `--start-tier` is read at the derivation site and nowhere else:
        # `record_success` (below, at the success return) and `record_failure`
        # (at the failure recording) take no start-tier argument, so the
        # prescribed retry appends its own outcome to this domain's row exactly
        # as any fetch does, and can promote it further or demote it. A
        # non-mutation assurance would be false about the one action this
        # sentence exists to recommend, which is the R5 defect it is meant to
        # repair, one level up. Cross-family review round 1 found it.
        # AND `--no-cache` IS PART OF THE INSTRUCTION, NOT A FLOURISH. The cache
        # is consulted at Stage 3, BEFORE the start tier is read at Stage 4, and
        # `compute_cache_key` is keyed on (url, raw_required, max_tier,
        # allow_synthesis) -- `start_tier` is not in it and `allow_synthesis`
        # is always false while Copilot is disabled. So if another fetch of
        # this URL SHARING ITS `--max-tier` AND
        # `--raw-required` succeeds between this failure and the advised retry --
        # another process, or the operator's own attempt at a higher START tier,
        # both ordinary in a skill whose concurrency model is independent
        # invocations -- the retry returns the cached body, never enters tier 0,
        # and records nothing. NOT "any other fetch", which is wider than the
        # key: a different `--max-tier` or `--raw-required` writes a NON-COLLIDING
        # key and cannot poison it. A retry without --no-cache can therefore
        # return cached content without making an HTTP attempt.
        # `--no-cache` skips both reads and writes so the HTTP rung is entered.
        # why-not make an explicit --start-tier bypass the cache instead: that
        # changes fetch behaviour for every caller, and this change is the
        # legibility fix -- the approved shape adds no tier or cache semantics.
        next_step += (
            f" This run also never tried the http tier: {domain} carried an "
            f"unlocked blocklist row recorded at {recorded} when the run began, "
            f"which started the cascade at {state.TIERS[run_idxs[0]]} -- and "
            "http is the only rung that extracts a PDF, returns a JSON or "
            "plain-text body verbatim, or reports that no browser can read the "
            "document. Re-run with --no-cache --start-tier http to include it: "
            "the flag decides this one run's start tier instead of the row, and "
            "--no-cache is needed because the cache is checked before the start "
            "tier and is not keyed on it, so a body cached meanwhile would come "
            "back without http running at all. The retry still records its own "
            "outcome against this domain and can move the row like any other "
            "fetch.")

    envelope = _make_envelope(
        url=rewritten_url, original_url=original_url, tier="-", extractor="-",
        synthesized=False, content="", bytes_=0,
        elapsed_ms=int((time.monotonic() - t0) * 1000), pages_read=None,
        from_cache=False, blocklist_action=blocklist_action,
        tier_attempts=tier_attempts, warnings=warnings,
        redirect_chain=reported.redirect_chain if reported else [],
        status=reported.status if reported else None,
        status_is_real=reported.status_is_real if reported else False,
        error=f"cascade_exhausted: {err}",
        error_code=policy["error_code"] or (reported.error_code if reported else None),
        failure_class=policy["failure_class"] or (
            reported.failure_class if reported and reported.error_code == "disabled" else None),
        login_url=policy["login_url"],
        retry_after=policy["retry_after"], next_step=next_step)
    envelope.update(next_action=policy["next_action"],
                    access_requirement=policy["access_requirement"],
                    inspection_history=list(history))
    return envelope


# ---- CLI ---------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="webfetch", allow_abbrev=False,
                                description="Smart auto-routing fetch with persistent blocklist learning.")
    p.add_argument("--inspect-response", action="store_true",
                   help="Pure JSON response/history classification from stdin; no fetch/state work")
    p.add_argument("--url", help="URL to fetch (required for non-admin operations)")
    p.add_argument("--format", choices=("json", "markdown"), default="json",
                   help="Output shape (default: json)")
    p.add_argument("--raw-required", action="store_true",
                   help="Require page-derived text (Copilot synthesis is disabled)")
    p.add_argument("--start-tier", choices=("http", "headless", "headed", "copilot", "cdp"),
                   help="Override starting tier")
    p.add_argument("--max-tier", choices=("http", "headless", "headed", "copilot", "cdp"),
                   default="cdp", help="Stop cascade at this tier (default: cdp). "
                        "NOTE: cdp is entered on a NEEDS_LOGIN verdict, or once "
                        "the headed tier has run and failed, and in either case "
                        "only when a browser answers on the DevTools port. The "
                        "headed tier runs by default; a machine opts out with "
                        "WEBFETCH_DISABLE_HEADED=1.")
    p.add_argument("--no-rewrite", action="store_true",
                   help="Skip URL rewrites (e.g. reddit -> old.reddit)")
    p.add_argument("--no-cache", action="store_true", help="Skip 15-min cache (read AND write)")
    p.add_argument("--allow-private", action="store_true",
                   help="Allow fetching private/loopback/link-local IPs (DEFAULT OFF - SSRF guard)")
    p.add_argument("--require-cdp", action="store_true",
                   help="Fail if Tier 4 (CDP) is selected but no Chrome at :9222")
    p.add_argument("--connect-timeout", type=float, default=5.0,
                   help="TCP connect timeout in seconds (Tier 0)")
    p.add_argument("--read-timeout", type=float, default=15.0,
                   help="Read timeout in seconds (Tier 0)")
    p.add_argument("--total-timeout", type=float, default=30.0,
                   help="Total wall-clock budget per fetch in seconds")
    p.add_argument("--copilot-model", default=None,
                   help="Retained for explicit refusal: Copilot synthesis is disabled")
    # Admin
    p.add_argument("--reap-zombies", action="store_true",
                   help="Admin: kill orphaned webfetch-* playwright sessions")
    p.add_argument("--clear-cache", action="store_true",
                   help="Admin: clear all cached entries")
    p.add_argument("--clear-blocklist", action="store_true",
                   help="Admin: drop blocklist (preserves config); next fetch re-bootstraps")
    return p


def _emit_envelope(envelope: dict, fmt: str) -> None:
    if fmt == "markdown":
        content = envelope.get("content", "")
        sys.stdout.write(content)
        if not content.endswith("\n"):
            sys.stdout.write("\n")
        # R5 IN THE HUMAN-READABLE FORMAT. This branch used to write `content`
        # and nothing else, so a failed fetch printed exactly one newline and
        # the whole diagnosis -- including the ask that has to reach a person --
        # was discarded. `content` is empty on every error envelope, so nothing
        # is displaced, and the "> " prefix is what tells a reader this is not
        # page text. Exit codes are untouched.
        error = envelope.get("error")
        if error:
            sys.stdout.write(f"\n> fetch failed: {error}\n")
            step = envelope.get("next_step")
            if step:
                sys.stdout.write(f"> next step: {step}\n")
            # THE DEDUPE IS A PREFIX TEST, NOT AN EQUALITY TEST, and it had to
            # become one. `warnings` carries the ASK ALONE while `next_step` may
            # carry the ask PLUS the skipped-http note, so `warning != step` --
            # correct while the two were one literal -- became true on exactly
            # the path the guard exists for, and markdown printed the whole ask
            # twice. MEASURED before the fix: 2 occurrences of "ASK THE USER"
            # in one envelope's output. A warning `next_step` already OPENS with
            # has been shown; anything else has not.
            for warning in (envelope.get("warnings") or []):
                text = str(warning)
                if (text.startswith("ASK THE USER")
                        and not (step or "").startswith(text)):
                    sys.stdout.write(f"> {text}\n")
    else:
        json.dump(envelope, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")


def _inspection_cli() -> int:
    def unique_object(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("duplicate inspection key")
            value[key] = item
        return value

    def invalid_constant(value):
        raise ValueError("non-JSON number")

    try:
        stream = getattr(sys.stdin, "buffer", sys.stdin)
        raw = stream.read(_MAX_INSPECTION_BYTES + 1)
        if isinstance(raw, str):
            raw = raw.encode("utf-8")
        if len(raw) > _MAX_INSPECTION_BYTES:
            raise ValueError("inspection request too large")
        request = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_object,
                             parse_constant=invalid_constant)
        keys = {"response", "history", "headed_allowed", "cdp_available", "max_tier", "entitlement"}
        if (type(request) is not dict or not {"response", "headed_allowed"} <= request.keys()
                or request.keys() - keys):
            raise ValueError("invalid inspection request")
        result = inspect_response(request["response"], history=request.get("history", ()),
            headed_allowed=request["headed_allowed"], cdp_available=request.get("cdp_available"),
            max_tier=request.get("max_tier", "cdp"), entitlement=request.get("entitlement", "unknown"))
        status = 0
    except InspectionUnavailable:
        result, status = _inspection_error("inspection_unavailable"), 1
    except (ValueError, UnicodeError, RecursionError):
        result, status = _inspection_error("invalid_input"), 1
    _emit_envelope(result, "json")
    return status


def main() -> int:
    # D24/Windows: ensure utf-8 stdout to avoid cp1252 encoding crashes.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

    argv = sys.argv[1:]
    inspecting = any(arg.split("=", 1)[0] == "--inspect-response" for arg in argv)
    if inspecting:
        # Contain argparse help/errors too: neither may echo private input or
        # produce a non-JSON frame on the inspection boundary.
        try:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                args = _build_parser().parse_args(argv)
            if argv != ["--inspect-response"]:
                raise ValueError("conflicting inspection options")
        except (SystemExit, ValueError):
            _emit_envelope(_inspection_error("invalid_input"), "json")
            return 1
        return _inspection_cli()
    args = _build_parser().parse_args(argv)
    if (args.start_tier == "copilot" or args.max_tier == "copilot"
            or args.copilot_model is not None):
        _emit_envelope(_copilot_disabled_envelope(args.url or ""), args.format)
        return 1

    import state
    import tiers

    conn = state.open_db()
    state.bootstrap_if_needed(conn, DEFAULT_BLOCKLIST)

    # M2 fix (discriminator iter 2): reap NON-canonical orphan webfetch-* sessions
    # left behind from prior crashes. Idempotent + safe to run on every invocation.
    #
    # SKIPPED for --reap-zombies. This sweep CLOSES AND DELETES the orphan
    # records, so running it first left the explicit command sweeping an
    # already-empty list and printing "reaped 0 ... deleted 0" -- the command
    # reported doing nothing about a condition it had just fixed three lines
    # earlier. Guarding is better than reordering: it keeps the implicit sweep
    # out of the admin path entirely.
    if not args.reap_zombies:
        reaped = tiers.startup_reap()
        # The sweep's count was discarded, so its work was invisible by
        # construction to every observer, including the failure census.
        if reaped:
            print(f"startup reap: closed and deleted {reaped} orphan "
                  f"webfetch-* session(s)", file=sys.stderr)

    # Admin commands
    if args.reap_zombies:
        result = tiers.reap_zombies()
        if result is None:
            print("reap failed: could not list playwright sessions. Nothing was "
                  "closed or deleted.", file=sys.stderr)
            return 1
        closed, deleted, problems = result
        # SAY WHAT WAS SWEPT, NOT THAT THE SWEEP WAS COMPLETE. The counts are
        # over the sessions the listing RETURNED, and a listing cut on a record
        # boundary is byte-for-byte a shorter valid one -- see `reap_zombies`.
        #
        # THE DENOMINATOR IS `deleted + problems`, AND IT IS EXACT. Every name
        # in the listing takes exactly one of three paths inside
        # `reap_zombies`: the close fails (-> `failed`); or it succeeds and the
        # delete succeeds (-> `deleted`); or it succeeds and the delete fails
        # (-> `undeleted`). `problems` is `failed + undeleted`, so
        # `deleted + problems` counts each listed name exactly once.
        # NOT `closed + problems`, which is what this line shipped first and
        # what review caught: a session that closed while its record would not
        # delete lands in `closed` AND in `undeleted`, so ONE listed session
        # reported as "1 of the 2".
        # The exactness rests on that three-way split staying TOTAL. Add a
        # fourth path to the loop -- a `continue` that skips the delete, say --
        # and this line starts under-reporting with nothing to warn you.
        listed = deleted + problems
        print(f"reaped {closed} of the {listed} webfetch-* session(s) that were "
              f"listed; deleted {deleted} session record(s). This is a sweep of "
              f"what the session listing returned, which cannot be shown to be "
              f"the whole of it. Chromium profile dirs under "
              f"{tiers.PROFILE_BASE}* are NOT removed by this command.",
              file=sys.stderr)
        if problems:
            print(f"reap INCOMPLETE: {problems} session(s) could not be fully "
                  f"cleaned up (see messages above).", file=sys.stderr)
            return 1
        return 0
    if args.clear_cache:
        state.Cache(conn).clear()
        print("cache cleared", file=sys.stderr)
        return 0
    if args.clear_blocklist:
        bl = state.Blocklist(conn)
        bl.clear()
        # Reset bootstrap so next fetch re-seeds.
        state.Config(conn).set("bootstrap_done", "0")
        # Also clear rewrites for re-bootstrap.
        conn.execute("DELETE FROM rewrites")
        print("blocklist + rewrites cleared; next fetch re-bootstraps", file=sys.stderr)
        return 0

    if not args.url:
        print("error: --url is required", file=sys.stderr)
        return 1


    envelope = cascade_fetch(
        args.url, raw_required=args.raw_required, no_rewrite=args.no_rewrite,
        no_cache=args.no_cache, allow_private=args.allow_private,
        start_tier=args.start_tier, max_tier=args.max_tier, require_cdp=args.require_cdp,
        connect_timeout=args.connect_timeout, read_timeout=args.read_timeout,
        total_timeout=args.total_timeout, copilot_model=args.copilot_model,
        conn=conn,
    )

    _emit_envelope(envelope, args.format)

    # Exit codes
    err = envelope.get("error", "")
    if err.startswith("unsafe_url:"):
        return 3
    if err.startswith("cascade_exhausted:") or err:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
