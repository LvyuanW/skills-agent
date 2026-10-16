# Terminal-Bench 4 preparation controller, v5

Preparation uses the pinned Harbor 0.24.0 environment API. It never creates an
Agent, calls a model, copies reference solutions or runs official verifier tests.
Formal execution remains a separate, explicit, approved command. All v4 code,
prewarm reports and stopped formal results remain historical evidence.

The v5 code lives under `/data/tb4/releases/remediation-v5/control`, preserving
`adapters/harbor/tb4_control`. Set both Python import roots to that release:

```sh
export PYTHONPATH=/data/tb4/releases/remediation-v5/control:/data/tb4/releases/remediation-v5/control/adapters/harbor
python -m adapters.harbor.tb4_control plan
python -m adapters.harbor.tb4_control status --output /data/tb4/runs/prewarm-v5
# Long-running preparation belongs in a tbench systemd service inside tb4.slice.
python -u -m adapters.harbor.tb4_control prewarm --max-concurrent 2 \
  --output /data/tb4/runs/prewarm-v5
# Separate host-wide-lock owner: do this after prewarm, never simultaneously.
python -u -m adapters.harbor.tb4_control.resource_probe \
  --output /data/tb4/runs/resource-probe-v5
# Model-free unit and full-plan fake-worker checks.
python -m unittest adapters.harbor.tb4_control.test_control \
  adapters.harbor.tb4_control.test_fake_scheduler \
  adapters.harbor.tb4_control.test_resources \
  adapters.harbor.tb4_control.test_outcomes -v
```

No credential EnvironmentFile is needed for these preparation commands. Real
Agent smoke/formal services use `User=tbench`, `Slice=tb4.slice`, the versioned
PYTHONPATH, and `EnvironmentFile=/data/tb4/secrets/deepseek.env`; do not put a key
in an argument, agent JSON, source file or log. The assigned API audit proxy
must already be available before a real Agent starts. The bundled `smoke_task/`
uses a synthetic shell/file task and separate lightweight verifier. Its results
are never benchmark results.

## Fixed runtime slots and service enforcement

`resource-inventory-v5.json` records all56 official main/verifier CPU, RAM,
storage, build and execution timeouts and the9 Compose definitions. Official
main/verifier maximums are4 CPU,16 GiB RAM and50 GiB declared storage. The20 task
sidecars originally have no memory limits, and only kv-live-surgery/loadgen has
an explicit CPU limit. `resource-profile.json` supplies new, experimental hard
CPU/RAM limits for all20 sidecars. They are experimental constraints, not
upstream requirements or proven workload peaks. The existing browser shared
memory settings remain unchanged. Main/verifier CPU and RAM are preserved.

`PreparedDockerEnvironment` appends an external Compose resource override after
the task Compose. Every service receives exact CPU, RAM, no-additional-swap and
`cgroup_parent=tb4.slice` limits. Live Docker limits and actual process cgroups
are verified after startup. Unknown services, changed Compose fingerprints or
changed task resources fail closed. The only selected network helper is the
batched-eval-parity verifier's optional egress sidecar, capped at0.25 CPU and
256 MiB. That whole phase is1.25 CPU/4352 MiB; it is included in the envelope.

The largest whole runtime phase is5 CPU and21 GiB. Each trial reserves the same
5 CPU/21 GiB slot, with a fixed maximum of5 concurrent trials per server:
25 CPU/105 GiB within the28 CPU/107 GiB admission pool. The shared parent has
30 CPU time, CPU affinity2-31, memory.max115 GiB and no swap, leaving CPU0-1
and at least8 GiB for host services. The difference between parent and five
runtime slots is5 CPU/10 GiB for shared overhead. Docker image builds run in the
parent through Docker/containerd; per-container runtime limits do not hard-cap
BuildKit's steps. Preparation separately reserves an additional4 CPU/8 GiB per
active build task and permits at most2. Cached-image prewarming reduces rebuild
cost but is not a guarantee that later builds have no cost.

Agent and separate verifier phases must not overlap. The environment registry
checks that the previous project's containers are gone before verifier.start.
Stop checks project containers, networks and volumes after Harbor's best-effort down; a swallowed cleanup
failure remains sticky. The worker rechecks this registry outside Trial.run
before publishing its controller outcome, so a reward cannot hide cleanup
failure. Before explicit service stop or project teardown, all containers are
checked for OOMKilled, unexpected RestartCount, unhealthy status and any natural
exit, including exit0. Deliberate Harbor artifact-collection stops are tracked
separately. OOM or restart evidence remains an error even after an operator stop.

## Disk admission

