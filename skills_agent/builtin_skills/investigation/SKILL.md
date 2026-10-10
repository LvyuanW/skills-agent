---
name: investigation
description: Acquire and preserve known HTTP(S) sources, and investigate uncertain claims, conflicting evidence, or unexplained behavior.
---

Identify the question or decision the investigation must support. Derive the required evidence from that question and the user's constraints before choosing a method. Include the possibility that the suspected problem is absent. Ordinary source lookup needs no elaborate investigation.

Prefer original sources and established inspection methods. Choose a method whose assumptions can be checked, whose results can be observed and whose failures have limited consequences. Before relying on a custom measurement or classification, check known-answer inputs or independently inspect a sample. Identify the measured object, unit, denominator, scope and method; keep estimates separate from measurements.

Ask whether the same observation could occur if the proposed explanation were false. If so, seek evidence that distinguishes the alternatives or narrow the conclusion. Confirm that an experiment reached the condition it claims to test; setup failure describes the experiment, not the suspected behavior. A precise measurement of a proxy does not establish the property or cause inferred from it.

Keep source locations, relevant versions and execution status with observations. Read enough surrounding material to interpret them and look for contradictory evidence. A partial read, filtered sample or failed search does not establish absence. Preserve full evidence in accessible files when needed; return relevant findings and references. Use the assigned access route, including delegated retrieval for conversation-history analysis.

For a known HTTP(S) URL, resolve [scripts/fetch.py](scripts/fetch.py) against this skill's directory and run it through the existing `shell` tool with Python 3.11 or newer:

```sh
python3 /absolute/path/to/investigation/scripts/fetch.py 'https://example.org/source' --output-dir /absolute/workspace/evidence --max-chars 8000
```

Each call creates a fresh evidence directory containing the complete received body as `body.bin`, `metadata.json` with requested/final URLs, redirect hops, actual HTTP status, response headers, UTC retrieval time and SHA-256, and extracted text when supported. HTTP errors retain their bodies and return failure; interrupted or failed transfers are marked incomplete. Inspect status, completeness and extraction diagnostics before using the preview. Retrieval time is not publication time, and a saved path alone does not establish its contents. Use `shell result_mode=direct` for exact excerpts, or read the preserved files in subsequent calls when the bounded preview is insufficient.

`--extract auto` handles declared HTML, JSON and text; `none` saves the body only. Explicit `text`, `html` or `json` selects extraction when a source's type is incorrect; `--encoding` overrides a known charset. `--max-extract-bytes` limits extraction memory; `--max-chars` limits only the returned preview. `--max-body-bytes` defaults to 64 MiB: exceeding it preserves only the acquired prefix and reports an incomplete failure. Raise it explicitly for a source that needs more. HTML extraction normalizes whitespace and omits scripts, styles and templates: it can include hidden or navigation text and cannot establish the rendered page, JavaScript content or complete article meaning. JSON extraction validates without reformatting the decoded source. The raw response remains authoritative.

The helper performs GET for known URLs; it does not search the internet, log in, execute page scripts or install readers. It sends no authorization/cookies/custom headers and does not inherit environment proxy credentials. Credential-bearing URLs and redirects are rejected. `--timeout` applies one deadline across redirects, response headers and body transfer; operating-system DNS lookup can exceed socket timeouts. If access or extraction fails, state the specific limitation and use an available appropriate access route; do not invent search results or unseen source contents. Use `--help` for the full interface.

Distinguish observations, assumptions and conclusions. Check the evidence for each step from a finding to a recommendation; certainty does not carry automatically between them. Revise the explanation when evidence conflicts. Seek an independent check when a consequential uncertainty remains; agreement alone is not evidence. Continue only when another check can materially inform the decision. Stop when the question is supported sufficiently, or state what remains unknown without expanding the task or claiming more than the evidence shows.
