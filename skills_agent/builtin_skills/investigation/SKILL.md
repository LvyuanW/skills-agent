---
name: investigation
description: >-
  Use to resolve conflicting evidence or uncertain causes with discriminating
  observations, or to capture and locate source evidence with version and completeness
  records. Skip straightforward lookups needing no source capture or comparison,
  routine edits, and conversation-history retrieval assigned to the history skill.
---

Choose an observation that can distinguish the plausible explanations, including that the suspected problem is absent. Check that an experiment reaches its intended condition. For a custom measurement, identify the object, unit, denominator and method; check a known-answer input or inspect a sample before relying on it. A proxy measurement alone does not establish the inferred cause.

Retain source locations and relevant versions with observations. Read enough context to assess contradictory evidence; a filtered sample or failed search does not prove absence. Keep observations, assumptions and conclusions distinct. A verified quotation establishes its source, not that it supports the attached conclusion: check the claim's scope, alternative explanations and any inference from correlation to cause. Obtain a discriminating observation or narrow the claim when support is insufficient. Stop when further checks would not change the decision. Ordinary source lookup needs no elaborate investigation. Conversation history follows its assigned delegated retrieval route.

Read [references/sources.md](references/sources.md) when the next decision needs a captured source, a precise location, or replay of an existing citation. [scripts/fetch.py](scripts/fetch.py) acquires known HTTP(S) sources. [scripts/source.py](scripts/source.py) captures local files, imports those fetch records, locates text/JSON/CSV/log content and verifies saved bindings. Use absolute script paths with Python 3.11 or newer and `shell result_mode=direct`; these references can be reused or adapted.

Reuse adequate source records. Capture only what the task needs; no separate evidence document or final audit is required. Distinguish raw snapshots from derived text and parsed values. Multiple matches require an explicit location; whitespace-normalized matches are candidates, not verbatim quotations. A previous snapshot remains historical evidence when its origin changes. Tool results do not assess semantic support, and missing content in a partial acquisition or selected range does not establish absence from the full source.
