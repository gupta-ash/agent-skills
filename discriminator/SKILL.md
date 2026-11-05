---
name: discriminator
description: Cross-family code and content review. Sends an artifact to a reviewer on a DIFFERENT model family and returns structured findings with severities. Use for code review, design and plan review, content verification, and quality gates. The caller declares what kind of artifact it is; a same-family review is never substituted.
---

# discriminator — cross-family code and content review

Sends an artifact to a reviewer on a **different model family** and returns structured findings.
A same-family review is never substituted; if the cross-family reviewer cannot be reached, you get
an envelope saying no review happened, never a review this tool wrote itself.

## Host selection

**OMP:** follow § OMP — native tool below. **Claude Code / Codex:** follow § Calling it
and the CLI-only launch/collect, environment, handle, fault and timeout instructions. Shared
artifact classes, severity, canonical verdict floor, fix-loop gate and 0/1/2/4 meanings apply to
both hosts. Do not apply a CLI recipe to OMP.

## OMP — native tool

An ordinary primary or an **already-authorized tool-capable subagent** invokes the installed
managed **`adversarial_review`** tool directly with these named parameters:

| parameter | required | value |
|---|---|---|
| `caller_family` | yes | Original caller's internal family: `gpt` or `claude`. Delegation preserves it unchanged; do not replace it with the subagent's family. OMP/provider names are not family values. |
| `contract` | yes | One canonical artifact class from § Artifact classes, including explicit `none` when appropriate. |
| `source` | yes | Path to a readable, nonempty UTF-8 artifact file. A prepared diff is simply such a file. |
| `focus` | no | Path to a readable UTF-8 file describing this review's focus. |
| `images` | no; OMP only | Ordered explicit image InputRefs: `[{"path":"...","sha256":"<64 lowercase hex>","media_type":"image/png"}]`. Supported media are PNG, JPEG, GIF and WebP. Each entry grants only that exact file's hash-bound image bytes; never derive reads from paths inside source/focus. |

Use the actual exposed tool, not a shell command or pseudocode launcher. If managed OMP mounts it
as an XD device rather than a top-level tool, read `xd://adversarial_review` for its schema, then
write the parameter JSON to that URI with the existing `write` tool. This dispatches the same
native tool with the owning tool-call context and cancellation signal; it is not another review
route. If neither presentation is available, report no review rather than installing an override.
The managed native helper owns exact opposite-family routing and canonical preparation/grading;
do not supply a reviewer selector, infer a missing family, or duplicate model constants here.
Original family is trusted routing input, not authenticated authorship. No author session, transcript, caller
hashes or credential input is needed. Missing tool/family/contract, unavailable exact route/auth,
invalid source or over-capacity packet is **no review**, not permission to substitute another path.

**No `request.py`, worker, CLI, `task` wrapping/nesting, parent model switch or fallback to perform
an OMP review.** An authorized subagent calls the same tool itself; do not create another reviewer
agent to reach it. The reviewer receives a fresh canonical packet/instructions, not primary
history or supplied tools. Do not truncate the target or repair a nonconforming reviewer response.

Optional images are native OMP attachments, not a new CLI flag. Relative source/focus/image paths
resolve through the actual owning tool cwd. Source and focus remain UTF-8 text; neither becomes
a binary container. Every declared image must be a genuine regular supported image with the exact
hash and decoded media type. The helper snapshots its immutable bytes and ordered inventory into
the invocation/packet binding; the fresh native context supplies canonical text plus actual image
parts, still with `tools: []`, no parent history and no Responses continuation. Base64 is only the
native image-part encoding, never review text. No OCR, image descriptions, path hints or reviewer
read roots substitute for the actual images.

Image calls require the installed host's supported decoder and a verified native catalog image
token rule, plus an observed whole-input budget covering images, text and native framing. Missing
capability, unknown pricing, bad media/hash, capacity, cancellation or failed image/context retention
fails closed before inference or publication as applicable. No guessed image token cost, text-only
capacity claim, permissive fallback, model switch or new Claude SDK prerequisite is permitted.
Absent `images` preserves the existing text invocation and both legacy CLI transports unchanged.


### OMP — consume the compact result

