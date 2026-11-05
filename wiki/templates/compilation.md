---
type: compilation
artifact_role: {artifact_role}
title: "{title}"
concept: "{concept}"
domain: {domain}
topic: {topic}
tags: [{tags}]
source_ids: [{source_ids}]
sources: {source_count}
{contributing_field}: [{contributing_labels}]
compiled_from: {compiled_from}
vault: "{vault_name}"
author: "{generator}"
pipeline_id: "{run_id}"
created: {date}
updated: {date}
last_compiled: {date}
last_verified: null
status: draft
contradictions: 0
open_questions: 0
freshness: monthly
human_edited: false
confidence: {confidence}
supersedes: ""
related: []
---

# {title}

<!-- Resolve artifact_role to topic_distill or synthesis. Emit exactly one of
contributing_books (all-book) or contributing_sources. compiled_from is a list
of objects {slug, source_class, citation_count} identifying direct raw evidence.
Preserve source authors in provenance, not in place of generator identity.
Never assert verification on a draft. Publication sets actual accepted fields
using the resolved canonical version's date/null (3.0) or UTC (3.1) contract. -->

## Scope and Source Identity

[Write factual scope/source identity assertions here once, each atomic, marked and
directly cited. Separately evidence origin_count, independent_lineage_count and
single_origin/cross_origin scope; never infer independence from chapter/file count.]

## Overview

[Detailed grounded explanation of this topic, not an executive-summary substitute.
Give each factual statement one marked/directly cited home in the claim sections;
retain full conditions, qualifications and conflicts without repeating them below.]

## Key Findings

- [Claim with exact qualification and atomic marker] [PARAPHRASE] ^[[[raw-source-slug]]]
- [Grounded inference and its limits] [INFERENCE] ^[[[raw-source-slug]]]

## Examples, Numbers and Qualifications

[Place each worked example, figure, quote, condition and exception here only if it
has not already been fully stated in another claim section; otherwise link its
existing location. All new factual assertions need atomic markers/direct raw cites.]

## Comparison Matrix

| Source / approach | Claim and conditions | Evidence |
|---|---|---|
| [Source identity claim location] | [Existing claim anchor; do not repeat it] | [Claim/evidence binding location] |

## Contradictions

[Source-specific disagreements with both/all opposing raw citations. An actual
absence finding is its own marked, directly cited claim, not count boilerplate.]

## Open Questions

[Separate marked/directly cited UNKNOWN gaps and limitations. Retain exact
qualifications without repeating claims already given elsewhere.]

## Coverage

[Reference existing claim headings/anchors and declared coverage.json entries and
scope rules. No repeated facts, source counts, conditions, unknowns or examples.
Keep all declared unit/topic accounting in the bound coverage artifact.]

## Sources

[Direct raw evidence links with actual source authors/locators; navigation is not evidence.]

## Verification Notes

[Reference affected claim locations and declared citation-bindings.json or
repair-receipt.json. Do not repeat factual narrative or claim earned acceptance.]