The pinned Docker backend does not implement a writable-layer storage quota.
Each trial therefore reserves `max(main.storage_mb, verifier.storage_mb)` as a
soft disk admission requirement. Before each admission, current filesystem free
space minus20% of total capacity must cover all active trial declarations plus
the candidate. Already written bytes are deliberately counted twice: live free
already excludes them, and the full active declaration is reserved again.

A candidate that does not fit stays pending and receives no score. Other fitting
candidates may proceed. With no active work and no fitting candidate, the
controller writes a durable disk pause and exits incomplete. Dropping below20%
free pauses new work while existing trials drain. Prewarm also checks live free
before every phase and reserves declarations for active preparation tasks.
Image caches, builds and task writes share the same disk, so this is conservative
admission rather than a per-trial hard guarantee. Five ordinary10 GiB trials can
fit when sufficient headroom exists; five50 GiB trials are not guaranteed on the
current500 GB disk.

## Environment preparation and acceptance

The new default is `/data/tb4/runs/prewarm-v5`; the old `runs/prewarm` path is
rejected. Each of56 tasks builds, starts, probes and removes its Agent and
separate verifier environment, serially within a task. Successful phases resume
only with matching preparation identity, complete cleanup and fresh retained
image IDs/aliases. Changed environment/profile/enforcer/probe identities require
fresh preparation. Per-phase build logs and prior attempts are retained.

All112 successful phases must attest actual service caps and cleanup. Startup
samples are idle/bounded preparation evidence, not workload peak measurements.
Each service image receives a retained `tb4-prewarm/...` alias before cleanup.
Formal admission freshly checks all112 aliases and recorded IDs before creating
its DB; a stale summary cannot survive Docker prune/retag unnoticed.

`resource_probe` separately holds5 isolated copies of the same task alive,
for each of the9 sidecar tasks. It verifies hard caps, distinct private project
networks, isolated synthetic shell/file writes, existing sidecar health requests,
medical's read-only MCP browser_tabs call and unchanged parent OOM counters.
It uses no Trial/Agent or official verifier and removes every project. This is
a bounded acceptance check, not proof that every possible workload fits the
experimental sidecar limits. A failed cap/health/cleanup check blocks acceptance;
limits are never automatically expanded. All9 matching successful five-project
reports are required by formal admission, via `--resource-probe-summary`.

## FreeCAD runtime dependency repair

The3 official FreeCAD verifier images otherwise select a PyVista/VTK combination
incompatible with conda FreeCAD. `dependency-profile.json` pins PyVista0.48.4 and
VTK9.2.6, retaining validator0.1.3 and base NumPy. The external Compose inline
Dockerfile changes exactly one audited dependency line in those3 verifier
Dockerfiles. Original task sources, reference solutions and grader logic remain
unchanged. This is an explicit runtime dependency deviation.

Prewarm and formal use the same environment import and same external overlay.
The keyless runtime probe imports FreeCAD/Part and validator, checks exact
VTK/PyVista versions, NumPy/VTK array roundtrip, box volume and a nonblank Xvfb
render. Formal admission requires3 successful matching ABI/render reports,
with current hashes and recomputed effective Dockerfile/overlay identities.

## Durable scheduling and failure policy

Formal defaults to a new `runs/formal-v5`; the old `runs/formal` path is rejected.
Its explicit matching approval includes experiment, manifest, agent config,
resource profile, trial plan, Agent and server identity. The required resource
argument is now `--resource-profile`; old exclusive sidecar budgets are unused.
No code here creates an approval file or starts formal evaluation implicitly.

The504-row frozen plan supplies exact slot IDs and each Agent/server's168 slots.
SQLite WAL stores slot CPU/RAM/disk reservations and all attempts. Frozen inputs
include exact dataset commit and clean task sources, model/effort/plan/config,
resource profile, controller/adapter/assets/release trees, prewarm summary and
five-project acceptance summary SHA, budget and concurrency settings. A changed
input rejects continuation. A recorded running attempt requires operator
reconciliation rather than blind duplication. One host-wide flock serializes
formal, prewarm and coexistence-probe admission across output directories.

`outcomes.py` owns classification. Valid binary verifier rewards score normal
runs and recognized Agent timeout/context/output limits. Known provider/setup
faults, unexplained native nonzero exits, resource/cleanup faults and missing or
invalid worker results remain needs_review even with a raw zero. The controller
writes safe typed reason/slot fields to `ADMISSION_PAUSED.json`, creates
`PAUSE_ADMISSION`, stops new admissions and drains in-flight work. It never
copies provider bodies, task text or credentials into this pause record.

Only the explicit environment transport allowlist can receive up to2 extra
attempts. Provider failures never resample a whole task. Raw rewards and original
native logs are retained. Clearing a pause or reconciling an interrupted slot is
an operator action; restarting the controller cannot silently ignore it.
