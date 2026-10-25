---
name: investigation
description: >-
  Resolve competing explanations with observations that distinguish them, or locate
  exact source passages with ambiguous-match and version checks. Useful when choosing
  a source or method that actually matches the input, or reusing citations after sources
  change. Simple lookups need no capture workflow; conversation history uses the history skill.
---

For source-location work, [source.py](scripts/source.py) accepts a direct file and quote; no capture or selector document is needed first:

```sh
python3 /absolute/skill/investigation/scripts/source.py locate --file /work/service.log --quote 'startup complete'
```

For several quotes, use `locate --batch -` with a JSON array on stdin. Each entry names its `file` (or an existing captured `source`) and a selector, for example `{"file":"/work/service.log","quote":"startup complete"}`. One invocation reads each source version once and returns exact locations or unresolved matches. Add `--store /work/evidence` only when snapshots and replayable bindings are useful; the returned saved-result path can be passed to `verify --bindings PATH --check-origin`. Without `--store`, direct file lookup is read-only and identifies consumed bytes but saves no historical snapshot. Existing `--source` / `--request` calls remain available; `--request -` reads stdin too.

Choose an observation that can distinguish the plausible explanations, including that the suspected problem is absent. Check that an experiment reaches its intended condition. For a custom measurement, identify the object, unit, denominator and method; check a known-answer input or inspect a sample before relying on it. A proxy measurement alone does not establish the inferred cause.

Useful distinctions include the configured runtime versus the library actually executing, a method for the same dataset/version versus a merely similar example, and two explanations that fit the same aggregate metric but predict different individual observations. A second implementation can share the first one's mistaken measurement definition. Source matching and targeted execution can be more informative than another agreement check; neither guarantees that one explanation is uniquely correct.

Retain source locations and relevant versions with observations. Read enough context to assess contradictory evidence; a filtered sample or failed search does not prove absence. Keep observations, assumptions and conclusions distinct. A verified quotation establishes its source, not that it supports the attached conclusion: check the claim's scope, alternative explanations and any inference from correlation to cause. Obtain a discriminating observation or narrow the claim when support is insufficient. Stop when further checks would not change the decision. Ordinary source lookup needs no elaborate investigation. Conversation history follows its assigned delegated retrieval route.

Read [references/sources.md](references/sources.md) when the next decision needs a captured source, a precise location, or replay of an existing citation. [scripts/fetch.py](scripts/fetch.py) acquires known HTTP(S) sources. [scripts/source.py](scripts/source.py) captures local files, imports those fetch records, locates text/JSON/CSV/log content and verifies saved bindings. Use absolute script paths with Python 3.11 or newer and `shell result_mode=direct`; these references can be reused or adapted.

Reuse adequate source records. Capture only what the task needs; no separate evidence document or final audit is required. Distinguish raw snapshots from derived text and parsed values. Multiple matches require an explicit location; whitespace-normalized matches are candidates, not verbatim quotations. A previous snapshot remains historical evidence when its origin changes. Tool results do not assess semantic support, and missing content in a partial acquisition or selected range does not establish absence from the full source.
