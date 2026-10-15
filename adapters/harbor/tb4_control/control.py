"""Prepare task environments without model calls; gate all formal evaluations.

Run with the pinned Harbor interpreter and inside tb4.slice via tb4-exec.
All subprocess commands use argument arrays. Credentials are inherited only by
explicit model smoke/formal workers; preparation never creates a Trial/Agent.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import uuid

ROOT = Path('/data/tb4')
PACKAGE = Path(__file__).resolve().parent
CPU_BUDGET = 28.0
MEMORY_MB_BUDGET = 107 * 1024
MAX_CONCURRENT = 14


def dump(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
    temp.replace(path)


def run(args, **kwargs):
    return subprocess.run(args, check=True, capture_output=True, text=True, **kwargs).stdout


def verify_boundary():
    """Require a real parent cgroup boundary, not just per-container flags."""
    cgroup = Path('/proc/self/cgroup').read_text()
    if '/tb4.slice/' not in cgroup and not cgroup.rstrip().endswith('/tb4.slice'):
        raise RuntimeError('Run through /usr/local/sbin/tb4-exec (outside tb4.slice).')
    base = Path('/sys/fs/cgroup/tb4.slice')
    memory_max = (base / 'memory.max').read_text().strip()
    if memory_max == 'max' or int(memory_max) > 115 * 1024**3:
        raise RuntimeError('tb4.slice memory.max must be <=115 GiB.')
    cpus = (base / 'cpuset.cpus.effective').read_text().strip()
    if cpus != '2-31':
        raise RuntimeError(f'tb4.slice must reserve CPUs 0-1; got {cpus!r}.')
    quota, period = (base / 'cpu.max').read_text().split()
    if quota == 'max' or int(quota) / int(period) > 30:
        raise RuntimeError('tb4.slice must cap CPU time at 30 CPUs.')
    return {'cgroup': cgroup.strip(), 'memory_max': int(memory_max),
            'cpu_max': [int(quota), int(period)], 'cpuset': cpus}


def disk_check(root):
    usage = shutil.disk_usage(root)
    if usage.free / usage.total < .20:
        raise RuntimeError('Disk free space <20%; pause admission, keep current images.')
    return {'total': usage.total, 'free': usage.free}


def manifest(path):
    data = json.loads(Path(path).read_text())
    tasks = data['tasks']
    if len(tasks) != 56 or len({x['task'] for x in tasks}) != 56:
        raise ValueError('Expected exactly 56 unique tasks.')
    for task in tasks:
        for field in ['main_resources', 'verifier_effective_resources']:
            resource = task[field]
            if resource['cpus'] > 4 or resource.get('gpus', 0):
                raise ValueError('Unexpected resources in the strict 56 task selection.')
    return data


def weights(task, calibrated=None):
    main = task['main_resources']
    verifier = task['verifier_effective_resources']
    side = {'cpus': 0, 'memory_mb': 0}
    if task['has_sidecars']:
        side = (calibrated or {}).get(task['task'])
        if not side or not side.get('reviewed'):
            raise ValueError(f"{task['task']}: reviewed sidecar reservations required")
    # Reserve the heavier whole phase, including sidecars during agent execution.
    return (max(main['cpus'] + side['cpus'], verifier['cpus']),
            max(main['memory_mb'] + side['memory_mb'], verifier['memory_mb']))


def fits(active, candidate):
    return (len(active) < MAX_CONCURRENT
            and sum(x[0] for x in active) + candidate[0] <= CPU_BUDGET
            and sum(x[1] for x in active) + candidate[1] <= MEMORY_MB_BUDGET)


def project_containers(session):
    project = re.sub(r'[^a-z0-9_-]', '-', session.lower())
    ids = run(['docker', 'ps', '-q', '--filter',
               f'label=com.docker.compose.project={project}']).split()
    if not ids:
        raise RuntimeError('Environment start returned no running containers.')
    inspected = json.loads(run(['docker', 'inspect', *ids]))
    records = []
    for obj in inspected:
        config = obj['HostConfig']
        pid = obj['State']['Pid']
        cg = Path(f'/proc/{pid}/cgroup').read_text().strip()
        if '/tb4.slice/' not in cg:
            raise RuntimeError('Docker container escaped tb4.slice.')
        records.append({'id': obj['Id'], 'name': obj['Name'],
                        'service': obj['Config']['Labels'].get('com.docker.compose.service'),
                        'image': obj['Image'], 'pid': pid, 'cgroup': cg,
                        'nano_cpus': config.get('NanoCpus', 0),
                        'memory_bytes_limit': config.get('Memory', 0)})
    return records


async def prewarm_phase(task_dir, phase, output, sample_seconds):
    # Intentionally no Trial, AgentFactory, verifier tests, or solution files.
    from harbor.environments.factory import EnvironmentFactory
    from harbor.models.environment_type import EnvironmentType
    from harbor.models.task.task import Task
    from harbor.models.task.verifier_mode import resolve_verifier_environment_definition
    from harbor.models.trial.config import EnvironmentConfig, ResourceMode
    from harbor.models.trial.paths import TrialPaths

    verify_boundary()
    disk_check(output)
    task = Task(task_dir)
    definition = resolve_verifier_environment_definition(task.config, task.paths)
    if phase == 'verifier' and definition is None:
        dump(Path(output) / 'report.json', {'task': task.short_name, 'phase': phase,
             'status': 'shared_environment', 'no_model_calls': True})
        return
    config = definition.config if phase == 'verifier' else task.config.environment
    directory = definition.directory if phase == 'verifier' else task.paths.environment_dir
    paths = TrialPaths(Path(output))
    paths.mkdir()
    paths.chmod_dir()
    session = f'tb4-prewarm-{task.short_name}-{phase}-{uuid.uuid4().hex[:8]}'
    env = EnvironmentFactory.create_environment_from_config(
        EnvironmentConfig(type=EnvironmentType.DOCKER,
                          cpu_enforcement_policy=ResourceMode.LIMIT,
                          memory_enforcement_policy=ResourceMode.LIMIT),
        environment_dir=directory, environment_name=task.short_name,
        session_id=session, trial_paths=paths, task_env_config=config,
        enable_environment_dir_upload=not (phase == 'verifier' and definition.bundled_tests),
    )
    started = time.time()
    report = {'task': task.short_name, 'phase': phase, 'started': started,
              'session': session, 'no_model_calls': True, 'no_reference_solution': True,
              'resource_config': config.model_dump(mode='json', exclude={'env'})}
    try:
        await asyncio.wait_for(env.start(force_build=False), timeout=config.build_timeout_sec)
        containers = project_containers(session)
        main = next(x for x in containers if x['service'] == 'main')
        if main['nano_cpus'] != int(config.cpus * 1_000_000_000):
            raise RuntimeError('Main CPU limit mismatch.')
        if main['memory_bytes_limit'] != config.memory_mb * 1024**2:
            raise RuntimeError('Main memory limit mismatch.')
        check = await env.exec(command='test -d / && test -d /tmp', timeout_sec=30)
        if check.return_code:
            raise RuntimeError('Main container basic shell probe failed.')
        samples = []
        # Idle/startup samples inform admission only; never claim workload peak calibration.
        for _ in range(max(1, math.ceil(sample_seconds / 5))):
            ids = [x['id'] for x in containers]
            sample = run(['docker', 'stats', '--no-stream', '--format', '{{json .}}', *ids])
            samples.append({'at': time.time(), 'containers': [json.loads(x) for x in sample.splitlines()]})
            if sample_seconds > 0:
                await asyncio.sleep(min(5, sample_seconds))
        report.update(status='passed', containers=containers, samples=samples,
                      elapsed_sec=time.time() - started,
                      note='Environment startup only; no task verifier tests or agent executed.')
    except BaseException as exc:
        report.update(status='failed', error_type=type(exc).__name__, error=str(exc),
                      elapsed_sec=time.time() - started)
        raise
    finally:
        try:
            await env.stop(delete=True)
            report['cleaned_up'] = True
        except Exception as exc:
            report['cleanup_error'] = str(exc)
        dump(Path(output) / 'report.json', report)


def prewarm(args):
    verify_boundary()
    data = manifest(args.manifest)
    task_list = data['tasks']
    if args.task:
        task_list = [x for x in task_list if x['task'] in args.task]
        if len(task_list) != len(set(args.task)):
            raise ValueError('Unknown task selected.')
    args.output.mkdir(parents=True, exist_ok=True)
    # One controller per output tree; sequential phases keep builds bounded.
    with (args.output / '.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        results = []
        for task in task_list:
            for phase in ['agent', 'verifier']:
                destination = args.output / task['task'] / phase
                report = destination / 'report.json'
                if report.exists() and not args.force:
                    existing = json.loads(report.read_text())
                    if existing.get('status') in ['passed', 'shared_environment'] and existing.get('cleaned_up', True):
                        results.append(existing)
                        continue
                disk_check(args.output)
                destination.mkdir(parents=True, exist_ok=True)
                command = [sys.executable, '-m', 'adapters.harbor.tb4_control', '_prewarm-phase',
                           '--task-dir', str(args.dataset / 'tasks' / task['task']), '--phase', phase,
                           '--output', str(destination), '--sample-seconds', str(args.sample_seconds)]
                print(json.dumps({'event': 'prewarm_start', 'task': task['task'], 'phase': phase}), flush=True)
                with (destination / 'build.log').open('w') as log:
                    completed = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT)
                result = json.loads(report.read_text()) if report.exists() else {
                    'task': task['task'], 'phase': phase, 'status': 'failed', 'returncode': completed.returncode}
                results.append(result)
                dump(args.output / 'summary.json', {'kind': 'environment_prewarm',
                     'no_model_calls': True, 'updated_at': time.time(), 'results': results,
                     'passed': sum(x['status'] in ['passed', 'shared_environment'] for x in results),
                     'failed': sum(x['status'] == 'failed' for x in results)})
                print(json.dumps({'event': 'prewarm_end', 'task': task['task'], 'phase': phase,
                                  'status': result['status']}), flush=True)
        if any(x['status'] == 'failed' for x in results):
            raise SystemExit(1)


async def trial_worker(task_dir, agent_file, output, result_file):
    from harbor.models.trial.config import TrialConfig, AgentConfig, EnvironmentConfig, TaskConfig, ResourceMode
    from harbor.models.environment_type import EnvironmentType
    from harbor.trial.trial import Trial
    verify_boundary()
    agent = AgentConfig.model_validate(json.loads(agent_file.read_text()))
    if not agent.import_path and (not agent.name or agent.name in ['oracle', 'nop']):
        raise ValueError('An explicit non-oracle Agent is required.')
    config = TrialConfig(task=TaskConfig(path=task_dir),
                         trial_name=output.name, trials_dir=output.parent,
                         agent=agent, environment=EnvironmentConfig(type=EnvironmentType.DOCKER,
                         cpu_enforcement_policy=ResourceMode.LIMIT,
                         memory_enforcement_policy=ResourceMode.LIMIT))
    try:
        trial = await Trial.create(config)
        result = await trial.run()
        dump(result_file, result.model_dump(mode='json'))
    except BaseException as exc:
        dump(result_file, {'controller_error': {'type': type(exc).__name__, 'message': str(exc)}})
        raise


def smoke(args):
    verify_boundary()
    task_dir = PACKAGE / 'smoke_task'
    output = args.output / ('synthetic-' + time.strftime('%Y%m%dT%H%M%S', time.gmtime()))
    output.mkdir(parents=True)
    print(f'Synthetic model preflight only: {output}', flush=True)
    asyncio.run(trial_worker(task_dir, args.agent_config, output, output / 'controller-result.json'))


def db_connect(path):
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA journal_mode=WAL')
    db.executescript('''
      CREATE TABLE IF NOT EXISTS slots (
        id TEXT PRIMARY KEY, task TEXT NOT NULL, repeat INTEGER NOT NULL,
        cpus REAL NOT NULL, memory_mb INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'pending');
      CREATE TABLE IF NOT EXISTS attempts (
        slot_id TEXT NOT NULL, attempt INTEGER NOT NULL, status TEXT NOT NULL,
        started REAL, finished REAL, pid INTEGER, result_path TEXT,
        PRIMARY KEY(slot_id, attempt));
    ''')
    return db


def classify(result):
    """Retry only an explicit infrastructure allowlist; never retry a failed reward."""
    error = result.get('exception_info') or result.get('controller_error')
    if not error:
        verifier = result.get('verifier_result') or {}
        return 'complete' if verifier.get('rewards') is not None else 'needs_review'
    name = error.get('exception_type') or error.get('type') or ''
    if name in {'EnvironmentStartTimeoutError', 'DockerDaemonConnectionError',
                'EnvironmentConnectionError', 'ProviderUnavailableError'}:
        return 'infra_failure'
    # Agent timeouts/errors are terminal; preserve reward if verifier produced one.
    if name in {'AgentTimeoutError', 'AgentExecutionTimeoutError', 'AgentSetupTimeoutError'}:
        return 'complete' if (result.get('verifier_result') or {}).get('rewards') is not None else 'needs_review'
    return 'needs_review'


def formal(args):
    verify_boundary()
    # No deployment command creates this token. An operator must separately approve formal start.
    expected = {'experiment': 'tb4-56-flash-max-v2', 'allow_formal': True,
                'manifest_sha256': hashlib.sha256(args.manifest.read_bytes()).hexdigest(),
                'agent_config_sha256': hashlib.sha256(args.agent_config.read_bytes()).hexdigest(),
                'sidecar_budgets_sha256': hashlib.sha256(args.sidecar_budgets.read_bytes()).hexdigest()}
    if not args.unlock.is_file() or json.loads(args.unlock.read_text()) != expected:
        raise RuntimeError('Formal evaluation locked: explicit matching approval file required.')
    data = manifest(args.manifest)
    budgets = json.loads(args.sidecar_budgets.read_text())
    weighted = {x['task']: weights(x, budgets) for x in data['tasks']}
    if any(not fits([], x) for x in weighted.values()):
        raise RuntimeError('One task exceeds admission budget.')
    args.output.mkdir(parents=True, exist_ok=True)
    with (args.output / '.controller.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        db = db_connect(args.output / 'state.sqlite3')
        if db.execute("SELECT 1 FROM attempts WHERE status='running'").fetchone():
            raise RuntimeError('Previous active attempts need operator reconciliation; do not duplicate them.')
        for name, (cpus, memory) in weighted.items():
            for repeat in range(1, 4):
                db.execute('INSERT OR IGNORE INTO slots(id,task,repeat,cpus,memory_mb) VALUES(?,?,?,?,?)',
                           (f'{name}__r{repeat}', name, repeat, cpus, memory))
        db.commit()
        active = {}
        stop = False
        try:
            while True:
                for slot_id, item in list(active.items()):
                    process, reservation, result_file, attempt, log = item
                    if process.poll() is None:
                        continue
                    log.close()
                    result = json.loads(result_file.read_text()) if result_file.exists() else {}
                    status = classify(result)
                    next_status = 'pending' if status == 'infra_failure' and attempt < 3 else status
                    db.execute('UPDATE attempts SET status=?,finished=? WHERE slot_id=? AND attempt=?',
                               (status, time.time(), slot_id, attempt))
                    db.execute('UPDATE slots SET status=? WHERE id=?', (next_status, slot_id))
                    db.commit()
                    del active[slot_id]
                if not stop:
                    try:
                        disk_check(args.output)
                    except RuntimeError:
                        stop = True
                    if (args.output / 'PAUSE_ADMISSION').exists():
                        stop = True
                pending = db.execute("SELECT * FROM slots WHERE status='pending' ORDER BY cpus*memory_mb DESC,task,repeat").fetchall()
                if not active and (not pending or stop):
                    break
                for slot in ([] if stop else pending):
                    reservation = (slot['cpus'], slot['memory_mb'])
                    if not fits([x[1] for x in active.values()], reservation):
                        continue
                    attempt = db.execute('SELECT count(*) FROM attempts WHERE slot_id=?', (slot['id'],)).fetchone()[0] + 1
                    directory = args.output / slot['id'] / f'attempt-{attempt}'
                    directory.mkdir(parents=True, exist_ok=False)
                    result_file = directory / 'controller-result.json'
                    log = (directory / 'worker.log').open('w')
                    command = [sys.executable, '-m', 'adapters.harbor.tb4_control', '_trial-worker',
                               '--task-dir', str(args.dataset / 'tasks' / slot['task']),
                               '--agent-config', str(args.agent_config), '--output', str(directory),
                               '--result-file', str(result_file)]
                    child_env = dict(os.environ, TB4_FORMAL_CHILD_AUTHORIZED='1')
                    process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, env=child_env)
                    db.execute('INSERT INTO attempts VALUES(?,?,?,?,?,?,?)',
                               (slot['id'], attempt, 'running', time.time(), None, process.pid, str(result_file)))
                    db.execute("UPDATE slots SET status='running' WHERE id=?", (slot['id'],))
                    db.commit()
                    active[slot['id']] = (process, reservation, result_file, attempt, log)
                time.sleep(1)
        except KeyboardInterrupt:
            # Stop admission, let workers finish normally; a second SIGINT remains visible.
            print('Interrupted; active attempts remain recorded for reconciliation.', file=sys.stderr)
            raise
        finally:
            db.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ['plan', 'status']:
        sub = commands.add_parser(name)
        sub.add_argument('--manifest', type=Path, default=ROOT / 'plans/task-manifest.json')
        sub.add_argument('--output', type=Path, default=ROOT / 'runs/prewarm')
    warm = commands.add_parser('prewarm', help='Build/start/probe/stop environments only; zero model calls')
    warm.add_argument('--manifest', type=Path, default=ROOT / 'plans/task-manifest.json')
    warm.add_argument('--dataset', type=Path, default=ROOT / 'src/terminal-bench')
    warm.add_argument('--output', type=Path, default=ROOT / 'runs/prewarm')
    warm.add_argument('--sample-seconds', type=int, default=10)
    warm.add_argument('--task', action='append')
    warm.add_argument('--force', action='store_true')
    phase = commands.add_parser('_prewarm-phase', help=argparse.SUPPRESS)
    phase.add_argument('--task-dir', type=Path, required=True)
    phase.add_argument('--phase', choices=['agent', 'verifier'], required=True)
    phase.add_argument('--output', type=Path, required=True)
    phase.add_argument('--sample-seconds', type=int, default=10)
    smoke_parser = commands.add_parser('smoke', help='One synthetic model preflight, never a benchmark task')
    smoke_parser.add_argument('--agent-config', type=Path, required=True)
    smoke_parser.add_argument('--output', type=Path, default=ROOT / 'runs/preflight')
    worker = commands.add_parser('_trial-worker', help=argparse.SUPPRESS)
    worker.add_argument('--task-dir', type=Path, required=True)
    worker.add_argument('--agent-config', type=Path, required=True)
    worker.add_argument('--output', type=Path, required=True)
    worker.add_argument('--result-file', type=Path, required=True)
    official = commands.add_parser('formal-start', help='Requires separate formal approval file; never used during preparation')
    for name, default in [('manifest', ROOT/'plans/task-manifest.json'),
                          ('dataset', ROOT/'src/terminal-bench'), ('output', ROOT/'runs/formal'),
                          ('unlock', ROOT/'plans/FORMAL_APPROVED.json')]:
        official.add_argument('--' + name, type=Path, default=default)
    official.add_argument('--agent-config', type=Path, required=True)
    official.add_argument('--sidecar-budgets', type=Path, required=True)
    args = parser.parse_args()
    if args.command == 'prewarm':
        prewarm(args)
    elif args.command == '_prewarm-phase':
        asyncio.run(prewarm_phase(args.task_dir, args.phase, args.output, args.sample_seconds))
    elif args.command == 'smoke':
        smoke(args)
    elif args.command == '_trial-worker':
        # Internal worker cannot be used to bypass the formal unlock gate.
        if args.task_dir.resolve() != (PACKAGE / 'smoke_task').resolve():
            if not os.environ.get('TB4_FORMAL_CHILD_AUTHORIZED'):
                raise RuntimeError('Formal task worker requires controller authorization.')
        asyncio.run(trial_worker(args.task_dir, args.agent_config, args.output, args.result_file))
    elif args.command == 'formal-start':
        formal(args)
    elif args.command == 'plan':
        data = manifest(args.manifest)
        print(json.dumps({'selected_tasks': len(data['tasks']), 'trials_per_agent': 168,
                          'cpu_budget': CPU_BUDGET, 'memory_mb_budget': MEMORY_MB_BUDGET,
                          'max_concurrent': MAX_CONCURRENT, 'formal_enabled': False}, indent=2))
    elif args.command == 'status':
        path = args.output / 'summary.json'
        print(path.read_text() if path.exists() else '{"status":"not_started"}')


if __name__ == '__main__':
    main()