Only the following fields belong in primary/progress/result details:
`status`, `verdict`, `exitCode`, `counts`, `must_fix_high_count`, `loop_should_continue`,
`caller_family`, `reviewer_provider`, `reviewer_model`, `reviewer_api`, `summary` (at most 1024
characters), `result_path`, `receipt_path`. Missing reviewer observations and unavailable paths
are `null`; do not invent them. Full findings, assessments, packet, captured raw output and native
observation stay in retained evidence. Read nonnull evidence paths only when needed to act on a
finding or diagnose the run; do not inject the whole review into the primary context.

Check `status`, the delivered top-level `verdict` and canonical `exitCode`; native tool completion
is **not approval**. Shared § Exit codes supplies the meanings: `0` approved, `2` usable nonblocking
nonapprove, `4` blocking, `1` no review with `verdict: null`. Missing/unqualified execution,
cancellation, stale ownership, malformed output or failed evidence retention cannot approve.
A null path is not an empty findings list or proof of retention; a nonnull diagnostic receipt
alone is not a qualifying review. No-review counts/must-fix counts may be null; do not interpret
them as zero findings. `loop_should_continue` reports that blocking work remains, not permission
to launch another review. On no-review, stop this invocation and report its result. A later attempt
requires the caller's existing applicable artifact-review policy and remaining allowance; if that
policy or allowance is unavailable, do not retry automatically. Existing limits remain at most two
full attempts/one repair round for documentation or utility artifacts, four/three for qualifying
production artifacts, and zero for temporary/in-flight artifacts. Failed and aborted logical
attempts count; delegation, route or name changes never reset a cap. Use the canonical floor,
counts and loop flag, not caller recomputation or the model's un-floored verdict.

### OMP — cooperative deadline and evidence

The native tool forwards the owning cancellation signal and has a **two-hour per-review abort
deadline**. Do not wrap it in a shorter outer timer or borrow the CLI background/kill recipe.
Cancellation before publication permanently makes the invocation nonqualifying; a late answer
cannot approve. Signal delivery and observed local stream settlement are separate facts: consult
the retained observation when needed, and report missing settlement honestly. Cancellation does
not prove remote GPU stoppage; host death cannot guarantee a receipt or uncaptured/unwritten bytes.

There is no reviewer AgentSession serving/disposal requirement. Required observed native route
metadata qualifies execution, not independent hidden upstream identity. Existing provider
same-route transport/auth/compatibility recovery is allowed; claim neither zero provider HTTP
attempts nor hidden backend identity. No adapter retry, schema repair or model substitution.
Normal managed loading must expose the tool; copying bytes or reloading a transcript is not
public activation proof. Do not restart/resume/rebind the canonical TUI to obtain it.

## Claude Code / Codex — supported CLI surface

Launch accepts `--contract`, `--source` and optional `--prompt-file`; collection
accepts `--collect <handle>`. There is no mode name, positional artifact, `--cwd`,
`--checklist`, wrapper-script fallback or compatibility translation. Unsupported
arguments return exit 1 with a refusal document. The caller, not the tool, chooses
the artifact class.

---

## Calling it — Claude Code / Codex CLI only

Two steps, and **the launch must be BACKGROUNDED**. It prints a handle as soon as
one exists, then stays alive until review and publication finish. A printed handle
does not mean launch has exited. Collect the answer separately under that handle.

Use a background execution lane that permits the complete launch to finish; do
not substitute a shorter foreground tool timeout for the CLI's per-attempt kill
switch. Backgrounding alone does not remove a harness's own lifetime limits.

**What stdout carries depends on how the launch EXITS.** On `0`, `2` and `4` it is the handle
and nothing else. On `1` it is one of three shapes — a leading `{` (the tooling-failure document
itself, with nothing to collect), a leading `disc-` (a real handle, whose failure document is
published under it), or nothing at all. **Do not collect on the first or the third.**

**TWO LANES, AND YOU ARE IN EXACTLY ONE OF THEM.**

- **A harness with a background option** — the Bash tool's, for instance. Use it. Read the
  launch's final exit code from the harness completion record and the first line of its captured
  stdout; keep stderr in the harness's separate diagnostic capture. This lane does not create or
  require `launch.rc` or `launch.log`, and nothing about `&`, `$!` or shell `wait` applies to it.
