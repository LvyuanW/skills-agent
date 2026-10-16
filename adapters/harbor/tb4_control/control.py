"""Prepare task environments without model calls; gate all formal evaluations.

Run with the pinned Harbor interpreter and inside tb4.slice via tb4-exec.
All subprocess commands use argument arrays. Credentials are inherited only by
explicit model smoke/formal workers; preparation never creates a Trial/Agent.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import signal
import shutil
import sqlite3
import subprocess
import sys
import time
import threading
import uuid
from urllib.parse import urlsplit, urlunsplit
from .outcomes import assess_outcome, classify_result
from .resources import (PROFILE_PATH, load_profile, resource_identity)

ROOT = Path('/data/tb4')
PACKAGE = Path(__file__).resolve().parent
CPU_BUDGET = 28.0
MEMORY_MB_BUDGET = 107 * 1024
MAX_CONCURRENT = 5
SLOT_CPUS = 5
SLOT_MEMORY_MB = 21 * 1024
DEPENDENCY_TASKS = ['freecad-impeller', 'freecad-platform-drawing', 'freecad-spring-clip']


def dependency_identity():
    return {**resource_identity(), **{key: hashlib.sha256((PACKAGE/name).read_bytes()).hexdigest() for key, name in [
        ('dependency_profile_sha256', 'dependency-profile.json'),
        ('environment_adapter_sha256', 'environment.py'),
        ('runtime_probe_sha256', 'cad_runtime_probe.py'),
        ('resource_probe_sha256', 'resource_probe.py')]}}


def expected_dependency_overlay(directory, task):
    from .environment import build_overlay
    return build_overlay(directory, task)


def verify_dependency_readiness(summary, directory, dataset):
    identity = dependency_identity()
    if any(summary.get(key) != value for key, value in identity.items()):
        raise RuntimeError('Prewarm dependency profile/adapter/probe identity differs from current code.')
    for task in DEPENDENCY_TASKS:
        report = json.loads((directory/task/'verifier/report.json').read_text())
        overlay = report.get('dependency_overlay') or {}
        original_hash = hashlib.sha256((dataset/'tasks'/task/'tests/Dockerfile').read_bytes()).hexdigest()
        _, expected = expected_dependency_overlay(dataset/'tasks'/task/'tests', task)
        if (report.get('status') != 'passed' or any(overlay.get(k) != v for k, v in identity.items())
            or overlay.get('original_dockerfile_sha256') != original_hash
            or any(overlay.get(key) != expected[key] for key in
                   ['effective_dockerfile_sha256', 'overlay_sha256'])
            or not (report.get('dependency_runtime_validation') or {}).get('passed')):
            raise RuntimeError(f'{task}: matching dependency overlay and successful ABI/render probe required.')


@contextlib.contextmanager
def host_lock(path=None):
    """One resource-admission owner across every output directory on a host."""
    path = path or ROOT / 'runs/.resource-controller.lock'
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def dump(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
    temp.replace(path)


def tree_digest(path):
    digest = hashlib.sha256()
    path = Path(path)
    for entry in sorted(path.rglob('*')):
        if not entry.is_file() or '__pycache__' in entry.parts or entry.suffix == '.pyc':
            continue
        digest.update(str(entry.relative_to(path)).encode() + b'\0')
        digest.update(hashlib.sha256(entry.read_bytes()).digest())
    return digest.hexdigest()


def verify_source_checkout(path, expected_commit):
    head = run(['git', '-C', str(path), 'rev-parse', 'HEAD']).strip()
    if head != expected_commit:
        raise RuntimeError('Dataset HEAD does not match the frozen benchmark commit.')
    dirty = run(['git', '-C', str(path), 'status', '--porcelain', '--untracked-files=all', '--', 'tasks'])
    if dirty.strip():
        raise RuntimeError('Dataset task sources have modified or untracked files.')
    return head


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


def disk_fits(snapshot, active_mb, candidate_mb):
    # Deliberately conservative: live free already excludes bytes written by
    # active trials, and their full declarations are reserved again here.
    return snapshot['free'] - snapshot['total'] * .20 >= (sum(active_mb) + candidate_mb) * 1024**2


def task_disk_mb(task):
    return max(task['main_resources']['storage_mb'], task['verifier_effective_resources']['storage_mb'])


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


def weights(task, profile=None):
    profile = profile or load_profile()
    record = profile['tasks'].get(task['task'])
    if record is None:
        raise ValueError('Task lacks a reviewed resource profile.')
    for phase, field in [('agent', 'main_resources'), ('verifier', 'verifier_effective_resources')]:
        for key in ['cpus', 'memory_mb', 'storage_mb']:
            if task[field][key] != record[phase][key]:
                raise ValueError('Manifest resources differ from the frozen resource profile.')
    return SLOT_CPUS, SLOT_MEMORY_MB


def verify_resource_readiness(summary):
    identity = resource_identity()
    if any(summary.get(key) != value for key, value in identity.items()):
        raise RuntimeError('Prewarm resource identity differs from the fixed v5 profile/enforcer.')
    for report in summary.get('results', []):
        metadata = report.get('resource_enforcement') or {}
        if (report.get('preparation_identity') != dependency_identity()
                or any(metadata.get(key) != value for key, value in identity.items())
                or not metadata.get('live_caps_verified') or not metadata.get('cleanup_verified')):
            raise RuntimeError('Every prewarm phase must attest live resource caps and cleanup.')


def verify_coexistence_readiness(summary, profile):
    expected = {task for task, record in profile['tasks'].items() if record['sidecars']}
    reports = summary.get('reports') or []
    if (not summary.get('no_model_calls') or summary.get('preparation_identity') != dependency_identity()
            or summary.get('passed') != 9 or summary.get('failed') != 0
            or len(reports) != 9 or {report.get('task') for report in reports} != expected):
        raise RuntimeError('All nine sidecar tasks need matching five-project coexistence acceptance.')
    for report in reports:
        if (report.get('status') != 'passed' or not report.get('cleaned_up')
                or report.get('preparation_identity') != dependency_identity()
                or report.get('simultaneous_projects_verified') != 5
                or len(report.get('projects') or []) != 5):
            raise RuntimeError('Coexistence acceptance is incomplete or failed.')
        for project in report['projects']:
            metadata = project.get('resource_enforcement') or {}
            if (any(metadata.get(key) != value for key, value in resource_identity().items())
                    or not metadata.get('live_caps_verified') or not metadata.get('cleanup_verified')):
                raise RuntimeError('Each coexistence project must attest live caps and complete cleanup.')


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
                        'memory_bytes_limit': config.get('Memory', 0),
                        'network_ips': [x['IPAddress'] for x in obj['NetworkSettings']['Networks'].values()
                                        if x.get('IPAddress')]})
    return records


def retained_images_present(report):
    if report.get('status') == 'shared_environment':
        return True
    containers = report.get('containers') or []
    aliases = [item.get('retained_image_alias') for item in containers]
    if not aliases or not all(aliases):
        return False
    try:
        inspected = json.loads(run(['docker', 'image', 'inspect', *aliases]))
    except subprocess.CalledProcessError:
        return False
    return [image['Id'] for image in inspected] == [item['image'] for item in containers]


def verify_retained_image_readiness(summary, task_names):
    """Check current Docker state, not a prewarm-time inventory count."""
    expected = {(task, phase) for task in task_names for phase in ['agent', 'verifier']}
    reports = summary.get('results') or []
    actual = {(report.get('task'), report.get('phase')) for report in reports}
    if len(reports) != len(expected) or actual != expected:
        raise RuntimeError('Prewarm reports do not cover each expected task/phase exactly once.')
    for report in reports:
        if (report.get('status') not in ['passed', 'shared_environment']
            or not report.get('cleaned_up', False) or not retained_images_present(report)):
            raise RuntimeError(f"{report['task']}/{report['phase']}: retained images missing or changed; "
                               'explicit environment preparation required.')


async def prewarm_phase(task_dir, phase, output, sample_seconds):
    # Intentionally no Trial, AgentFactory, verifier tests, or solution files.
    from harbor.environments.factory import EnvironmentFactory
    from harbor.models.environment_type import EnvironmentType
    from harbor.models.task.task import Task
    from harbor.models.task.verifier_mode import resolve_verifier_environment_definition
    from harbor.models.trial.config import EnvironmentConfig, ResourceMode
    from harbor.models.trial.paths import TrialPaths

    # A restart request pauses the NEXT phase before creating any environment;
    # the active phase is allowed to finish and clean up normally.
    pause_marker = Path(output).parents[1] / 'PAUSE_BEFORE_PHASE'
    if pause_marker.exists():
        ready = Path(output) / 'PAUSED_BEFORE_PHASE'
        ready.parent.mkdir(parents=True, exist_ok=True)
        ready.write_text(str(os.getpid()))
        while pause_marker.exists():
            await asyncio.sleep(1)
        ready.unlink(missing_ok=True)
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
                          import_path='adapters.harbor.tb4_control.environment:PreparedDockerEnvironment',
                          cpu_enforcement_policy=ResourceMode.LIMIT,
                          memory_enforcement_policy=ResourceMode.LIMIT),
        environment_dir=directory, environment_name=task.short_name,
        session_id=session, trial_paths=paths, task_env_config=config,
        network_policy=config.resolve_baseline(),
        enable_environment_dir_upload=not (phase == 'verifier' and definition.bundled_tests),
    )
    started = time.time()
    report = {'task': task.short_name, 'phase': phase, 'started': started,
              'session': session, 'no_model_calls': True, 'no_reference_solution': True,
              'resource_enforcement': env.resource_enforcement,
              'preparation_identity': dependency_identity(),
              'resource_config': config.model_dump(mode='json', exclude={'env'})}
    if env.dependency_overlay:
        report['dependency_overlay'] = env.dependency_overlay
    try:
        await asyncio.wait_for(env.start(force_build=False), timeout=config.build_timeout_sec)
        containers = project_containers(session)
        # Harbor 0.24 removes automatically generated project image tags during
        # down --rmi local. Keep an explicit immutable alias so the prewarmed
        # layers/images remain after all containers and volumes are deleted.
        for container in containers:
            alias = f"tb4-prewarm/{task.short_name}-{phase}-{container['service']}:{container['image'].split(':')[-1][:12]}"
            run(['docker', 'image', 'tag', container['image'], alias])
            container['retained_image_alias'] = alias
        main = next(x for x in containers if x['service'] == 'main')
        if main['nano_cpus'] != int(config.cpus * 1_000_000_000):
            raise RuntimeError('Main CPU limit mismatch.')
        if main['memory_bytes_limit'] != config.memory_mb * 1024**2:
            raise RuntimeError('Main memory limit mismatch.')
        check = await env.exec(command='test -d / && test -d /tmp', timeout_sec=30)
        if check.return_code:
            raise RuntimeError('Main container basic shell probe failed.')
        if env.dependency_overlay:
            await env.upload_file(PACKAGE/'cad_runtime_probe.py', '/tmp/tb4-cad-runtime-probe.py')
            validation = await env.exec(command='xvfb-run -a -s "-screen 0 800x600x24" python /tmp/tb4-cad-runtime-probe.py',
                                        timeout_sec=120)
            if validation.return_code:
                raise RuntimeError('FreeCAD dependency ABI/render probe failed: '
                                   + str(validation.stdout) + str(validation.stderr))
            report['dependency_runtime_validation'] = json.loads(validation.stdout.strip().splitlines()[-1])
        if phase == 'agent' and config.mcp_servers:
            from .mcp_probe import probe_sse
            report['mcp_checks'] = []
            for server in config.mcp_servers:
                if server.transport != 'sse':
                    raise RuntimeError('Explicit prewarm probe needed for non-SSE task MCP.')
                parsed = urlsplit(server.url)
                service = next(x for x in containers if x['service'] == parsed.hostname)
                if not service['network_ips']:
                    raise RuntimeError('Task MCP sidecar has no reachable Docker network IP.')
                netloc = service['network_ips'][0] + (f':{parsed.port}' if parsed.port else '')
                url = urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))
                result = probe_sse(url)
                report['mcp_checks'].append({'server': server.name, **result})
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
            project = re.sub(r'[^a-z0-9_-]', '-', session.lower())
            remaining = run(['docker', 'ps', '-aq', '--filter',
                             f'label=com.docker.compose.project={project}']).split()
            if remaining:
                raise RuntimeError(f'Prewarm cleanup left {len(remaining)} containers.')
            report['cleaned_up'] = True
        except Exception as exc:
            report['cleanup_error'] = str(exc)
            report['cleaned_up'] = False
            report['status'] = 'failed'
        dump(Path(output) / 'report.json', report)


def prewarm(args):
    verify_boundary()
    data = manifest(args.manifest)
    verify_source_checkout(args.dataset, data['benchmark']['commit'])
    profile = load_profile()
    if set(profile['tasks']) != {task['task'] for task in data['tasks']}:
        raise ValueError('Manifest task selection differs from the reviewed resource profile.')
    if args.output.resolve() == (ROOT / 'runs/prewarm').resolve():
        raise RuntimeError('v5 must use a new prewarm directory; legacy evidence is immutable.')
    task_list = data['tasks']
    if args.task:
        task_list = [x for x in task_list if x['task'] in args.task]
        if len(task_list) != len(set(args.task)):
            raise ValueError('Unknown task selected.')
    args.output.mkdir(parents=True, exist_ok=True)
    # One host admission owner; each task always builds Agent then verifier.
    with host_lock():
        records = {}
        records_lock = threading.Lock()
        # Keep prior successful coverage visible immediately after a safe restart,
        # even if task ordering now checks sidecars before the resumed simple tasks.
        for task in task_list:
            for phase in ['agent', 'verifier']:
                existing_path = args.output / task['task'] / phase / 'report.json'
                if existing_path.exists():
                    previous = json.loads(existing_path.read_text())
                    records[(task['task'], phase)] = previous

        def save(result):
            with records_lock:
                records[(result['task'], result['phase'])] = result
                results = list(records.values())
                dump(args.output / 'summary.json', {'kind': 'environment_prewarm',
                     'manifest_sha256': hashlib.sha256(args.manifest.read_bytes()).hexdigest(),
                     'no_model_calls': True, 'updated_at': time.time(), 'results': results,
                     **dependency_identity(),
                     'max_concurrent_tasks': args.max_concurrent,
                     'passed': sum(x['status'] in ['passed', 'shared_environment'] for x in results),
                     'failed': sum(x['status'] == 'failed' for x in results)})

        def one_task(task):
            for phase in ['agent', 'verifier']:
                destination = args.output / task['task'] / phase
                report = destination / 'report.json'
                if report.exists() and not args.force:
                    existing = json.loads(report.read_text())
                    if (existing.get('status') in ['passed', 'shared_environment']
                        and existing.get('cleaned_up', False) and retained_images_present(existing)
                        and existing.get('preparation_identity') == dependency_identity()
                        and all((existing.get('resource_enforcement') or {}).get(key) == value
                                for key, value in resource_identity().items())):
                        save(existing)
                        continue
                if report.exists():
                    archive = destination/'attempt-history'/f'{int(time.time())}-{uuid.uuid4().hex[:8]}'
                    archive.mkdir(parents=True)
                    for previous in [report, destination/'build.log']:
                        if previous.exists():
                            shutil.copy2(previous, archive/previous.name)
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
                save(result)
                print(json.dumps({'event': 'prewarm_end', 'task': task['task'], 'phase': phase,
                                  'status': result['status']}), flush=True)
        # Two build workers fit with a fixed whole-phase slot plus4CPU/8GiB
        # build overhead each. Runtime service caps are enforced separately.
        def preparation_weight(task):
            return SLOT_CPUS + 4, SLOT_MEMORY_MB + 8192

        # Check multi-service environments early, including the selected MCP task.
        pending = sorted(task_list, key=lambda x: (x['task'] != 'medical-claims-processing',
                                                  not x['has_sidecars'], x['task']))
        active = {}
        with ThreadPoolExecutor(max_workers=args.max_concurrent) as executor:
            while pending or active:
                snapshot = disk_check(args.output)
                for task in pending[:]:
                    reservation = preparation_weight(task)
                    active_disks = [item[1] for item in active.values()]
                    if (len(active) < args.max_concurrent
                        and fits([item[0] for item in active.values()], reservation)
                        and disk_fits(snapshot, active_disks, task_disk_mb(task))):
                        future = executor.submit(one_task, task)
                        active[future] = (reservation, task_disk_mb(task))
                        pending.remove(task)
                if active:
                    done, _ = wait(active, timeout=1, return_when=FIRST_COMPLETED)
                    for future in done:
                        del active[future]
                        future.result()
                elif pending:
                    dump(args.output/'ADMISSION_PAUSED.json', {'reason': 'disk_reservation_unavailable',
                         'pending_tasks': len(pending), 'at': time.time(), 'no_model_calls': True})
                    raise RuntimeError('Prewarm disk reservation unavailable; tasks remain unstarted.')
        summary_path = args.output / 'summary.json'
        if summary_path.exists():
            summary = json.loads(summary_path.read_text())
            summary['retained_image_phases'] = sum(retained_images_present(x) for x in records.values()
                                                     if x['status'] in ['passed', 'shared_environment'])
            summary.update(dependency_identity())
            dump(summary_path, summary)
        if any(x['status'] == 'failed' for x in records.values()):
            raise SystemExit(1)


async def trial_worker(task_dir, agent_file, output, result_file):
    from harbor.models.trial.config import TrialConfig, AgentConfig, EnvironmentConfig, TaskConfig, ResourceMode
    from harbor.models.environment_type import EnvironmentType
    from harbor.trial.trial import Trial
    from .environment import PreparedDockerEnvironment
    verify_boundary()
    agent = AgentConfig.model_validate(json.loads(agent_file.read_text()))
    if not agent.import_path and (not agent.name or agent.name in ['oracle', 'nop']):
        raise ValueError('An explicit non-oracle Agent is required.')
    config = TrialConfig(task=TaskConfig(path=task_dir),
                         trial_name=output.name, trials_dir=output.parent,
                         agent=agent, environment=EnvironmentConfig(type=EnvironmentType.DOCKER,
                         import_path='adapters.harbor.tb4_control.environment:PreparedDockerEnvironment',
                         cpu_enforcement_policy=ResourceMode.LIMIT,
                         memory_enforcement_policy=ResourceMode.LIMIT))
    try:
        trial = await Trial.create(config)
        if trial.agent.name() in {'oracle', 'nop'}:
            raise ValueError('Reference-solution and no-op agents are forbidden here.')
        result = await trial.run()
        PreparedDockerEnvironment.assert_trial_clean(output)
        dump(result_file, result.model_dump(mode='json'))
    except BaseException as exc:
        # Keep Harbor's raw result/reward for audit; cleanup faults override
        # outcome classification and can never become a hidden scored zero.
        raw = result.model_dump(mode='json') if 'result' in locals() else {}
        try:
            PreparedDockerEnvironment.assert_trial_clean(output)
        except Exception as cleanup:
            exc = cleanup
        dump(result_file, {**raw, 'controller_error': {'type': type(exc).__name__, 'message': str(exc)}})
        raise


def smoke(args):
    verify_boundary()
    task_dir = PACKAGE / 'smoke_task'
    label = 'transport-only' if args.transport_only else 'synthetic'
    output = args.output / (label + '-' + time.strftime('%Y%m%dT%H%M%S', time.gmtime()))
    output.mkdir(parents=True)
    description = 'No-model shell transport preflight' if args.transport_only else 'Synthetic model preflight only'
    print(f'{description}: {output}', flush=True)
    agent_file = args.agent_config
    if args.transport_only:
        agent_file = output / 'transport-agent.json'
        dump(agent_file, {'import_path': 'adapters.harbor.tb4_control.synthetic_agent:ShellTransportProbe'})
    asyncio.run(trial_worker(task_dir, agent_file, output, output / 'controller-result.json'))
    if args.transport_only:
        result = json.loads((output / 'controller-result.json').read_text())
        reward = (result.get('verifier_result') or {}).get('rewards', {}).get('reward')
        if reward != 1:
            raise RuntimeError('Synthetic transport preflight verifier did not return reward=1.')
        print('Transport-only synthetic verifier reward=1; model requests=0.', flush=True)


def db_connect(path):
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA journal_mode=WAL')
    db.executescript('''
      CREATE TABLE IF NOT EXISTS slots (
        id TEXT PRIMARY KEY, task TEXT NOT NULL, repeat INTEGER NOT NULL,
        cpus REAL NOT NULL, memory_mb INTEGER NOT NULL, disk_mb INTEGER NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending');
      CREATE TABLE IF NOT EXISTS attempts (
        slot_id TEXT NOT NULL, attempt INTEGER NOT NULL, status TEXT NOT NULL,
        started REAL, finished REAL, pid INTEGER, result_path TEXT,
        PRIMARY KEY(slot_id, attempt));
      CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    ''')
    return db


def freeze(db, values):
    """Reject a resumed run if any replay-affecting input changed."""
    prepared = []
    for key, value in values.items():
        encoded = json.dumps(value, sort_keys=True, separators=(',', ':'))
        previous = db.execute('SELECT value FROM metadata WHERE key=?', (key,)).fetchone()
        if previous is not None and previous['value'] != encoded:
            raise RuntimeError(f'Frozen run input changed: {key}')
        prepared.append((key, encoded))
    db.executemany('INSERT OR IGNORE INTO metadata VALUES(?,?)', prepared)
    db.commit()


def selected_slots(path, agent, server, task_names):
    records = [json.loads(x) for x in path.read_text().splitlines() if x.strip()]
    if len(records) != 504 or len({x['model'] for x in records}) != 1:
        raise ValueError('Frozen 504-slot plan must use one identical model across all Agents.')
    selected = [x for x in records if x['agent'] == agent and x['server'] == server]
    pairs = {(x['task'], x['repeat']) for x in selected}
    expected = {(task, repeat) for task in task_names for repeat in range(1, 4)}
    if len(selected) != 168 or len({x['slot_id'] for x in selected}) != 168 or pairs != expected:
        raise ValueError('Frozen trial plan must contain 56 tasks x3 for this agent/server.')
    if len({x['model'] for x in selected}) != 1 or any(x['reasoning_effort'] != 'max' for x in selected):
        raise ValueError('Unexpected model or reasoning effort in frozen trial plan.')
    return selected


def classify(result):
    return classify_result(result)


def persist_admission_pause(output, reason, slot=None, attempt=None, error_type=None):
    safe = {'reason': reason, 'slot_id': slot, 'attempt': attempt,
            'error_type': error_type, 'at': time.time(), 'active_policy': 'drain'}
    dump(output / 'ADMISSION_PAUSED.json', safe)
    (output / 'PAUSE_ADMISSION').touch()


def read_worker_result(path):
    try:
        result = json.loads(path.read_text())
        if not isinstance(result, dict):
            raise ValueError('Invalid result mapping')
        return result
    except (OSError, ValueError, UnicodeError):
        return {'controller_error': {'type': 'InvalidWorkerResult'}}


def next_slot_status(result, attempt):
    status = classify(result)
    return 'pending' if status == 'infra_failure' and attempt < 3 else status


def formal(args):
    verify_boundary()
    # No deployment command creates this token. An operator must separately approve formal start.
    planned = [json.loads(line) for line in args.trial_plan.read_text().splitlines() if line.strip()]
    experiments = {row['slot_id'].split('__')[0] for row in planned}
    if len(experiments) != 1:
        raise ValueError('Frozen trial plan mixes experiment IDs.')
    expected = {'experiment': experiments.pop(), 'allow_formal': True,
                'manifest_sha256': hashlib.sha256(args.manifest.read_bytes()).hexdigest(),
                'agent_config_sha256': hashlib.sha256(args.agent_config.read_bytes()).hexdigest(),
                'resource_profile_sha256': hashlib.sha256(args.resource_profile.read_bytes()).hexdigest(),
                'trial_plan_sha256': hashlib.sha256(args.trial_plan.read_bytes()).hexdigest(),
                'agent': args.agent, 'server': args.server}
    if not args.unlock.is_file() or json.loads(args.unlock.read_text()) != expected:
        raise RuntimeError('Formal evaluation locked: explicit matching approval file required.')
    data = manifest(args.manifest)
    source_head = verify_source_checkout(args.dataset, data['benchmark']['commit'])
    if args.resource_profile.read_bytes() != PROFILE_PATH.read_bytes():
        raise RuntimeError('Approved resource profile must match the packaged runtime profile exactly.')
    profile = load_profile(args.resource_profile)
    weighted = {x['task']: weights(x, profile) for x in data['tasks']}
    disk_reservations = {task['task']: task_disk_mb(task) for task in data['tasks']}
    slots = selected_slots(args.trial_plan, args.agent, args.server, weighted)
    declared_model = json.loads(args.agent_config.read_text()).get('model_name', '')
    if declared_model.removesuffix('[1m]') != slots[0]['model']:
        raise RuntimeError('AgentConfig model_name must match the model frozen in trial-plan.jsonl.')
    if any(not fits([], x) for x in weighted.values()):
        raise RuntimeError('One task exceeds admission budget.')
    args.output.mkdir(parents=True, exist_ok=True)
    if args.output.resolve() == (ROOT / 'runs/formal').resolve():
        raise RuntimeError('v5 must use a new formal output; the legacy batch is immutable.')
    with host_lock():
        prewarm_bytes = args.prewarm_summary.read_bytes()
        prewarm_summary = json.loads(prewarm_bytes)
        if (prewarm_summary.get('passed') != 112 or prewarm_summary.get('failed') != 0
            or prewarm_summary.get('retained_image_phases') != 112
            or prewarm_summary.get('manifest_sha256') != expected['manifest_sha256']):
            raise RuntimeError('All 112 environment phases and retained images must pass for the frozen manifest.')
        verify_retained_image_readiness(prewarm_summary, weighted)
        verify_dependency_readiness(prewarm_summary, args.prewarm_summary.parent, args.dataset)
        verify_resource_readiness(prewarm_summary)
        resource_probe_bytes = args.resource_probe_summary.read_bytes()
        verify_coexistence_readiness(json.loads(resource_probe_bytes), profile)
        db = db_connect(args.output / 'state.sqlite3')
        try:
            freeze(db, {**expected, 'dataset_path': str(args.dataset.resolve()),
                        'dataset_commit': source_head,
                        'prewarm_summary_sha256': hashlib.sha256(prewarm_bytes).hexdigest(),
                        'resource_probe_summary_sha256': hashlib.sha256(resource_probe_bytes).hexdigest(),
                        'controller_sha256': tree_digest(PACKAGE),
                        'agent_adapter_sha256': tree_digest(PACKAGE.parent / 'tb4_agents'),
                        'adapter_assets_sha256': tree_digest(PACKAGE.parent / 'assets'),
                        'release_assets_sha256': tree_digest(ROOT / 'releases'),
                        'cpu_budget': CPU_BUDGET, 'memory_mb_budget': MEMORY_MB_BUDGET,
                        'disk_reservations_mb': disk_reservations, 'disk_free_floor_percent': 20,
                        'max_concurrent': MAX_CONCURRENT})
        except BaseException:
            db.close()
            raise
        if db.execute("SELECT 1 FROM attempts WHERE status='running'").fetchone():
            db.close()
            raise RuntimeError('Previous active attempts need operator reconciliation; do not duplicate them.')
        for slot in slots:
            cpus, memory = weighted[slot['task']]
            db.execute('INSERT OR IGNORE INTO slots(id,task,repeat,cpus,memory_mb,disk_mb) VALUES(?,?,?,?,?,?)',
                       (slot['slot_id'], slot['task'], slot['repeat'], cpus, memory, disk_reservations[slot['task']]))
        db.commit()
        active = {}
        stop = False
        requested_stop = threading.Event()
        previous_sigint = signal.getsignal(signal.SIGINT)

        def pause_admission(signum, frame):
            requested_stop.set()
            print('Admission paused; active attempts will finish and be recorded.', flush=True)

        signal.signal(signal.SIGINT, pause_admission)
        try:
            while True:
                for slot_id, item in list(active.items()):
                    process, reservation, result_file, attempt, log = item
                    if process.poll() is None:
                        continue
                    log.close()
                    result = read_worker_result(result_file)
                    assessment = assess_outcome(result)
                    status = assessment['status']
                    next_status = next_slot_status(result, attempt)
                    db.execute('UPDATE attempts SET status=?,finished=? WHERE slot_id=? AND attempt=?',
                               (status, time.time(), slot_id, attempt))
                    db.execute('UPDATE slots SET status=? WHERE id=?', (next_status, slot_id))
                    db.commit()
                    del active[slot_id]
                    if assessment['pause_admission']:
                        error = result.get('controller_error') or result.get('exception_info') or {}
                        error = error if isinstance(error, dict) else {}
                        error_type = error.get('type') or error.get('exception_type')
                        # Only typed identifiers are written; no exception body,
                        # prompts, HTTP response text or credentials enter this file.
                        error_type = error_type if isinstance(error_type, str) and re.fullmatch(r'[A-Za-z0-9_]{1,100}', error_type) else None
                        persist_admission_pause(args.output, assessment['reason'], slot_id, attempt, error_type)
                        stop = True
                if not stop:
                    try:
                        snapshot = disk_check(args.output)
                    except RuntimeError:
                        persist_admission_pause(args.output, 'disk_free_below_floor')
                        stop = True
                    if (args.output / 'PAUSE_ADMISSION').exists() or requested_stop.is_set():
                        stop = True
                pending = db.execute("SELECT * FROM slots WHERE status='pending' ORDER BY cpus*memory_mb DESC,task,repeat").fetchall()
                if not active and (not pending or stop):
                    break
                for slot in ([] if stop else pending):
                    reservation = (slot['cpus'], slot['memory_mb'])
                    if not fits([x[1] for x in active.values()], reservation):
                        continue
                    active_disks = [disk_reservations[row['task']] for row in
                        db.execute("SELECT task FROM slots WHERE status='running'")]
                    try:
                        snapshot = disk_check(args.output)
                    except RuntimeError:
                        persist_admission_pause(args.output, 'disk_free_below_floor')
                        stop = True
                        break
                    if not disk_fits(snapshot, active_disks, slot['disk_mb']):
                        continue
                    attempt = db.execute('SELECT count(*) FROM attempts WHERE slot_id=?', (slot['id'],)).fetchone()[0] + 1
                    # Harbor uses trial_name as its Docker Compose project identity.
                    # A plain attempt-1 name would collide across concurrent slots.
                    identity = hashlib.sha256(slot['id'].encode()).hexdigest()[:10]
                    directory = args.output / slot['id'] / f'attempt-{attempt}__{identity}'
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
                if pending and not active and not stop:
                    persist_admission_pause(args.output, 'disk_reservation_unavailable')
                    stop = True
                time.sleep(1)
            counts = dict(db.execute('SELECT status,count(*) FROM slots GROUP BY status'))
            report = {'slots': counts, 'planned_slots': 168,
                      'all_planned_slots_complete': counts.get('complete', 0) == 168}
        except KeyboardInterrupt:
            print('Interrupted; active attempts remain recorded for reconciliation.', file=sys.stderr)
            raise
        finally:
            signal.signal(signal.SIGINT, previous_sigint)
            db.close()
        return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ['plan', 'status']:
        sub = commands.add_parser(name)
        sub.add_argument('--manifest', type=Path, default=ROOT / 'plans/terminal-bench-4-evaluation/task-manifest.json')
        sub.add_argument('--output', type=Path, default=ROOT / 'runs/prewarm-v5')
    warm = commands.add_parser('prewarm', help='Build/start/probe/stop environments only; zero model calls')
    warm.add_argument('--manifest', type=Path, default=ROOT / 'plans/terminal-bench-4-evaluation/task-manifest.json')
    warm.add_argument('--dataset', type=Path, default=ROOT / 'src/terminal-bench')
    warm.add_argument('--output', type=Path, default=ROOT / 'runs/prewarm-v5')
    warm.add_argument('--sample-seconds', type=int, default=10)
    warm.add_argument('--max-concurrent', type=int, choices=[1, 2], default=1)
    warm.add_argument('--task', action='append')
    warm.add_argument('--force', action='store_true')
    phase = commands.add_parser('_prewarm-phase', help=argparse.SUPPRESS)
    phase.add_argument('--task-dir', type=Path, required=True)
    phase.add_argument('--phase', choices=['agent', 'verifier'], required=True)
    phase.add_argument('--output', type=Path, required=True)
    phase.add_argument('--sample-seconds', type=int, default=10)
    smoke_parser = commands.add_parser('smoke', help='One synthetic model preflight, never a benchmark task')
    smoke_agent = smoke_parser.add_mutually_exclusive_group(required=True)
    smoke_agent.add_argument('--agent-config', type=Path)
    smoke_agent.add_argument('--transport-only', action='store_true')
    smoke_parser.add_argument('--output', type=Path, default=ROOT / 'runs/preflight-v5')
    worker = commands.add_parser('_trial-worker', help=argparse.SUPPRESS)
    worker.add_argument('--task-dir', type=Path, required=True)
    worker.add_argument('--agent-config', type=Path, required=True)
    worker.add_argument('--output', type=Path, required=True)
    worker.add_argument('--result-file', type=Path, required=True)
    official = commands.add_parser('formal-start', help='Requires separate formal approval file; never used during preparation')
    for name, default in [('manifest', ROOT/'plans/terminal-bench-4-evaluation/task-manifest.json'),
                          ('dataset', ROOT/'src/terminal-bench'), ('output', ROOT/'runs/formal-v5'),
                          ('unlock', ROOT/'plans/FORMAL_APPROVED.json')]:
        official.add_argument('--' + name, type=Path, default=default)
    official.add_argument('--agent-config', type=Path, required=True)
    official.add_argument('--resource-profile', type=Path, default=PROFILE_PATH)
    official.add_argument('--trial-plan', type=Path, default=ROOT/'plans/terminal-bench-4-evaluation/trial-plan.jsonl')
    official.add_argument('--agent', choices=['codex', 'claude-code', 'skills-agent'], required=True)
    official.add_argument('--server', choices=['serverA', 'serverB', 'serverC'], required=True)
    official.add_argument('--prewarm-summary', type=Path, default=ROOT/'runs/prewarm-v5/summary.json')
    official.add_argument('--resource-probe-summary', type=Path, default=ROOT/'runs/resource-probe-v5/summary.json')
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
        report = formal(args)
        print(json.dumps(report, indent=2))
        if not report['all_planned_slots_complete']:
            raise SystemExit(2)
    elif args.command == 'plan':
        data = manifest(args.manifest)
        description = {'selected_tasks': len(data['tasks']), 'trials_per_agent': 168,
                       'cpu_budget': CPU_BUDGET, 'memory_mb_budget': MEMORY_MB_BUDGET,
                       'slot_cpus': SLOT_CPUS, 'slot_memory_mb': SLOT_MEMORY_MB,
                       'max_concurrent': MAX_CONCURRENT, 'formal_enabled': False}
        trial_plan = args.manifest.with_name('trial-plan.jsonl')
        if trial_plan.exists():
            rows = [json.loads(line) for line in trial_plan.read_text().splitlines() if line.strip()]
            description.update(experiments=sorted({row['slot_id'].split('__')[0] for row in rows}),
                models=sorted({row['model'] for row in rows}),
                reasoning_efforts=sorted({row['reasoning_effort'] for row in rows}),
                frozen_trial_plan_sha256=hashlib.sha256(trial_plan.read_bytes()).hexdigest())
        print(json.dumps(description, indent=2))
    elif args.command == 'status':
        path = args.output / 'summary.json'
        database = args.output / 'state.sqlite3'
        if database.exists():
            db = sqlite3.connect(f'file:{database}?mode=ro', uri=True)
            try:
                slot_counts = dict(db.execute('SELECT status,count(*) FROM slots GROUP BY status'))
                attempt_counts = dict(db.execute('SELECT status,count(*) FROM attempts GROUP BY status'))
                active = db.execute("SELECT count(*),coalesce(sum(cpus),0),coalesce(sum(memory_mb),0) FROM slots WHERE status='running'").fetchone()
                print(json.dumps({'slots': slot_counts, 'attempts': attempt_counts,
                      'active_reservations': {'slots': active[0], 'cpus': active[1], 'memory_mb': active[2]}}, indent=2))
            finally:
                db.close()
        else:
            print(path.read_text() if path.exists() else '{"status":"not_started"}')


if __name__ == '__main__':
    main()
