"""Model-free enforcement and lifecycle regression checks, using fake Docker."""
import contextlib
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

from . import resources
from . import control
from . import resource_probe


def live(service='main', **changes):
    return {'id': 'fake', 'service': service, 'running': True, 'pid': 123,
            'oom_killed': False, 'restart_count': 0, 'exit_code': 0, 'health': None,
            'nano_cpus': 2*10**9, 'memory': 4096*1024**2,
            'memory_swap': 4096*1024**2, 'cgroup_parent': 'tb4.slice', **changes}


class ResourceTests(unittest.TestCase):
    def test_probe_handles_main_without_healthcheck_and_executes_sidecar_health(self):
        with patch.object(resource_probe, 'run', side_effect=['null']) as command:
            self.assertEqual(resource_probe.health_request(live()),
                             {'service': 'main', 'image_healthcheck': False})
            self.assertEqual(command.call_args.args[0][3], '{{json .Config.Healthcheck}}')
        with patch.object(resource_probe, 'run', side_effect=[
                json.dumps({'Test': ['CMD', 'curl', '-sf', 'http://localhost/health']}), '']
            ) as command:
            self.assertTrue(resource_probe.health_request(live('api'))['request_succeeded'])
            self.assertEqual(command.call_args.args[0][:3], ['docker', 'exec', 'fake'])

    def test_all56_and20_sidecars_fit_five_slots_including_helper(self):
        profile = resources.load_profile()
        self.assertEqual(len(profile['tasks']), 56)
        self.assertEqual(sum(bool(task['sidecars']) for task in profile['tasks'].values()), 9)
        self.assertEqual(sum(len(task['sidecars']) for task in profile['tasks'].values()), 20)
        envelopes = [record['phase_envelope'][phase] for record in profile['tasks'].values()
                     for phase in ['agent', 'verifier']]
        self.assertEqual(max(value['cpus'] for value in envelopes), 5)
        self.assertEqual(max(value['memory_mb'] for value in envelopes), 21*1024)
        helper = resources.phase_caps(profile, 'batched-eval-parity', 'verifier', True)
        self.assertEqual(sum(cap['cpus'] for cap in helper.values()), 1.25)
        self.assertEqual(sum(cap['memory_mb'] for cap in helper.values()), 4352)
        self.assertLessEqual(5*5, control.CPU_BUDGET)
        self.assertLessEqual(5*21*1024, control.MEMORY_MB_BUDGET)

    def test_rejects_budgets_over_slot_and_old_exclusive_policy(self):
        profile = resources.load_profile()
        broken = deepcopy(profile)
        broken['tasks']['ctr-optimization']['sidecars']['api']['cpus'] = 20
        with self.assertRaises(resources.ResourceConfigurationError):
            resources.validate_profile(broken)
        broken = deepcopy(profile)
        broken['admission']['max_concurrent'] = 14
        with self.assertRaises(resources.ResourceConfigurationError):
            resources.validate_profile(broken)

    def test_compose_override_preserves_main_and_caps_each_service_and_helper(self):
        profile = resources.load_profile()
        task = 'medical-claims-processing'
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)/'environment'
            directory.mkdir()
            compose = directory/'docker-compose.yaml'
            compose.write_text('services: {main: {}, playwright-mcp: {}, workspace: {}}\n')
            profile['tasks'][task]['compose']['sha256'] = resources.hashlib.sha256(compose.read_bytes()).hexdigest()
            config = SimpleNamespace(model_dump=lambda **kwargs: profile['tasks'][task]['agent'])
            with patch.object(resources, 'load_profile', return_value=profile):
                overlay, metadata = resources.resource_overlay(directory, task, config, False)
                self.assertEqual(overlay['services']['main']['cpus'], 2)
                self.assertEqual(overlay['services']['main']['mem_limit'], '16384M')
                self.assertEqual(overlay['services']['playwright-mcp']['cpus'], 2)
                self.assertEqual(overlay['services']['workspace']['memswap_limit'], '1024M')
                self.assertTrue(all(service['cgroup_parent'] == 'tb4.slice' for service in overlay['services'].values()))
                self.assertTrue(metadata['official_main_limits_preserved'])
                compose.write_text('services: {main: {}, unexpected: {}}\n')
                with self.assertRaisesRegex(resources.ResourceConfigurationError, 'Compose differs'):
                    resources.resource_overlay(directory, task, config)

    def test_live_cpu_memory_swap_parent_and_service_set_are_hard_checked(self):
        caps = {'main': {'cpus': 2, 'memory_mb': 4096}}
        with patch.object(Path, 'read_text', return_value='0::/tb4.slice/docker-test.scope'):
            resources.validate_live_caps([live()], caps)
            for field, value in [('nano_cpus', 0), ('memory', 0), ('memory_swap', -1),
                                  ('cgroup_parent', 'system.slice'), ('service', 'unexpected')]:
                with self.subTest(field=field), self.assertRaises(resources.ResourceConfigurationError):
                    resources.validate_live_caps([live(**{field: value})], caps)
        with patch.object(Path, 'read_text', return_value='0::/system.slice/docker-test.scope'):
            with self.assertRaisesRegex(resources.ResourceConfigurationError, 'escaped'):
                resources.validate_live_caps([live()], caps)

    def test_health_rejects_oom_restart_unhealthy_and_natural_zero_exit(self):
        for change in [{'oom_killed': True}, {'restart_count': 1}, {'health': 'unhealthy'},
                       {'running': False, 'exit_code': 137}, {'running': False, 'exit_code': 0}]:
            with self.subTest(change=change), self.assertRaises(resources.EnvironmentHealthError):
                resources.validate_health([live('api', **change)])
        resources.validate_health([live('main', running=False, exit_code=137)], {'main'})
        with self.assertRaises(resources.EnvironmentHealthError):
            resources.validate_health([live('main', running=False, oom_killed=True)], {'main'})

    def test_phase_guard_refuses_live_old_project_and_sticky_failed_cleanup(self):
        guard = resources.PhaseGuard()
        guard.register('trial', 'agent', 'agent')
        with patch.object(resources, 'project_residuals', return_value={'containers': ['fake'], 'networks': [], 'volumes': []}):
            with self.assertRaisesRegex(resources.EnvironmentCleanupError, 'remain'):
                guard.register('trial', 'verifier', 'verifier')
        with patch.object(resources, 'project_residuals', return_value={'containers': [], 'networks': [], 'volumes': []}):
            guard.register('trial', 'verifier', 'verifier')
            guard.fail('trial', 'agent', 'EnvironmentHealthError')
            with self.assertRaisesRegex(resources.EnvironmentCleanupError, 'Prior environment'):
                guard.check_clean('trial')

    def test_phase_guard_rejects_containerless_volume_leak(self):
        guard = resources.PhaseGuard()
        guard.register('trial', 'agent', 'agent')
        with patch.object(resources, 'project_residuals', return_value={
                'containers': [], 'networks': [], 'volumes': ['unremoved-volume']}):
            with self.assertRaisesRegex(resources.EnvironmentCleanupError, 'resources remain'):
                guard.register('trial', 'verifier', 'verifier')

    def test_every_phase_requires_matching_enforcement_and_preparation_identity(self):
        identity = control.dependency_identity()
        summary = {**identity, 'results': [{'preparation_identity': identity,
            'resource_enforcement': {**resources.resource_identity(),
                'live_caps_verified': True, 'cleanup_verified': True}}]}
        control.verify_resource_readiness(summary)
        summary['results'][0]['resource_enforcement']['cleanup_verified'] = False
        with self.assertRaisesRegex(RuntimeError, 'Every prewarm'):
            control.verify_resource_readiness(summary)

    def test_coexistence_gate_requires_all_nine_five_copies_and_current_probe(self):
        identity = control.dependency_identity()
        profile = resources.load_profile()
        metadata = {**resources.resource_identity(), 'live_caps_verified': True, 'cleanup_verified': True}
        reports = [{'task': task, 'status': 'passed', 'cleaned_up': True,
                    'preparation_identity': identity, 'simultaneous_projects_verified': 5,
                    'projects': [{'resource_enforcement': deepcopy(metadata)} for _ in range(5)]}
                   for task, record in profile['tasks'].items() if record['sidecars']]
        summary = {'no_model_calls': True, 'preparation_identity': identity,
                   'passed': 9, 'failed': 0, 'reports': reports}
        control.verify_coexistence_readiness(summary, profile)
        with patch.object(control, 'dependency_identity', return_value={**identity, 'resource_probe_sha256': 'changed'}):
            with self.assertRaisesRegex(RuntimeError, 'matching five-project'):
                control.verify_coexistence_readiness(summary, profile)
        reports[0]['simultaneous_projects_verified'] = 4
        with self.assertRaisesRegex(RuntimeError, 'incomplete'):
            control.verify_coexistence_readiness(summary, profile)
        reports[0]['simultaneous_projects_verified'] = 5
        reports[0]['projects'][0]['resource_enforcement']['cleanup_verified'] = False
        with self.assertRaisesRegex(RuntimeError, 'complete cleanup'):
            control.verify_coexistence_readiness(summary, profile)


class EnvironmentLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.records = {}
        self.volume_leaks = set()
        self.base_starts = []
        owner = self

        class FakeDocker:
            def __init__(self, **kwargs):
                self.__dict__.update(kwargs)
                self.extra_docker_compose_paths = []
                self._enable_egress_control = False
                self.leave_on_stop = False

            async def start(self, force_build):
                owner.base_starts.append(self.session_id)
                owner.records[self.session_id] = [live()]

            async def stop(self, delete):
                if not self.leave_on_stop:
                    owner.records[self.session_id] = []

            async def stop_service(self, service):
                owner.records[self.session_id] = [live(service, running=False, exit_code=137)]

        fake_modules = {name: ModuleType(name) for name in
                        ['harbor', 'harbor.environments', 'harbor.environments.docker',
                         'harbor.environments.docker.docker']}
        fake_modules['harbor.environments.docker.docker'].DockerEnvironment = FakeDocker
        self.modules = patch.dict(sys.modules, fake_modules)
        self.modules.start()
        name = 'adapters.harbor.tb4_control._environment_under_test'
        spec = importlib.util.spec_from_file_location(name, resources.DIRECTORY/'environment.py')
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.guard = resources.PhaseGuard()
        self.stack = contextlib.ExitStack()
        self.stack.enter_context(patch.object(self.module, 'PHASE_GUARD', self.guard))
        inspect = lambda session: deepcopy(self.records.get(session, []))
        self.stack.enter_context(patch.object(self.module, 'inspect_project', side_effect=inspect))
        self.stack.enter_context(patch.object(resources, 'inspect_project', side_effect=inspect))
        residuals = lambda session: {'containers': [item['id'] for item in inspect(session)],
            'networks': [], 'volumes': ['unremoved-volume'] if session in self.volume_leaks else []}
        self.stack.enter_context(patch.object(self.module, 'project_residuals', side_effect=residuals))
        self.stack.enter_context(patch.object(resources, 'project_residuals', side_effect=residuals))
        self.stack.enter_context(patch.object(self.module, 'validate_live_caps', return_value=None))
        self.stack.enter_context(patch.object(self.module, 'resource_overlay', return_value=(
            {'services': {'main': {}}}, {'service_caps': {'main': {'cpus': 2, 'memory_mb': 4096}}})))

    def environment(self, phase='agent'):
        directory = Path(self.temp.name)/('tests' if phase == 'verifier' else 'environment')
        return self.module.PreparedDockerEnvironment(environment_dir=directory,
            environment_name='fixture', session_id=phase,
            trial_paths=SimpleNamespace(trial_dir=Path(self.temp.name)), task_env_config=None)

    async def asyncTearDown(self):
        self.stack.close()
        self.modules.stop()
        self.temp.cleanup()

    async def test_swallowed_docker_down_failure_blocks_verifier_and_final_outcome(self):
        agent = self.environment()
        await agent.start(False)
        agent.leave_on_stop = True
        with self.assertRaises(resources.EnvironmentCleanupError):
            await agent.stop(True)
        verifier = self.environment('verifier')
        try:
            with self.assertRaises(resources.EnvironmentCleanupError):
                await verifier.start(False)
            self.assertEqual(self.base_starts, ['agent'])
            with self.assertRaises(resources.EnvironmentCleanupError):
                verifier.assert_trial_clean(Path(self.temp.name))
        finally:
            await verifier.stop(True)

    async def test_oom_then_successful_delete_still_blocks_scored_zero(self):
        agent = self.environment()
        await agent.start(False)
        self.records['agent'][0]['oom_killed'] = True
        with self.assertRaises(resources.EnvironmentHealthError):
            await agent.stop(True)
        self.assertFalse(self.records['agent'])
        with self.assertRaises(resources.EnvironmentCleanupError):
            agent.assert_trial_clean(Path(self.temp.name))

    async def test_swallowed_volume_cleanup_failure_rejects_success_and_next_phase(self):
        agent = self.environment()
        await agent.start(False)
        self.volume_leaks.add('agent')
        with self.assertRaises(resources.EnvironmentCleanupError):
            await agent.stop(True)
        self.assertFalse(self.records['agent'])
        self.assertFalse(agent.resource_enforcement.get('cleanup_verified', False))
        verifier = self.environment('verifier')
        try:
            with self.assertRaises(resources.EnvironmentCleanupError):
                await verifier.start(False)
        finally:
            await verifier.stop(True)

    async def test_explicit_main_stop_is_allowed_and_clean_phase_can_continue(self):
        agent = self.environment()
        await agent.start(False)
        await agent.stop_service('main')
        await agent.stop(True)
        verifier = self.environment('verifier')
        await verifier.start(False)
        await verifier.stop(True)
        verifier.assert_trial_clean(Path(self.temp.name))


if __name__ == '__main__':
    unittest.main()
