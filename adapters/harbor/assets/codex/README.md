# TB4 Codex / DeepSeek integration

This profile uses the upstream Harbor Codex adapter with a pinned runtime and
catalog profile plus explicit parent/child session accounting. It can
upload a pinned official native runtime without package installation in every
task, and upload an experiment-derived Flash/max-only model catalog into the
isolated Codex home. The published source catalog is preserved separately.
Harbor still handles task skills, MCP servers, native trajectories, ATIF export,
timeouts and secret cleanup.

## Frozen configuration

- Harbor: `aad19e235922ee5007ac635a96a1c2f72eef9dfc` / `0.24.0`.
- Codex CLI: `0.149.1`, official npm `0.149.1-linux-x64` package.
- Model: `deepseek-v4.1-flash`; reasoning effort: `max`.
- Built-in web search: `disabled`.
- Context: official catalog `1048576` tokens; native output and compaction policy.
- Original official snapshot: `official-models.json`, SHA256 `bce20f679495809f4fa6e48672b6dcbab84b6adff5251890b6e6a21eddb9c12f`.
- Experiment-derived catalog: `models.json`, SHA256 `9d11eb2daa7d6732162a6b6fc304a47f55681a25e4fe64ede864d482149a944e`.
- Runtime SHA512 and source metadata: `catalog-source.json`.

The adapter validates the runtime and experiment catalog hashes before use.
The experiment maps the official source Flash slug to the user-selected gateway
ID `deepseek-v4.1-flash`, retains only that model, retains only `max` in
`supported_reasoning_levels`, and changes its default reasoning level to `max`.
All other Flash metadata and model prompt fields match the official snapshot.
This is an explicit experiment filter, not an unmodified official catalog.
Native Codex rejects child overrides outside that catalog; parent defaults also
use Flash/max. The smoke relay records resolved outgoing settings.

On server A (`47.91.21.152`), the current prepared control release is beneath
`/data/tb4/experiments/gateway-yuzhi-20261016/`. Set `PYTHONPATH` to the selected
release root, not the historical `/data/tb4/control`. The import path is
`adapters.harbor.tb4_agents.codex:DeepSeekCodex`. `agent.json` is the direct user-confirmed gateway
configuration. It contains no credential; the host process loads
`DEEPSEEK_API_KEY` from `/data/tb4/secrets/deepseek.env`.
The authorized endpoint is `https://yuzhiapi.xyz/v1`. These are gateway keys.

`probe-responses.py` defaults to the current authorized gateway, validates any
explicit `--api-base` before reading credentials, and refuses HTTP redirects.
Historical gateway replay requires an explicit option and matching credentials.

## Synthetic smoke protocol

The live validation records below are historical evidence for the previous
`deepseek-v4-1-flash-260910` route. They do not validate the new gateway/model.

Start with the shared `local/tb4-synthetic-preflight` task. It reads
`/app/input.txt`, writes `/app/answer.txt`, reports its hash, transfers the artifact
to a separate verifier, and checks the exact file contents. It is outside the
56-task manifest. The smoke timeout is five minutes.

`smoke-agent.json` temporarily points at the metadata-only relay on
`http://172.17.0.1:8765/v1`. The relay source is `../common/audit_proxy.py`; bind
the host's Docker bridge IP, use a private JSONL log, and run inside `tb4.slice`.
The relay accepts Flash requests only. For the current gateway, start it with
`--upstream-base https://yuzhiapi.xyz --model deepseek-v4.1-flash`; it preserves the `/v1/responses` path.
It stores model, effort, tool names/types, output limits, image-present flags,
numeric usage, response status and timings. It never writes raw headers, API
keys, prompts, response text or provider error bodies. The temporary relay is
stopped when smoke finishes. Formal runs use direct gateway HTTPS.

Earlier attempts remain as historical infrastructure evidence: the pre-gateway
attempt `synthetic-20261015T175630` received 401, and the old Flash alias attempt
`synthetic-20261015T180355` received 503. Neither used the current exact model ID.
The user-selected exact ID resolved inference availability; those old failed
attempts are preserved and are not benchmark results.

