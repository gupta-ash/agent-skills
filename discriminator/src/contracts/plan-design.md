ARTIFACT CLASS: plan-design

This review is of a plan or a design document that someone else will build from — an
architect's plan, a design spec, a document under a project's `docs/plans/`. It is not the
thing being built. Two readers depend on it: the peer or agent who executes it step by step,
and the person who signs it off first. Both rely on it being true without going back to the
codebase to check, and that reliance is the whole reason a plan saves anyone work.

WHAT "WRONG" MEANS HERE — apply this to any defect the list below does not name:
the document sends its reader somewhere the world is not. Either it asserts something
about the code, the data, an interface or an earlier decision that does not hold, or it
describes work the reader cannot actually carry out from the text in front of them. The
cost is the same either way — the build starts, and the ground gives way partway through.

GRADED CATEGORIES: truthfulness, stability, usability, flexibility — all four. Nothing is
out of scope on this class for category reasons.

PRIMARY CATEGORY: truthfulness. This is read from the tool rather than chosen: `envelope.py`
holds the class-to-primary map, and its entry is `"plan-design": "truthfulness"`. It is the
right one, because the defect this class produces most often is a plan that cites a file, a
line, a schema or a prior decision which is not what it says it is — and the reader has no
way to catch it, since the citation is exactly the reason they stopped checking.

FLOOR: a false premise the plan is built on → `critical`; a schema, file, interface or prior
decision the design names that does not exist, or does not say what the design claims
→ `critical`; two sections stating incompatible requirements for the same thing
→ `critical`; a step with no stated behaviour on its failure path → `critical`; any other
instruction the executing peer cannot carry out from the plan text alone — no edit target
that resolves to exactly one file, or no stated position in the execution order, among them
→ `critical`. Illustrative, not exhaustive: the covering definition at the top of this
contract governs a defect this list does not name → `critical`, identically to the named
shapes above. The floor pins severity and leaves category to the STEP 1 order in the
reviewer's preamble, because on this class a `critical` is neither suppressed nor
re-routed by it.

THINGS THAT ARE DEFECTS IN A PLAN
- It names a file, function, schema, interface, flag or prior decision that does not exist,
  or that does exist and does not say what the plan claims it says.
- It rests on a premise about how the system works today that is not true.
- Two parts of it require incompatible things of the same component, so a builder who
  satisfies one breaks the other.
- A step cannot be executed from the text alone — the edit target does not resolve to a
  single file, or the step's position in the order is never stated.
- A step that can fail says nothing about what happens when it does, leaving the executor
  to invent the failure path.

THINGS THAT ARE NOT DEFECTS HERE
- The plan contains no working code and no finished artifact. That is its normal state,
  not an omission.
- Work the document explicitly defers, alternatives it deliberately leaves open, or
  questions it names as unanswered. A gap the plan declares is the plan doing its job.
- A design you would have approached differently, where the approach actually written down
  is sound and does what was asked of it.
- Length, repetition, or the order the sections happen to come in.
