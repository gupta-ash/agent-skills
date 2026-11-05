# vault-raw — a raw ingested note in a knowledge vault

A raw note is the fleet's captured copy of something published somewhere else: an article, a
paper, a thread, a transcript, a page of documentation. Its job is to hold what that source said,
faithfully enough that nobody has to go and fetch the original again. Compiled wiki notes quote
it, strategy documents reason from those, and agents answer from the result — and nobody re-checks
a raw note against its source after capture, so an error introduced here is invisible from
downstream and is repeated with growing confidence.

## The category that matters here

`truthfulness` is the primary category for this class, and it is the only one graded. NOT GRADED:
`stability`, `usability`, `flexibility`. A note does not run, and one that is awkward to read costs
a reader a minute — while one that misreports its source corrupts everything built on top of it.

Read `truthfulness` here as FIDELITY TO THE SOURCE, not as correctness of the claim. A raw note
records what the source said, including where the source is wrong: misquoting the source is the
defect, disagreeing with it is not.

FLOOR: a claim attributed to the source that the source does not make is `critical`.

## What is a defect here

- A quotation that does not match the source's words, or a figure, date, name or number that
  differs from the one the source gives.
- Something presented as the source's that the source does not say — the capturer's own
  inference, summary or opinion written in the source's voice.
- A claim stripped of the hedge, condition or scope the source attached to it, so that it reads
  as broader or more certain than what was actually published.
- Provenance that does not resolve: a URL, title, author or date naming something other than the
  material the note actually contains, or naming nothing at all.
- A passage marked as verbatim capture that has in fact been edited, reordered or abridged, with
  nothing saying so.

## What is not a defect here

- The source being wrong, biased, out of date or badly argued. Recording that faithfully is the
  note doing its job.
- Missing analysis, commentary or cross-links to other notes. That work belongs to the compiled
  notes built from this one, not to the capture.
- An incomplete capture — part of the source not taken — provided the note does not present
  itself as the whole of it.
- Formatting: heading levels, frontmatter field order, wrapping, tag style, filename.
