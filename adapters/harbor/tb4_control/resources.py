"""Audited fixed experimental service caps and fail-closed lifecycle checks.

No credentials, question text or Docker Config.Env are returned by this module.
Task storage declarations are soft admission reservations, not Docker quotas.
"""
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess

DIRECTORY = Path(__file__).resolve().parent
PROFILE_PATH = DIRECTORY / 'resource-profile.json'


class ResourceConfigurationError(RuntimeError):
    pass


class EnvironmentHealthError(RuntimeError):
    pass


class EnvironmentCleanupError(RuntimeError):
    pass


def resource_identity():
    return {key: hashlib.sha256((DIRECTORY / name).read_bytes()).hexdigest()
            for key, name in [('resource_profile_sha256', 'resource-profile.json'),
                              ('resource_enforcer_sha256', 'resources.py')]}


def phase_caps(profile, task, phase, helper=False):
    record = profile['tasks'][task]
    caps = {'main': {key: record[phase][key] for key in ['cpus', 'memory_mb']}}
    if phase == 'agent':
        caps.update({name: {key: value[key] for key in ['cpus', 'memory_mb']}
                     for name, value in record['sidecars'].items()})
    if helper:
        extra = profile['harbor_egress_helper']
        caps[extra['service']] = {key: extra[key] for key in ['cpus', 'memory_mb']}
    return caps


def validate_profile(profile):
    admission = profile.get('admission', {})
    if admission != {'cpus': 28, 'memory_mb': 107 * 1024,
                     'slot_cpus': 5, 'slot_memory_mb': 21 * 1024,
                     'max_concurrent': 5, 'policy': 'uniform_fixed_slots'}:
        raise ResourceConfigurationError('Only the reviewed v5 five-slot resource policy is accepted.')
    if len(profile.get('tasks', {})) != 56:
        raise ResourceConfigurationError('Resource profile must cover exactly 56 tasks.')
    sidecars = 0
    for task, record in profile['tasks'].items():
        sidecars += len(record['sidecars'])
        for phase in ['agent', 'verifier']:
            official = record[phase]
            if official.get('gpus') or official['cpus'] > 4 or official['memory_mb'] > 16384:
                raise ResourceConfigurationError('Unexpected official main resource declaration.')
            helper = official['network_mode'] != 'public'
            caps = phase_caps(profile, task, phase, helper)
            for cap in caps.values():
                if (not isinstance(cap['cpus'], (float, int)) or not math.isfinite(cap['cpus'])
                        or cap['cpus'] <= 0 or type(cap['memory_mb']) is not int or cap['memory_mb'] <= 0):
                    raise ResourceConfigurationError('Each service needs a positive finite CPU/RAM cap.')
            total = {'cpus': sum(cap['cpus'] for cap in caps.values()),
                     'memory_mb': sum(cap['memory_mb'] for cap in caps.values())}
            if total != record['phase_envelope'][phase]:
                raise ResourceConfigurationError('Recorded whole-phase envelope differs from service caps.')
            if total['cpus'] > 5 or total['memory_mb'] > 21 * 1024:
                raise ResourceConfigurationError('A whole phase exceeds the fixed slot.')
    if sidecars != 20:
        raise ResourceConfigurationError('Expected 20 reviewed sidecar service caps.')
    return profile


def load_profile(path=PROFILE_PATH):
    return validate_profile(json.loads(Path(path).read_text()))


def resource_overlay(environment_dir, task, task_config, helper=False):
    """Return only resource overrides; all original Dockerfiles/Compose stay intact."""
    profile = load_profile()
    directory = Path(environment_dir)
    phase = 'verifier' if directory.name == 'tests' else 'agent'
    if task not in profile['tasks']:
        # Only the bundled synthetic task may use this environment outside the56.
        if directory.resolve().parent != (DIRECTORY / 'smoke_task').resolve():
            raise ResourceConfigurationError('Task is absent from the frozen resource profile.')
        caps = {'main': {'cpus': task_config.cpus, 'memory_mb': task_config.memory_mb}}
        if helper:
            extra = profile['harbor_egress_helper']
            caps[extra['service']] = {key: extra[key] for key in ['cpus', 'memory_mb']}
    else:
        record = profile['tasks'][task]
        actual = task_config.model_dump(mode='json')
        for key in ['cpus', 'memory_mb', 'storage_mb', 'build_timeout_sec', 'network_mode']:
            if actual[key] != record[phase][key]:
                raise ResourceConfigurationError('Effective task resources differ from the audited source.')
        if (actual.get('gpus') or 0) != record[phase]['gpus']:
            raise ResourceConfigurationError('GPU resource declaration differs from the audited source.')
        compose = directory / 'docker-compose.yaml'
        source = record['compose'] if phase == 'agent' else None
        if source:
            if not compose.is_file() or hashlib.sha256(compose.read_bytes()).hexdigest() != source['sha256']:
                raise ResourceConfigurationError('Task Compose differs from the audited source.')
        elif compose.exists() or (directory / 'docker-compose.yml').exists():
            raise ResourceConfigurationError('Unexpected task Compose without reviewed service caps.')
        caps = phase_caps(profile, task, phase, helper)
    if (sum(cap['cpus'] for cap in caps.values()) > 5
            or sum(cap['memory_mb'] for cap in caps.values()) > 21*1024):
        raise ResourceConfigurationError('Effective services exceed one reviewed fixed slot.')
    services = {name: {'cpus': cap['cpus'], 'mem_limit': f"{cap['memory_mb']}M",
                       'memswap_limit': f"{cap['memory_mb']}M", 'cgroup_parent': 'tb4.slice'}
                for name, cap in caps.items()}
    overlay = {'services': services}
    metadata = {**resource_identity(), 'profile_id': profile['profile_id'], 'phase': phase,
                'experimental_sidecar_limits': True, 'official_main_limits_preserved': True,
                'service_caps': caps,
                'overlay_sha256': hashlib.sha256(json.dumps(overlay, sort_keys=True).encode()).hexdigest()}
    return overlay, metadata


