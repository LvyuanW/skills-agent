"""Explicit dependency repair for three verifier images, preserving task sources."""
import hashlib
import json
from pathlib import Path
import tempfile

from harbor.environments.docker.docker import DockerEnvironment
from .control import dependency_identity
from .resources import (PHASE_GUARD, EnvironmentCleanupError, EnvironmentHealthError,
                        inspect_project, project_residuals, resource_overlay, validate_health, validate_live_caps)

DIRECTORY = Path(__file__).resolve().parent
PROFILE_PATH = DIRECTORY/'dependency-profile.json'
PROFILE = json.loads(PROFILE_PATH.read_text())
ORIGINAL_LINE = "RUN pip install --no-cache-dir 'gnucleus-freecad-validator[render]==0.1.3'"


def build_overlay(environment_dir, environment_name):
    directory = Path(environment_dir)
    if directory.name != 'tests' or environment_name not in PROFILE['tasks']:
        return None
    original = (directory/'Dockerfile').read_text()
    if original.splitlines().count(ORIGINAL_LINE) != 1:
        raise RuntimeError('FreeCAD verifier Dockerfile does not match the audited dependency line exactly once.')
    replacement = ORIGINAL_LINE + ''.join(f" '{pin}'" for pin in PROFILE['pins'])
    effective = ''.join(replacement + line[len(ORIGINAL_LINE):]
        if line.rstrip('\r\n') == ORIGINAL_LINE else line
        for line in original.splitlines(keepends=True))
    # Compose interpolates dollar expressions before handing the inline
    # Dockerfile to BuildKit; preserve Dockerfile ENV expansion verbatim.
    overlay = {'services': {'main': {'build': {'dockerfile_inline': effective.replace('$', '$$')}}}}
    metadata = {**dependency_identity(), 'profile': PROFILE['name'],
        'official_sources_modified': False, 'validator_or_tests_modified': False,
        'original_dockerfile_sha256': hashlib.sha256(original.encode()).hexdigest(),
        'effective_dockerfile_sha256': hashlib.sha256(effective.encode()).hexdigest(),
        'overlay_sha256': hashlib.sha256(json.dumps(overlay, sort_keys=True).encode()).hexdigest(),
        'pins': PROFILE['pins']}
    return overlay, metadata


class PreparedDockerEnvironment(DockerEnvironment):
    def __init__(self, *args, **kwargs):
        prepared = build_overlay(kwargs['environment_dir'], kwargs['environment_name'])
        self.dependency_overlay = None
        self._tb4_overlay_dir = None
        if prepared:
            overlay, self.dependency_overlay = prepared
            self._tb4_overlay_dir = tempfile.TemporaryDirectory(prefix='tb4-freecad-dependency-')
            path = Path(self._tb4_overlay_dir.name)/'compose.json'
            path.write_text(json.dumps(overlay))
            kwargs['extra_docker_compose'] = [*(kwargs.get('extra_docker_compose') or []), path]
        super().__init__(*args, **kwargs)
        self._tb4_trial_key = str(self.trial_paths.trial_dir.resolve())
        self._tb4_phase = 'verifier' if Path(self.environment_dir).name == 'tests' else 'agent'
        self._tb4_operator_stopped = set()
        self._tb4_started = False
        overlay, self.resource_enforcement = resource_overlay(
            self.environment_dir, self.environment_name, self.task_env_config,
            helper=self._enable_egress_control)
        self._tb4_resource_dir = tempfile.TemporaryDirectory(prefix='tb4-fixed-resources-')
        path = Path(self._tb4_resource_dir.name) / 'compose.json'
        path.write_text(json.dumps(overlay))
        self.extra_docker_compose_paths.append(path)

    def _tb4_health(self):
        records = inspect_project(self.session_id)
        self.resource_enforcement['last_health_snapshot'] = records
        validate_health(records, self._tb4_operator_stopped)
        if self._tb4_started:
            validate_live_caps(records, self.resource_enforcement['service_caps'])
        return records

    async def start(self, force_build):
        PHASE_GUARD.register(self._tb4_trial_key, self.session_id, self._tb4_phase)
        try:
            await super().start(force_build)
            self._tb4_started = True
            records = self._tb4_health()
            self.resource_enforcement['live_caps_verified'] = True
            self.resource_enforcement['startup_health_verified'] = True
            self.resource_enforcement['containers'] = records
        except (EnvironmentHealthError, EnvironmentCleanupError) as exc:
            PHASE_GUARD.fail(self._tb4_trial_key, self.session_id, type(exc).__name__)
            raise

    async def stop_service(self, service):
        # Harbor deliberately stops main before downloading sidecar artifacts.
        # Inspect before that operation so natural failures cannot be hidden by it.
        try:
            self._tb4_health()
            await super().stop_service(service)
            self._tb4_operator_stopped.add(service)
        except BaseException as exc:
            PHASE_GUARD.fail(self._tb4_trial_key, self.session_id, type(exc).__name__)
            raise

    async def stop(self, delete):
        health_error = None
        try:
            self._tb4_health()
        except Exception as exc:
            health_error = exc
            PHASE_GUARD.fail(self._tb4_trial_key, self.session_id, type(exc).__name__)
        try:
            await super().stop(delete)
            remaining = project_residuals(self.session_id)
            self.resource_enforcement['cleanup_remaining'] = remaining
            if any(remaining.values()):
                raise EnvironmentCleanupError('Environment cleanup left project containers, networks or volumes.')
            self.resource_enforcement['cleanup_verified'] = True
            if health_error:
                raise health_error
        except BaseException as exc:
            PHASE_GUARD.fail(self._tb4_trial_key, self.session_id, type(exc).__name__)
            raise
        finally:
            if self._tb4_overlay_dir:
                self._tb4_overlay_dir.cleanup()
            if self._tb4_resource_dir:
                self._tb4_resource_dir.cleanup()

    @staticmethod
    def assert_trial_clean(output):
        # Trial.run can swallow environment stop errors. Recheck outside Harbor
        # before publishing a controller outcome, even when a reward exists.
        PHASE_GUARD.check_clean(str(Path(output).resolve()))
