"""Five isolated copies of a sidecar task; environment API only, zero model calls.

Runs health requests, a tiny shell/file probe and medical read-only MCP tabs.
This is bounded preparation evidence, never workload peak calibration or scores.
"""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import shlex
import time
import uuid

from .control import (ROOT, dependency_identity, disk_check, disk_fits, dump, host_lock,
                      project_containers, run, verify_boundary, verify_source_checkout)
from .resources import inspect_project, load_profile, project_residuals, validate_health, validate_live_caps


def memory_events():
    path = Path('/sys/fs/cgroup/tb4.slice/memory.events')
    return {key: int(value) for key, value in (line.split() for line in path.read_text().splitlines())}


def project_networks(records):
    ids = [record['id'] for record in records]
    networks = run(['docker', 'inspect', '--format', '{{json .NetworkSettings.Networks}}', *ids])
    return sorted({value['NetworkID'] for line in networks.splitlines()
                   for value in json.loads(line).values() if value.get('NetworkID')})


def health_request(record):
    """Execute the image's existing healthcheck once, discarding response bodies."""
    health = json.loads(run(['docker', 'inspect', '--format', '{{json .Config.Healthcheck}}', record['id']]))
    test = (health or {}).get('Test')
    if not test or test[0] == 'NONE':
        return {'service': record['service'], 'image_healthcheck': False}
    if test[0] == 'CMD':
        command = test[1:]
    elif test[0] == 'CMD-SHELL':
        command = ['sh', '-c', test[1]]
    else:
        raise RuntimeError('Unsupported healthcheck transport.')
    run(['docker', 'exec', record['id'], *command], timeout=30)
    return {'service': record['service'], 'image_healthcheck': True, 'request_succeeded': True}


