---
name: investigation
description: >-
  Use when results disagree, a change has no effect, or you need to clarify what
  a value means before continuing. Also use when you need a fair comparison to
  choose between explanations or methods. Decide what to check and use the result
  to guide the next step. This skill can also save a known web source with its
  retrieval record. Skip ordinary lookups and edits when the answer is clear and
  no saved source is needed. Use the assigned route for conversation history.
---

# Investigate a question that affects the work

Start with the question that would change your next action. It may concern a result you cannot explain, two sources that disagree, the meaning of a value, or which method to use. Choose a check that can help settle that question. You do not need to wait for an error, and you do not need to invent alternatives when the answer is already clear.

## Choose a check or comparison

Use existing logs, source data or measurements when they answer the question. For an unexplained result, consider plausible explanations, including the possibility that the suspected problem is absent. Choose an observation whose possible results would distinguish them.

When choosing between methods, keep the current usable work and compare relevant candidates in a suitably sized trial. Use the same task inputs, constraints and basis of comparison. Check the property that matters to the choice, then use the result to decide which method to continue with.

For a mismatch, choose a check that fits the observed pattern:

- If several changes leave the same mismatch, check a condition they all rely on before trying another similar variation.
- If the mismatch is systematic, compare processing stages or input conditions to find where the results first differ.
- If a search over smoothly varying parameters stalls, checking a boundary may reveal an untested convention or a discrete change in behavior.

These are alternative approaches, not required steps. Before interpreting an experiment, confirm that it actually reaches the condition you intended to test. If it does not, repair the trial before drawing a conclusion. If the result still cannot distinguish the explanations or methods and the decision matters, choose a different observation.

## Check what the values mean

Check definitions that could affect the comparison, such as the object, unit, denominator, time, coordinates or aggregation. A period total and a value at the end of that period may both be correct. Recalculating them does not establish which one the task requires.

Resolve the definition from the task, source or established contract before treating the difference as a code defect. Preserve the identifiers and coordinates needed through later transformations. If the evidence does not settle the definition, state the remaining ambiguity; do not choose a meaning just because it makes the numbers agree.

Before relying on a custom measurement, establish these definitions and the measurement method. Then inspect a sample or check an input with a known answer. Two implementations that share an uncertain definition do not resolve that uncertainty. An indirect measurement alone does not prove the suspected cause.

## Use the result to continue the work

When evidence identifies a defect within the task's scope, repair that part and preserve unaffected work. When a definition is resolved, apply it to the affected transformations, calculations and comparisons. If a comparison supports changing methods, retain the work that remains useful. Update the saved outputs that depend on the change, then read or run them to confirm that the change took effect.

When evidence contradicts or limits an explanation, withdraw or narrow it and change the next action accordingly. Keep observations separate from assumptions and conclusions. Save source locations and relevant versions with observations. Read enough surrounding context to assess contradictory evidence: a filtered sample or an unsuccessful search does not prove that something is absent.

Stop investigating when another check would not change the decision, and continue the task.

## Capture a known source

[scripts/fetch.py](scripts/fetch.py) shows how to save a source with its retrieval record. Use it directly, adapt it, or write a reader suited to the source. Direct use requires Python 3.11 or newer. Resolve its absolute path from this SKILL.md:

```sh
python3 /absolute/skill/scripts/fetch.py 'https://example.org/source' --output-dir /absolute/workspace/evidence --max-chars 8000
```

The helper saves the received body, request and final URLs, redirects, HTTP status, headers, UTC retrieval time and SHA-256 in a fresh directory. It marks failed or interrupted transfers as incomplete and keeps HTTP error bodies with their failure status. Check status, completeness and content before quoting the source. Retrieval time is not publication time.

`--extract auto` uses the declared HTML, JSON or text type. Use `none` to save bytes only, or supply the type or encoding when the metadata is known to be wrong. `--max-chars` limits the preview, `--max-extract-bytes` limits extraction memory, and `--max-body-bytes` defaults to 64 MiB. If the body exceeds that limit, the helper keeps the received prefix and reports failure. Raise the limit only when the task needs more.

HTML extraction removes scripts, styles and templates and normalizes whitespace. It does not show rendered content or content produced by JavaScript. JSON extraction validates the response without reformatting it. Use the raw response as the source of record.

The helper fetches known URLs; it cannot search, log in or run a browser. It sends no authorization, cookies or custom headers, does not inherit environment proxy credentials, and rejects URLs and redirects that contain credentials. Its timeout covers redirects, headers and body transfer, but operating-system DNS lookup may outlast a socket timeout. If the source needs unsupported access, use an appropriate authorized route or report the limitation.

Use `result_mode: direct` for exact excerpts. Read the saved content when the preview is not enough.
