ARTIFACT CLASS: pipeline-template

This review is of a pipeline or workflow template — a document or script that ORCHESTRATES other steps
rather than doing the work itself. It names the stages and their order, says what each is handed and
where each writes, and says what happens when one fails. An orchestrating model or a runtime drives real
subagents against a real tree from it, and the outputs it names are read by later stages and by
consumers who never see this file.

WHAT "WRONG" MEANS FOR THIS CLASS: carried out as written, the run does not do what the template says it
does — a stage is skipped, handed the wrong thing, or reports done when it did nothing — and because
each stage's output is the next one's input, the damage is silent and travels.

GRADED CATEGORIES: stability, usability, truthfulness
PRIMARY CATEGORY: stability
NOT GRADED FOR THIS CLASS: flexibility

Why stability is primary here: a template exists to be EXECUTED, and it is read once by a machine that
will not stop to ask a question. A wrong step produces artifacts that look exactly like right ones.

FLOOR: a stage that reports success on an operation that did not happen is `critical`.

WHAT COUNTS AS A DEFECT HERE
  - A stage whose success is declared rather than established — it announces completion on a path where
    its work did not run, its command failed, or it wrote nothing.
  - A stage that consumes a file, field or handle no earlier stage produces, or produces one under a
    name or shape different from the one the consuming stage reads — including where that mismatch lets
    a stale artifact from an earlier run stand in for this run's output.
  - A step with no stated behaviour on its failure path, so the driver must invent whether to stop,
    retry, or carry on to the next stage.
  - A step the driver cannot carry out from the template text alone: no target that resolves to exactly
    one thing, or no stated position in the execution order.
  - Two places instructing the driver differently about the same step — its order, its destination, or
    which stage owns a write.
  - A skill, agent type, script, path or prior stage the template names that does not exist, or does not
    do what the template says it does.

WHAT IS NOT A DEFECT HERE
  - That a new stage or a new shape would be awkward to add. Not graded on this class: do not raise it.
  - Absent detail about the INTERNALS of a step the template calls. You are reviewing the orchestration,
    not the skill or agent it delegates to; a named, existing step is a black box the template is
    entitled to treat as one.
  - That the template does not handle a condition it never claims to handle — no resume, no parallel
    execution, no partial re-run — unless the template itself says it does.