- **Plain shell.** Use the complete four-line block below in one shell whose execution lane can
  outlive the review's kill switch; its `wait` lasts for the whole review. Read the final exit code
  from `launch.rc` and the first stdout line from `launch.log`. Do not run that blocking block in
  a shorter capped foreground tool call; use the harness-background lane when it is available.

**Keep forbidden forms outside runnable blocks.** The launch examples contain
only the supported commands; the pitfalls below explain what not to combine with
them.

- **KEEP STDOUT AND STDERR APART.** stdout carries the handle and nothing else; stderr carries
  diagnostics. Merging them can put a diagnostic on line 1, and `$handle` then names nothing. So
  `> launch.log 2> launch.err &` — never `> launch.log 2>&1 &`.
- **BARE `wait` LOSES THE CODE.** `&` detaches the status, and `wait` with no operand waits for
  every child and returns 0 whatever any of them exited with — so `launch.rc` reads 0 for a refused
  launch exactly as it does for an approved one. That fails OPEN: a caller guarding on
  *1 = no review happened* never fires, and goes on to collect a handle that may not exist. Save
  `$!` and wait on THAT pid.
- **`wait "$PID"; echo $? > launch.rc` LOSES THE WHOLE RUN UNDER `set -e`.** The shell exits AT the
  `wait`, before the write: `launch.rc` is never written and the collect never runs. Codes `2` and
  `4` are COMPLETED REVIEWS, so that form discards finished work and the missing file then reads as
  *"no review happened"*. Read the status through an `if`, which records every code under `set -e`
  and without it alike.

**Before collecting, read the final launch exit code from YOUR lane's record:** the harness
completion record in the first lane, `launch.rc` in the plain-shell lane. Then inspect that lane's
stdout using the shapes in **What stdout carries depends on how the launch EXITS** above: collect
only when it names a real handle. A leading failure document or empty stdout has nothing to collect.

**EVERY BLOCK BELOW CARRIES A MARKER ON ITS FIRST LINE AND CONTAINS ONLY RUNNABLE LINES.** A caller
told *"use the block from § Calling it"* selects one by that marker instead of judging which lines
are runnable and which of two launches was meant.

### Launch — a harness with a background option

```bash
# discriminator:launch:harness-background
python ~/.agents/skills/discriminator/request.py --contract <artifact-class> --source <path>
```

### Launch — plain shell, and it is ALL FOUR LINES

**Neither the pid line nor the `if` is a refinement of this recipe** — they are what make the exit
code real, so a copy missing either is broken on arrival and silent about it. **Keep all four lines
in ONE shell:** split across two calls, `wait` cannot see the pid, says so loudly and returns
nonzero, which at least fails CLOSED. The bare form there returns 0.

```bash
# discriminator:launch:plain-shell
python ~/.agents/skills/discriminator/request.py --contract <artifact-class> --source <path> > launch.log 2> launch.err &
LAUNCH_PID=$!
if wait "$LAUNCH_PID"; then launch_rc=0; else launch_rc=$?; fi
printf '%s\n' "$launch_rc" > launch.rc
```

### Collect — either lane, and the same answer every time once one is published

```bash
# discriminator:collect
python ~/.agents/skills/discriminator/request.py --collect <handle>
```

Collecting twice is not a second consumption. There is no mode name and no positional argument.

**The sameness begins at publication, not at launch.** Collect before the review has published
and you get a document describing the run's *state* — which is a real answer to "where is this
review", but it is not the review, and a later collect supersedes it. Treating the first thing a
handle returns as final is the mistake this paragraph exists to prevent; a state document says so
in its own recommendation, and its exit code is never the approve code.

| flag | required | what it is |
|---|---|---|
| `--contract <class>` | **yes** | Which kind of artifact this is. Selects the rubric and the contract. Use `none` to declare that no contract applies. **An absent flag is refused, never defaulted.** |
| `--source <path>` | **yes** | What is being reviewed. Its content is inlined into the packet — it is not the reviewer's working directory. |
| `--prompt-file <path>` | no | What to look for in *this particular* review. |
| `--collect <handle>` | — | Fetch the answer for a launched review. |

