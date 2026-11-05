---
type: source
artifact_role: capture
source_ids: [{source_ids}]
source_class: {source_class}
provenance_class: {provenance_class}
raw_modality: {raw_modality}
source_hash: "{source_hash}"
source_hash_kind: {source_hash_kind}
source_hash_algorithm: sha256
content_hash: "{content_hash}"
vault: "{vault_name}"
pipeline_id: "{run_id}"
status: draft
title: "{title}"
author: "{actual_generator}"
source_authors: [{source_authors}]
source: "{source_identifier}"
ingested: {date}
backfill: {backfill}
archive_url: {archive_url}
url: "{url}"
date: {date}
source_type: {source_type}
domain: {domain}
topic: {topic}
tags: [{tags}]
depth: {depth}
parent_source: {parent_source}
raw_archive: "{raw_archive}"
compiled_into: []
related: []
unfetched_links: []
freshness: monthly
last_verified: null
human_edited: false
confidence: high
verification_notes: ""
---

# {title}

> Source: [{source_type}]({url}) | Raw: [[{raw_archive}]]

## Faithful Source Capture

[Preserve the full accessible source or link its immutable full content_path.
Keep source authors, quotes, numbers, units, conditions and contradictions exact.
Do not replace raw evidence with generated interpretation. Detailed analysis
belongs in a separate type: compilation, artifact_role: topic_distill artifact.]

## Source Units and Assets

[Record original locators and capture-inventory units. Preserve image context,
OCR uncertainty, missing/unavailable units and actual extraction diagnostics.]

<!-- Emit source_url for web/thread/video and source_file for document/book from
actual acquisition metadata. Keep optional unavailable values absent with a
diagnostic; schema-required unavailable data blocks. A corpus-authoritative
source_sha256/source_hash_authority is copied separately, never recomputed here. -->

## Connections to Existing Knowledge

- Extends [[related-compilation]] — how this extends existing understanding
- Related to [[related-source]] — connection description

## Scope and Capture Limitations

[State partial scope/unavailable evidence explicitly; capture does not verify truth.]

## Source Limitations

[Note any limitations: source age, author bias, missing data, scope limitations.
If none identified, write "None identified."]

## Sub-Sources

- [[sub-source-slug]] (depth: 1) — description

## Unfetched Links (depth 2)

- url — "context where link was found" — unfetched
