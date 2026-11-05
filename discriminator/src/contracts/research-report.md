ARTIFACT CLASS: research-report

WHAT THIS IS AND WHO DEPENDS ON IT
A research write-up making factual claims from sources — the output of a research stage, or a
document filed under docs/research/. Its readers are the next stage of a pipeline and the people
who act on what it concludes. Almost none of them will open the sources; they take this document's
word for what those sources contain. That borrowed trust is the whole value of the artifact.

GRADED HERE: truthfulness, usability.
NOT GRADED HERE: stability, flexibility.
PRIMARY CATEGORY: truthfulness.
FLOOR: this class states none of its own. The scale as given already grades this class's worst
defect correctly, and restating it here would put one instruction in two places.

WHY TRUTHFULNESS IS PRIMARY
Read from the tool's own mapping rather than chosen here: build/v1/envelope.py, PRIMARY_CATEGORY,
the entry `"research-report": "truthfulness"`. It is also the right one. This artifact does not
run, holds no state and breaks nothing by executing. Its one expensive failure is being believed
while wrong — a report that is hard to follow costs a reader some time, and a report that misstates
what a source said costs whatever decision was built on it.

DEFECTS IN THIS CLASS
- A claim attributed to a source that the source does not make. The source is real; the sentence
  hung on it is not in it.
- A citation that cannot be resolved — a document, section, file or figure that does not exist, or
  that exists and is about something else.
- A number, date, quantity or quoted line that does not match its source, or that changes meaning
  in transit: measured for one thing, reported as another.
- A conclusion or recommendation the report's own evidence does not reach. Every source can be
  quoted correctly and the step from them to the recommendation still be a leap.
- Certainty the evidence does not carry — one undated post or a single unreplicated result written
  in the voice of a settled fact, or a source's own hedge dropped on the way in.
- No way for a reader to tell which statements are sourced and which are the author's own
  inference, because the report never distinguishes them.

NOT DEFECTS IN THIS CLASS
- Reaching a conclusion you would not have reached, where the evidence shown genuinely supports
  what is written. Ambiguous evidence read defensibly is not a false claim.
- Faithfully reporting a source that is itself wrong or disputed, where the report attributes the
  claim rather than adopting it as its own.
- A gap the report declares — an open question, an area marked uncertain, a topic stated as out of
  scope. "I could not establish this" is the report doing its job.
- How the research was carried out: which tools were used, how many sources were consulted, in what
  order. This artifact is judged on what it asserts.