**Both declarations must be set.** `GAN_DISCRIMINATOR_CALLER_FAMILY` names the caller product;
`GAN_DISCRIMINATOR_CLI` names the reviewer CLI. The product-specific agent definition pins the
pair: `codex` → `claude` in Codex and `claude` → `codex` in Claude Code. Those are the only
accepted values. A missing declaration or equal pair is refused before launch, and there is no
fallback arm. After launch, the selected transport's result metadata must name the expected model
family. A missing, same-family, or unrecognisable identity produces a tooling-failure envelope and
exit `1`, never a plausible review with null provenance.

---

## Artifact classes

`code-service` · `code-fleet` · `vault-raw` · `vault-wiki` · `research-report` · `plan-design` ·
`project-state` · `model-instruction` · `pipeline-template` · `configuration` · `none`

**Declare the real class.** It is not bookkeeping: it decides which findings hold your fix loop
open. See the next section — this is the single most consequential thing a caller gets wrong.

---

## Severity

`critical` · `high` · `medium`. **There is no `low`** — a cosmetic, stylistic or preference finding
is not emitted at all. What each level means is defined in the rubric the reviewer is shown, and
nowhere else; do not restate it in your own pipeline.

---

## The two gates are different gates

Callers conflate these, and conflating them is what makes a fix loop run forever.

**The verdict gate.** `critical`, `high` and `medium` all keep the verdict off `approve`.

**The delivered verdict is floored by the engine.** The model writes its label;
the engine raises it to `fail` for a `critical` or must-fix `high`. The floor only
moves a label **away from `approve`, never toward it**. A model's explicit more-
blocking label is preserved even when the findings alone would permit less.

**The fix-loop gate.** Only `critical` findings and **must-fix** `high` findings hold a loop open.
**A loop is allowed to stop with `medium` findings outstanding, and with non-must-fix `high`
findings outstanding.**

A `high` is must-fix when its category is the declared class's **primary** category. A serious
finding in some *other* respect is real, is reported, and does not block.

> **Read the mark. Do not derive it — and it is NOT on the finding.** `assessments[]` carries one
> entry per finding, joined to it by that entry's `index` into `review.findings[]`, and that entry
> is where `must_fix_high`, `blocking` and `category_matches_primary` live. A finding object has no
> such field, so `review.findings[i].must_fix_high` reads nothing at all. The envelope also carries
> `must_fix_high_count` and `loop_should_continue`, both already computed. A caller that recounts
> severities itself will get this wrong.

**If you declare `--contract none` there is no primary category, so nothing can fall outside it and
every `high` becomes must-fix.** That is the safe direction and it is deliberate — but it means a
loop under `none` stops only when there are no serious findings at all. **If your loop will not
converge, check first that you are declaring a real artifact class.**

---

## What comes back — CLI envelope and retained canonical delivery

This full-envelope table and its handle/fault/run-record fields describe CLI launch/collect and
retained canonical delivery, not the OMP compact tool result. OMP follows its bounded projection
above; retained findings/assessments are read only when needed.

| field | what it is |
|---|---|
| `status` | `reviewed`, or `tooling-failure` / `too-large` meaning **no review happened** |
| `verdict` | `approve` · `needs-attention` · `fail`, or **`null` when no review happened**. **THE KEY IS ALWAYS PRESENT** — branch on its VALUE, never on whether the key is there. **THE ENGINE'S, after the floor. This is the one to act on.** |
| `review.verdict` | **A SECOND VERDICT FIELD, and it is the MODEL'S OWN UNFLOORED label.** It sits beside `review.findings[]`, so a caller that navigates in for the findings can read it by accident. **Do not.** |
| `counts` | findings by severity. **On a no-review envelope these count the tool's own finding, not a model's** |
| `must_fix_high_count` | serious findings that hold your loop open |
| `loop_should_continue` | the answer to "do I iterate again?", already computed |
| `assessments[]` | per finding: `must_fix_high`, `category_matches_primary`, `blocking` |
| `review.findings[]` | the findings themselves, each with a category, a basis, and where it is |
| `tool_findings[]` | the tool's own findings about the RUN — why no review happened, or what it could not read |
| `review_conforming` | whether the reviewer's answer met its required shape |
| `fault` | **no-review envelopes only.** Which fault class this is, as a field rather than as prose: `caller-or-environment` (your invocation was wrong — fix it and relaunch), `unknown-review-state` (the tool cannot know what the review would have said), `review-unfinished` (nothing has failed; it is simply not done). |
| `run_state` | **no-review envelopes only.** Which of the run record's four states produced this document, or `null` where no run existed yet to have a state. |
| `last_frame_at` | **no-review envelopes only.** The run record's own timestamp for its last frame, ISO-8601 with a UTC offset, or `null` if the record holds none or holds something unparseable. **Absolute, so it ages** — compare it against your own clock. Contrast `quiet_seconds`, which is RELATIVE and frozen when the record was last written, so a run that stopped being written to reports the same figure forever. **Neither field is evidence that a run is alive or dead**; this tool watches no process. |
| `quiet_seconds` | **no-review envelopes only.** The record's own last reading of how long it had been quiet, or `null`. See the caveat on `last_frame_at`. |

