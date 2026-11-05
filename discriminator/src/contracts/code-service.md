ARTIFACT CLASS: code-service
GRADED CATEGORIES: stability, usability, flexibility, truthfulness
PRIMARY CATEGORY: stability
NOT GRADED FOR THIS CLASS: none — all four are graded here.
FLOOR: a comment, docstring or header describing behaviour the code does not have → high

WHAT THIS IS, AND WHO DEPENDS ON IT

You are reviewing a service, library or application module — code that runs as part of a working
system. Its callers are other code, not a person. What it returns gets stored, passed on and
acted upon by machines, with nobody reading along. Depending on it are the engineers who will
call it without reading it, and the operator who finds out later, from the state it left behind.

WHY stability IS THE CATEGORY THAT MATTERS MOST HERE

This artifact's whole job is to behave correctly when something invokes it. A false sentence in a
document costs something when a reader believes it; a wrong result here costs something on every
execution and is downstream before anyone notices. So the question to hold throughout: given an
input this code will actually be handed, does it give the wrong answer, stop, corrupt state, or
report success on an operation that failed?

WHAT IS A DEFECT IN THIS CLASS

  - A path that returns a wrong value, or leaves data half-written, for an input a real caller
    in this system supplies. Name the input and name the wrong outcome.
  - A failure that is swallowed, defaulted past, or logged and continued, so the caller is told
    the operation succeeded when it did not.
  - A comment, docstring or header describing behaviour the code does not have. Someone calls it
    on the strength of that sentence and writes a bug they cannot see.
  - A change in what a function does that its existing callers in this system were not updated
    for — this module is part of a system, and its callers are the system.
  - Something a caller must know in order to use it correctly that is stated nowhere: a required
    call order, an argument that gets mutated, a result that may come back absent.

WHAT IS NOT A DEFECT HERE

  - Vendored or third-party subtrees. Code the owning team neither wrote nor calls is out of
    scope, and so is judging the project's own rules against it. Skip those files.
  - Generated files and build output. The artifact is the generator, not what it emitted.
  - Work this code deliberately leaves to its caller and says that it does.
  - A wrong outcome that needs an input no caller in this system produces. If you cannot name
    the caller and the input, it is not a finding here.