An additional offline mock test passed on server A without a live provider call.
The real pinned CLI executed `exec_command`, read a PNG through `view_image`, and
applied a freeform `apply_patch`. File assertions passed; the third request
contained `input_image`, and the fourth contained `custom_tool_call_output`.
All four mock requests used Flash/max. The script is `offline-image-smoke.py`;
its server-side report is `/data/tb4/logs/codex-offline-image-smoke.json`. This
checks native CLI and transport behavior, not DeepSeek's model-side image support.

The previous gateway/model live preflights passed (historical evidence):

- A single non-streaming Responses probe returned HTTP 200 and the exact model
  ID, with reasoning/message output and 35 input / 19 output tokens.
- `synthetic-20261015T182232` passed the shared verifier with reward 1 and no
  exception. All four requests used the exact model and max; 34,857 input,
  29,312 cached input, and 322 output tokens were reported.
- `capability-synthetic-20261015T182442Z` passed its separate verifier with reward
  1 and no exception. The parent used native terminal and `apply_patch`; one
  child used native `view_image`, identified the red PNG, and wrote `child.txt`.
  All 11 requests used the exact model and max, with four image-bearing requests.
  Both native session files were preserved.

The capability task is another explicit synthetic task outside the 56-task
manifest. It uses two CPU / 4 GiB for the agent and one CPU / 512 MiB for the
separate verifier. At the time of that historical preflight, no formal benchmark task had run and the audit relay was stopped.

The additional `offline-subagent-smoke.py` test passed with the filtered catalog.
It deliberately requested a Pro child and a Flash/low child. Native Codex rejected
both before any child inference request. A legitimate Flash/max child then ran;
all six parent/child mock requests were Flash/max. Its Docker network was
internal-only and used a dummy credential, with zero external API calls.
Report: `/data/tb4/logs/codex-offline-subagent-smoke.json`.

## Token accounting

The pinned upstream converter selected the latest native session file, which can
be a child and omit the parent. The experiment adapter now converts every native
session with the upstream parser, retains the root ATIF timeline, embeds child
ATIF trajectories, and aggregates their per-call usage. Duplicate session IDs are
counted once. All 56 selected tasks are single-step and use a fresh root session.

Replaying the two successful live logs required no extra API call. The capability
totals now match the recording relay exactly: 119,884 input tokens including
106,112 cached input tokens, and 1,687 output tokens. The original pre-fix ATIF
and controller result are preserved; `tb4-usage-replay-validation.json` records
the corrected context. The plain synthetic totals also match the relay.

Gateway billing is unknown. `cost_usd` remains null; native price estimates, if
any, are excluded from billable-cost fields. Gateway Responses usage currently
does not supply a usable reasoning-token breakdown, so no separate reasoning
token count is inferred from the CLI's zero-valued counter.

The offline subagent test also passed with `--fork-all`: the child inherited the
exact model/max, Pro and low overrides were rejected, and all six mock requests
were counted exactly once in the aggregated context. No external API was called.

## Offline transport roots

Some task images lack OS CA certificates. Native Codex 0.149.1 uses native root
stores, so the adapter uploads the pinned public certifi 2026.1.4 bundle and sets
`CODEX_CA_CERTIFICATE` for the parent and children. The bundle SHA256 is
`4f3975ff30abbf35443b486064ae8bcb41dff707ffdf576c76d292fb458aa0a9`; provenance
and its license are in `certs/`. Installation checks this hash. OS trust files,
model configuration, tools and normal retry policy are unchanged.

The no-CA `bun-sourcemap-leak` image failed the native TLS probe without the
bundle. With the bundle, that image plus `ks-solver-cpp` and `nextjs-performance`
all reached the expected HTTP 401 using a rejected literal dummy key. These
checks loaded no real credential and performed no model inference. The
authentication-only helper bounds its own runtime to avoid Codex's separate
wait-for-network loop; this limit is not a formal-evaluation setting.

## Sources

- [OpenAI Codex configuration reference](https://developers.openai.com/codex/config-reference/)
- [DeepSeek official Codex setup and catalog](https://api-docs.deepseek.com/quick_start/agent_integrations/codex/)
- [Official npm package metadata](https://registry.npmjs.org/@openai%2fcodex/0.149.1-linux-x64)