**Read the delivered top-level `verdict`.** The nested `review.verdict` is the
model's original label and can be less blocking than canonical delivery. It is not
the consumer's approval signal.

**`status` and `verdict` are different fields and neither substitutes for the other.**
`tooling-failure` and `too-large` mean *nothing was reviewed*: `verdict` is `null` — **present as a key, and null as a value, so a caller testing key PRESENCE will read a no-review envelope as a reviewed one** — and
`review.findings` and `assessments` are empty because no model looked at anything.

**They are not empty envelopes, and do not treat them as such.** The tool writes its own
finding about the failure into `tool_findings`, `counts` reflects that finding rather than
reading all-zero, and `loop_should_continue` is unconditionally true. **Check `status`
first, always** — not because the counts would fool you, but because `status` is the only
field that says whether a model reviewed anything at all.

---

## Exit codes

| code | meaning |
|---|---|
| `0` | a review ran and approved |
| `2` | **a review ran, did not approve, and its findings do NOT block — this is a successful call.** Read the envelope. |
| `4` | **a review ran, did not approve, and its findings DO block — also a successful call.** Read the envelope. |
| `1` | no review happened: refused, or the tooling failed |

Exits `2` and `4` are not errors. Treating either as one discards a complete review.

**`4` IS SEPARATE FROM `2` BECAUSE THEY MEAN DIFFERENT THINGS, and a caller that does not know
it discards exactly the reviews that matter most.** `2` is a review that did not approve; `4`
is one that did not approve and whose findings BLOCK. On one shared code a caller cannot tell
them apart at the only interface it has.

**THESE FOUR CODES ANSWER THREE QUESTIONS AND NO MORE: whether a review happened at all, whether
it approved, and whether its findings block.** What no exit code answers is whether the review found
*anything* — read the envelope for that, because a review can legitimately be `needs-attention` with
an empty findings list.

**Migrating from before `4` existed:** a caller testing `rc == 2` decides which it meant.
*Did not approve* is now `rc == 2 || rc == 4`. *Did not approve and does not block* is `rc == 2`
alone, and changes nothing.

**A COLLECT USES THE SAME FOUR CODES FOR THE SAME FOUR MEANINGS, and its `1` is still a
document.** On `1` a collect prints the tool's own no-review answer — what the run's record
says instead of a review — so all four codes are read, not just `0`. **A caller that
discards non-zero collect output discards every review that DID NOT APPROVE**, because `2` and
`4` are exactly those.

---

## Timeouts — Claude Code / Codex CLI only

Each internal CLI attempt has its own **two-hour kill switch**. There is no shared
time budget between attempts. The CLI can make at most two internal attempts;
that does not authorize an additional caller-level review or repair round.

Background the complete launch and allow its internal attempts to finish. A
harness's foreground timeout may be shorter than one attempt, and its background
lane may also have a lifetime limit; use the actual lane contract rather than
assuming background means unlimited. Do not invent a total wall-clock guarantee:
Codex teardown, process-tree termination and durable record writes are outside
its timed wait and can delay return past the configured timeout.

---

## What this tool will not do

- It will not review with a same-family model as a fallback.
- In CLI envelopes and retained canonical delivery, it will not return an empty findings list to
  mean "no review happened" — that case is a `tooling-failure` status carrying an explanatory
  finding in `tool_findings`. OMP exposes the compact no-review result described above instead.
- Published nonempty results are guarded against replacement. CLI empty-file
  replacement and link-less publication fallback do not protect against a
  competing writer; retained state is not swept automatically.