async def probe_task(task_name, dataset, destination):
    from harbor.environments.factory import EnvironmentFactory
    from harbor.models.environment_type import EnvironmentType
    from harbor.models.task.task import Task
    from harbor.models.trial.config import EnvironmentConfig, ResourceMode
    from harbor.models.trial.paths import TrialPaths
    from .mcp_probe import probe_sse
    from urllib.parse import urlsplit, urlunsplit

    profile = load_profile()
    record = profile['tasks'][task_name]
    if not record['sidecars']:
        raise ValueError('Resource coexistence probe is for the nine reviewed sidecar tasks.')
    task = Task(dataset/'tasks'/task_name)
    task_config = task.config.environment
    storage = max(record[phase]['storage_mb'] for phase in ['agent', 'verifier'])
    envs, reports = [], []
    before = memory_events()
    report = {'task': task_name, 'copies': 5, 'kind': 'bounded_resource_coexistence_probe',
        'no_model_calls': True, 'no_agent': True, 'no_reference_solution': True,
        'no_official_verifier_or_scores': True, 'not_workload_peak_evidence': True,
        'preparation_identity': dependency_identity(), 'started_at': time.time(), 'projects': reports}
    try:
        if not disk_fits(disk_check(destination), [], storage*5):
            raise RuntimeError('Five-project declared disk reservation does not fit before startup.')
        for index in range(5):
            if not disk_fits(disk_check(destination), [storage]*len(envs), storage):
                raise RuntimeError('Disk declarations prevent all five simultaneous copies.')
            paths = TrialPaths(destination/f'copy-{index+1}')
            paths.mkdir()
            paths.chmod_dir()
            session = f'tb4-cap-probe-{task_name}-{index+1}-{uuid.uuid4().hex[:8]}'
            env = EnvironmentFactory.create_environment_from_config(
                EnvironmentConfig(type=EnvironmentType.DOCKER,
                    import_path='adapters.harbor.tb4_control.environment:PreparedDockerEnvironment',
                    cpu_enforcement_policy=ResourceMode.LIMIT, memory_enforcement_policy=ResourceMode.LIMIT),
                environment_dir=task.paths.environment_dir, environment_name=task.short_name,
                session_id=session, trial_paths=paths, task_env_config=task_config,
                network_policy=task_config.resolve_baseline())
            envs.append(env)
            await asyncio.wait_for(env.start(False), timeout=task_config.build_timeout_sec)
            containers = project_containers(session)
            networks = project_networks(containers)
            if not networks or any(set(networks) & set(old['network_ids']) for old in reports):
                raise RuntimeError('Independent projects must have disjoint private Docker networks.')
            token = uuid.uuid4().hex
            command = f"printf %s {shlex.quote(token)} > /tmp/tb4-resource-probe-token"
            shell = await env.exec(command=command, timeout_sec=30)
            if shell.return_code:
                raise RuntimeError('Synthetic shell/file write failed.')
            reports.append({'session': session, 'network_ids': networks, 'token_sha256': hashlib.sha256(token.encode()).hexdigest(),
                'containers': containers, 'resource_enforcement': env.resource_enforcement})
        # All five copies remain alive throughout these checks, including
        # existing HTTP/VNC/Redis health requests inside each sidecar.
        for env, project in zip(envs, reports):
            rows = inspect_project(env.session_id)
            validate_live_caps(rows, env.resource_enforcement['service_caps'])
            validate_health(rows)
            project['health_requests'] = [health_request(row) for row in rows]
            if not all(item.get('request_succeeded') for item in project['health_requests']
                       if item['service'] != 'main'):
                raise RuntimeError('Each reviewed long-lived sidecar needs a successful health request.')
            shell = await env.exec(command='cat /tmp/tb4-resource-probe-token', timeout_sec=30)
            if shell.return_code or hashlib.sha256(shell.stdout.encode()).hexdigest() != project['token_sha256']:
                raise RuntimeError('Independent shell/file probe isolation failed.')
            if task_name == 'medical-claims-processing':
                project['mcp_checks'] = []
                for server in task_config.mcp_servers:
                    parsed = urlsplit(server.url)
                    container = next(item for item in project['containers'] if item['service'] == parsed.hostname)
                    ip = container['network_ips'][0]
                    url = urlunsplit((parsed.scheme, ip+(f':{parsed.port}' if parsed.port else ''),
                                      parsed.path, parsed.query, parsed.fragment))
                    project['mcp_checks'].append(probe_sse(url))
            disk_check(destination)
        after = memory_events()
        if any(after.get(key, 0) != before.get(key, 0) for key in ['oom', 'oom_kill', 'oom_group_kill']):
            raise RuntimeError('Parent cgroup recorded an OOM during bounded coexistence checks.')
        report.update(status='passed', simultaneous_projects_verified=5,
            memory_events_before=before, memory_events_after=after)
    except BaseException as exc:
        report.update(status='failed', error_type=type(exc).__name__)
        raise
    finally:
        clean = True
        for env in reversed(envs):
            try:
                await env.stop(True)
                remaining = project_residuals(env.session_id)
                if any(remaining.values()):
                    raise RuntimeError('Project resources remain after coexistence cleanup.')
            except Exception as exc:
                clean = False
                report.setdefault('cleanup_errors', []).append({'session': env.session_id, 'type': type(exc).__name__})
        report['cleaned_up'] = clean
        report['finished_at'] = time.time()
        if not clean:
            report['status'] = 'failed'
        dump(destination/'report.json', report)
    if report['status'] != 'passed':
        raise RuntimeError('Coexistence probe did not pass cleanup.')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', type=Path, default=ROOT/'src/terminal-bench')
    parser.add_argument('--output', type=Path, default=ROOT/'runs/resource-probe-v5')
    parser.add_argument('--task', action='append')
    args = parser.parse_args()
    verify_boundary()
    profile = load_profile()
    verify_source_checkout(args.dataset, profile['benchmark_commit'])
    tasks = args.task or [name for name, record in profile['tasks'].items() if record['sidecars']]
    args.output.mkdir(parents=True, exist_ok=True)
    if args.output.resolve() in [(ROOT/'runs/prewarm').resolve(), (ROOT/'runs/formal').resolve()]:
        raise RuntimeError('Legacy evidence must not be modified.')
    with host_lock():
        reports = []
        for task in tasks:
            destination = args.output/task
            destination.mkdir(parents=True, exist_ok=False)
            reports.append(asyncio.run(probe_task(task, args.dataset, destination)))
            dump(args.output/'summary.json', {'kind': 'bounded_resource_coexistence_probe',
                'no_model_calls': True, 'reports': reports, 'passed': len(reports), 'failed': 0,
                'preparation_identity': dependency_identity()})


if __name__ == '__main__':
    main()