def project_name(session):
    name = session.lower()
    if not re.match(r'^[a-z0-9]', name):
        name = '0' + name
    return re.sub(r'[^a-z0-9_-]', '-', name)


def inspect_project(session):
    project = project_name(session)
    ids = subprocess.run(['docker', 'ps', '-aq', '--filter',
                          f'label=com.docker.compose.project={project}'],
                         check=True, text=True, capture_output=True, timeout=30).stdout.split()
    if not ids:
        return []
    # Project only safe fields inside Docker, so full Env is never materialized.
    template = ('{{json .Id}} {{json .Config.Labels}} {{json .State}} '
                '{{json .HostConfig}} {{json .RestartCount}}')
    output = subprocess.run(['docker', 'inspect', '--format', template, *ids], check=True,
                            text=True, capture_output=True, timeout=30).stdout
    records = []
    decoder = json.JSONDecoder()
    for line in output.splitlines():
        fields, rest = [], line
        while rest.strip():
            value, end = decoder.raw_decode(rest.lstrip())
            fields.append(value)
            rest = rest.lstrip()[end:]
        identity, labels, state, host, restarts = fields
        records.append({'id': identity, 'service': labels.get('com.docker.compose.service'),
            'project': labels.get('com.docker.compose.project'), 'running': state.get('Running', False),
            'status': state.get('Status'), 'pid': state.get('Pid', 0),
            'oom_killed': state.get('OOMKilled', False), 'exit_code': state.get('ExitCode', 0),
            'health': (state.get('Health') or {}).get('Status'), 'restart_count': restarts,
            'nano_cpus': host.get('NanoCpus', 0), 'memory': host.get('Memory', 0),
            'memory_swap': host.get('MemorySwap', 0), 'cgroup_parent': host.get('CgroupParent')})
    return records


def project_residuals(session):
    selector = f'label=com.docker.compose.project={project_name(session)}'
    commands = {'containers': ['docker', 'ps', '-aq', '--filter', selector],
                'networks': ['docker', 'network', 'ls', '-q', '--filter', selector],
                'volumes': ['docker', 'volume', 'ls', '-q', '--filter', selector]}
    return {kind: subprocess.run(command, check=True, text=True, capture_output=True,
                                timeout=30).stdout.split()
            for kind, command in commands.items()}


def validate_live_caps(records, caps):
    services = [record['service'] for record in records]
    if len(services) != len(set(services)) or set(services) != set(caps):
        raise ResourceConfigurationError('Live project services differ from the reviewed cap inventory.')
    for record in records:
        cap = caps[record['service']]
        if (record['nano_cpus'] != int(cap['cpus'] * 10**9)
                or record['memory'] != cap['memory_mb'] * 1024**2
                or record['memory_swap'] != cap['memory_mb'] * 1024**2
                or record['cgroup_parent'] != 'tb4.slice'):
            raise ResourceConfigurationError('Live container CPU/RAM/swap/parent differs from hard caps.')
        if record['running']:
            cgroup = Path(f"/proc/{record['pid']}/cgroup").read_text()
            if '/tb4.slice/' not in cgroup:
                raise ResourceConfigurationError('Live container escaped the parent cgroup.')


def validate_health(records, operator_stopped=()):
    for record in records:
        service = record['service']
        if record['oom_killed'] or record['restart_count']:
            raise EnvironmentHealthError(f'{service}: OOM or unexpected container restart.')
        if record.get('health') == 'unhealthy':
            raise EnvironmentHealthError(f'{service}: container healthcheck failed.')
        # All20 reviewed task sidecars and main keepalive services are long
        # lived; even exit0 before an explicit Harbor stop means lost service.
        if not record['running'] and service not in operator_stopped:
            raise EnvironmentHealthError(f'{service}: unexpected natural container exit.')


class PhaseGuard:
    """One process-local registry per Trial; verifier cannot overlap failed cleanup."""
    def __init__(self):
        self.environments = {}
        self.failures = {}

    def register(self, key, session, phase):
        if phase == 'verifier':
            self.check_clean(key, except_session=session)
        self.environments.setdefault(key, set()).add(session)

    def fail(self, key, session, kind):
        self.failures.setdefault(key, {})[session] = kind

    def check_clean(self, key, except_session=None):
        if self.failures.get(key):
            raise EnvironmentCleanupError('Prior environment health/cleanup failed; next phase is blocked.')
        for session in self.environments.get(key, set()):
            if session != except_session and any(project_residuals(session).values()):
                raise EnvironmentCleanupError('Prior environment project resources remain; next phase is blocked.')


PHASE_GUARD = PhaseGuard()
