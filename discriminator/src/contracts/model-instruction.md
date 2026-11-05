ARTIFACT CLASS: model-instruction

This review is of text whose only reader is a model: a system prompt, an agent definition, a
skill body, a prompt overlay. Nothing executes it and nothing type-checks it — an agent loads
it before doing any work and then acts on whatever it found there. What depends on it is every
task those agents carry out unsupervised, and an operator who will not be present at the moment
a misreading becomes work done the wrong way.

GRADED CATEGORIES: usability, truthfulness, stability
PRIMARY CATEGORY: usability
NOT GRADED FOR THIS CLASS: flexibility
FLOOR: a contradiction between two instruction layers is `high`.

Usability is primary because the costly failure of this class is a reader who cannot tell what
is being asked. Such text can be entirely true and still leave an agent at a fork the words do
not settle — and a model that guesses leaves no trace of having guessed: no error, no failed
step, just confident work in the wrong direction. A person who finds a document ambiguous asks
someone. This reader cannot, and nobody is watching at the moment it decides.

A defect here is anything that leaves the next model unable to act as the author intended:
  - Two passages telling the reader to do different things in the same situation with nothing
    saying which wins — including a later section quietly reversing an earlier one.
  - A named path, file, command, flag, tool or skill that does not exist, or that does not
    behave the way the text says it behaves.
  - A step with a trigger but no action, or an action with no way to tell when it is done, so
    the reader has to invent the missing half.
  - An instruction that, followed exactly as written, does the wrong thing: writes to the wrong
    place, discards work, or claims something was done that was not.
  - A rule stated as absolute whose real scope is narrower, so the reader applies it somewhere
    the author never meant it to reach.

These are NOT defects here, and reporting one is itself an error:
  - Tone, bluntness, ordering, or repetition. This kind of text repeats on purpose; emphasis is
    a working technique here, not redundancy to be tidied away.
  - Strictness. Narrowing what a model may do is the author's decision, and is frequently the
    whole purpose of the file.
  - That some other wording, structure or example would prompt better, when the text as written
    can be followed.
  - Silence about a situation outside what the text sets out to cover.
