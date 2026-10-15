---
name: investigation
description: >-
  Use to resolve conflicting evidence or uncertain causes with discriminating
  observations, or to capture known HTTP(S) sources with provenance and completeness
  records. Skip straightforward lookups needing no source capture or comparison,
  routine edits, and conversation-history retrieval assigned to the history skill.
---

Choose an observation that can distinguish the plausible explanations, including that the suspected problem is absent. Check that an experiment reaches its intended condition. For a custom measurement, identify the object, unit, denominator and method; check a known-answer input or inspect a sample before relying on it. A proxy measurement alone does not establish the inferred cause.

Retain source locations and relevant versions with observations. Read enough context to assess contradictory evidence; a filtered sample or failed search does not prove absence. Keep observations, assumptions and conclusions distinct, and stop investigating when further checks would not change the decision. Ordinary source lookup needs no elaborate investigation. Conversation history follows its assigned delegated retrieval route.

For a known URL, [scripts/fetch.py](scripts/fetch.py) is a reference for acquisition with provenance. Read or adapt it, reuse it directly, or write a reader appropriate to the source. Direct use requires Python 3.11 or newer and the path resolved relative to this SKILL.md:

```sh
python3 /absolute/skill/scripts/fetch.py 'https://example.org/source' --output-dir /absolute/workspace/evidence --max-chars 8000
```

The helper saves the received body, request/final URLs, redirect hops, HTTP status, headers, UTC retrieval time and SHA-256 in a fresh directory. Failed or interrupted transfers remain marked incomplete; HTTP error bodies are retained with failure status. Inspect status, completeness and content before quoting the source. Retrieval time is not publication time.

`--extract auto` handles declared HTML, JSON and text; use `none` to save bytes only or an explicit type/encoding for known incorrect metadata. `--max-chars` limits the preview, `--max-extract-bytes` bounds extraction memory, and `--max-body-bytes` defaults to 64 MiB. Exceeding the body limit preserves a prefix and reports failure; raise it only when the task needs more. HTML extraction omits scripts/styles/templates and normalizes whitespace; it does not establish rendered or JavaScript content. JSON extraction validates without reformatting. The raw response remains authoritative.

This helper fetches known URLs; it does not provide search, login or browser execution. It sends no authorization, cookies or custom headers, does not inherit environment proxy credentials, and rejects credential-bearing URLs and redirects. Its timeout spans redirects, headers and body transfer, but OS DNS lookup may outlast socket timeouts. For unsupported access, use an appropriate authorized route or report the limitation. Choose `result_mode: direct` for exact excerpts; inspect saved content when a preview is insufficient.
