ARTIFACT CLASS: configuration

This review is of a configuration artifact: a settings file, a deployment template, a
per-machine parameter file, or a declarative rule set that changes what a program does
without changing any code. Nobody runs a configuration file. Something else runs differently
because of it — deploy tooling, an agent harness, a hook, a service — usually on several
machines, and usually with nobody watching.

WHAT "WRONG" MEANS HERE: a value that is read successfully and makes whatever reads it do the
wrong thing, or stop, with nothing pointing back at the setting as the cause. Apply this to
any defect the examples below do not name.

GRADED CATEGORIES: stability, truthfulness, usability
PRIMARY CATEGORY: stability
NOT GRADED FOR THIS CLASS: flexibility
FLOOR: per-machine divergence from the declared template → high. A `source_file` +
`line_range` + `verbatim_quote` citation whose quote is not present at that line in that file
→ critical.

Stability is primary because the consumer here is a machine, not a reader. A wrong value does
not argue with you or look wrong — it is accepted, and the damage surfaces later inside a
program whose own code is correct. Prose in a config file is commentary; the values are the
behaviour.

DEFECTS IN THIS CLASS
- A value that points at something that is not there, or is not what it names: a path, a key,
  an environment variable, a command, a referenced file or section.
- A value in the wrong shape or unit for whatever consumes it — a string where a number is
  read, a relative path where an absolute one is required, seconds where milliseconds are
  expected. It parses; it is still wrong at the point of use.
- Two settings that contradict each other, in the same file or across a layer that overrides
  it, so the behaviour that results is not what either statement says.
- A per-machine or per-environment file that drops, renames or re-values a key its declared
  template states, so one machine quietly behaves unlike the rest.
- A comment or description stating behaviour the settings beside it do not produce. This
  fleet reads those as authority and edits the file without re-checking.

NOT DEFECTS HERE
- A value you cannot verify from the material in front of you. A machine-specific path or a
  tuned number is not wrong because it is uncheckable; say nothing rather than speculate.
- Legitimate per-machine difference. Divergence is a finding only against a template or key
  set the artifact itself declares, never against another machine's choices.
- Key ordering, grouping, indentation, quoting style, or the choice of format.
- The absence of validation, a schema, or tests around the file.
