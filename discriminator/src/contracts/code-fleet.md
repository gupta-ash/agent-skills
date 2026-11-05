ARTIFACT CLASS: code-fleet

This is the code that operates the fleet itself: deploy scripts, git and session hooks, agent
wrappers, and the logic that resolves per-machine configuration. It is machinery, not a product —
it moves configuration onto machines, decides what is allowed to be committed, and starts and
shapes agent sessions. Every agent and every machine depends on it, including boxes nobody is
sitting at. It runs unattended, so when it does the wrong thing there is no human watching, and the
damage is found later, on another machine, by someone who has no idea they inherited it.

GRADED CATEGORIES: truthfulness, stability, usability, flexibility — all four.
PRIMARY CATEGORY: stability.
NOT GRADED FOR THIS CLASS: nothing. No category is switched off here.
FLOOR: code that reports success on an operation that did not happen is `critical`.

Stability is primary because what this code produces is an effect on machines — a file written, a
commit refused, a hook fired, a session configured. Its output is state, not text, and a wrong or
skipped effect leaves a machine quietly different from what everyone believes it to be, so every
later decision made on that machine is made against the wrong state.

WHAT IS A DEFECT HERE

- A step fails and the code carries on as though it had not: an exit status nobody checks, an error
  caught and logged, a failure path that returns success. It then reports applying something it did
  not apply.
- A path, drive letter, home directory or machine name written into code that runs on other
  machines. It works where it was written and does something else, or nothing, everywhere else.
- Something meant to refuse cannot refuse: a gate, hook or check whose failure path lets the work
  through, or which is skipped entirely when the value it reads is absent.
- Work that reaches past its own paths — operating on a whole tree or a whole index where it means
  to operate on its own files, or writing into a deployed copy rather than the source that copy is
  generated from.
- A comment, usage line or header describing what the script used to do. An operator follows it and
  runs the wrong command against a real machine.

WHAT IS NOT A DEFECT HERE

- Vendored or third-party subtrees the fleet merely carries. If nothing in the tree imports or
  invokes it, it is not this class's code and nothing is raised against it.
- Behaviour that differs between machines because the machines genuinely differ. A value each box
  supplies for itself is the design; only a value baked in where it must vary is the defect above.
- Shell where you would have used Python, a helper written out twice, a long function. Where what is
  there is correct, a tidier way to write it is not a finding.
- Manual steps. Parts of this fleet are operated by hand deliberately, and a script that does one
  thing and leaves the next to a human is not thereby unfinished.
