ARTIFACT CLASS: project-state

This review is of a project-state record: a checkpoint, a living doc, a decision log, a
roadmap, or another document a project keeps so the work can be picked up later. Nobody
executes it. It is read back as authority — by the agent resuming this project once the
session that did the work is gone, by a peer deciding whether something is settled, and by the
operator reading where things stand. What it says becomes what everyone believes, because the
sessions that could have contradicted it are over.

WHAT "WRONG" MEANS FOR THIS CLASS — apply it to any defect not named below: the record tells a
later reader something about this project that is not so, and they act on it with no way left
to find out.

GRADED CATEGORIES: truthfulness, usability, stability
PRIMARY CATEGORY: truthfulness. This is the class's assigned primary category in the tool
itself, and it is the right one: the artifact does nothing but assert, and its value is that a
reader trusts it without re-deriving it. A false assertion here is the record failing at the
only job it has.
NOT GRADED FOR THIS CLASS: flexibility.
FLOOR: a false claim that will drive a real decision is `critical`.

DEFECTS IN THIS CLASS
- A status claiming more than was done: something marked done, verified, closed or measured
  when that work was never carried out, or the evidence cited for it does not exist or does
  not say that.
- A decision, constraint or measurement written down with the wrong outcome — the record says
  a thing was chosen, ruled out or measured at some value, and the artifact it points at says
  otherwise.
- Two parts of the record disagreeing about the same fact, with nothing saying which is
  current: a summary line against the entry it summarises, a table against the prose.
- A pointer that does not resolve — a file, path, ID, decision number or section the reader is
  sent to that is not there, or is not about what it is cited for.
- A resume procedure or next-step list that cannot be carried out as written: it assumes state
  that does not exist, or a step names no target the reader can identify.
- Something the project genuinely settled missing from the place this record says holds it, so
  the next reader takes it as open and redoes or contradicts it.

NOT DEFECTS HERE
- Being a snapshot. The record describes the project as of when it was written; that later
  work has happened is not a defect unless it claims to be current and is not.
- Open questions, unfinished sections, and work marked in progress or undecided. A record
  saying plainly that something is not settled is doing its job.
- Thin rationale. A decision recorded without the full argument behind it is terse, not false,
  unless the record claims a rationale it does not carry.
- Length, repetition, ordering, or that another document or format would have held this better.
