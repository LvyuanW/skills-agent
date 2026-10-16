"""Model-free checks for admission, persistence, retries and launch guards."""
import json
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import queue
import subprocess
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from .control import (CPU_BUDGET, MEMORY_MB_BUDGET, classify, db_connect,
                      fits, formal, freeze, host_lock, next_slot_status, selected_slots,
                      verify_source_checkout, weights)
from .mcp_probe import probe_sse
from . import control


class ControlTests(unittest.TestCase):
    def test_retained_inventory_rechecks_deleted_and_retagged_docker_aliases(self):
        images = {}
        reports = []
        for task in ['fixture-a', 'fixture-b']:
            for phase in ['agent', 'verifier']:
                alias = f'tb4-prewarm/{task}-{phase}:original'
                image = f'sha256:{task}-{phase}'
                images[alias] = image
                reports.append({'task': task, 'phase': phase, 'status': 'passed',
                    'cleaned_up': True, 'containers': [{'image': image, 'retained_image_alias': alias}]})
        # The old summary continues to claim full coverage after Docker changes.
        summary = {'passed': 112, 'retained_image_phases': 112, 'results': reports}

        def inspect(command):
            self.assertEqual(command[:3], ['docker', 'image', 'inspect'])
            if any(alias not in images for alias in command[3:]):
                raise subprocess.CalledProcessError(1, command)
            return json.dumps([{'Id': images[alias]} for alias in command[3:]])

        with patch.object(control, 'run', side_effect=inspect):
            control.verify_retained_image_readiness(summary, ['fixture-a', 'fixture-b'])
            alias, original = next(iter(images.items()))
            del images[alias]
            with self.assertRaisesRegex(RuntimeError, 'retained images missing or changed'):
                control.verify_retained_image_readiness(summary, ['fixture-a', 'fixture-b'])
            images[alias] = 'sha256:another-image-under-same-alias'
            with self.assertRaisesRegex(RuntimeError, 'retained images missing or changed'):
                control.verify_retained_image_readiness(summary, ['fixture-a', 'fixture-b'])
            images[alias] = original
            control.verify_retained_image_readiness(summary, ['fixture-a', 'fixture-b'])
            summary['results'] = reports[:-1] + [reports[0]]
            with self.assertRaisesRegex(RuntimeError, 'exactly once'):
                control.verify_retained_image_readiness(summary, ['fixture-a', 'fixture-b'])

    def test_prewarm_identity_requires_matching_profile_and_render_success(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            identity = {'dependency_profile_sha256': 'profile-v1',
                        'environment_adapter_sha256': 'adapter-v1', 'runtime_probe_sha256': 'probe-v1'}
            reports = []
            for task in control.DEPENDENCY_TASKS:
                source = base/'dataset/tasks'/task/'tests/Dockerfile'
                source.parent.mkdir(parents=True)
                source.write_text('FROM fixture\n')
                report = base/'prewarm'/task/'verifier/report.json'
                report.parent.mkdir(parents=True)
                data = {'status': 'passed', 'dependency_overlay': {**identity,
                    'original_dockerfile_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
                    'effective_dockerfile_sha256': 'effective', 'overlay_sha256': 'overlay'},
                    'dependency_runtime_validation': {'passed': True}}
                report.write_text(json.dumps(data))
                reports.append((report, data))
            with patch.object(control, 'dependency_identity', return_value=identity), \
                 patch.object(control, 'expected_dependency_overlay', return_value=(None,
                    {'effective_dockerfile_sha256': 'effective', 'overlay_sha256': 'overlay'})):
                control.verify_dependency_readiness(identity, base/'prewarm', base/'dataset')
                with self.assertRaisesRegex(RuntimeError, 'identity differs'):
                    control.verify_dependency_readiness({**identity, 'environment_adapter_sha256': 'changed'},
                                                       base/'prewarm', base/'dataset')
                report, data = reports[0]
                data['dependency_overlay']['overlay_sha256'] = 'wrong-compose-overlay'
                report.write_text(json.dumps(data))
                with self.assertRaisesRegex(RuntimeError, 'ABI/render probe required'):
                    control.verify_dependency_readiness(identity, base/'prewarm', base/'dataset')
                data['dependency_overlay']['overlay_sha256'] = 'overlay'
                data['dependency_runtime_validation']['passed'] = False
                report.write_text(json.dumps(data))
                with self.assertRaisesRegex(RuntimeError, 'ABI/render probe required'):
                    control.verify_dependency_readiness(identity, base/'prewarm', base/'dataset')

    def test_dataset_requires_exact_head_and_unchanged_task_sources(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            subprocess.run(['git', 'init', '-q', str(base)], check=True)
            source = base/'tasks/example/task.toml'
            source.parent.mkdir(parents=True)
            source.write_text('version = "original"\n')
            subprocess.run(['git', '-C', str(base), 'add', 'tasks'], check=True)
            subprocess.run(['git', '-C', str(base), '-c', 'user.name=Transport Test',
                '-c', 'user.email=transport-test@localhost', 'commit', '-q', '-m', 'fixture'], check=True)
            head = subprocess.check_output(['git', '-C', str(base), 'rev-parse', 'HEAD'], text=True).strip()
            self.assertEqual(verify_source_checkout(base, head), head)
            with self.assertRaisesRegex(RuntimeError, 'HEAD does not match'):
                verify_source_checkout(base, '0'*40)
            source.write_text('version = "changed"\n')
            with self.assertRaisesRegex(RuntimeError, 'task sources'):
                verify_source_checkout(base, head)
            source.write_text('version = "original"\n')
            (source.parent/'extra.py').write_text('# untracked build input\n')
            with self.assertRaisesRegex(RuntimeError, 'task sources'):
                verify_source_checkout(base, head)

    def test_formal_guard_creates_no_slots_without_approval(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            for name in ['manifest.json', 'agent.json', 'sidecars.json', 'plan.jsonl']:
                (base / name).write_text('{"slot_id":"test-experiment__codex__task__r01"}'
                                        if name == 'plan.jsonl' else '{}')
            args = SimpleNamespace(manifest=base/'manifest.json', agent_config=base/'agent.json',
                resource_profile=base/'sidecars.json', trial_plan=base/'plan.jsonl',
                agent='codex', server='serverA', unlock=base/'FORMAL_APPROVED.json', output=base/'formal')
            with patch('adapters.harbor.tb4_control.control.verify_boundary', return_value={}):
                with self.assertRaisesRegex(RuntimeError, 'Formal evaluation locked'):
                    formal(args)
            self.assertFalse(args.output.exists())
            self.assertFalse(list(base.rglob('state.sqlite3')))

    def test_sse_probe_negotiates_and_calls_only_read_only_browser_list(self):
        messages = queue.Queue()
        methods = []
        stopped = threading.Event()

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream')
                self.end_headers()
                self.wfile.write(b'event: endpoint\ndata: /messages?sessionId=test\n\n')
                self.wfile.flush()
                while not stopped.is_set():
                    try:
                        response = messages.get(timeout=.1)
                    except queue.Empty:
                        continue
                    self.wfile.write(('event: message\ndata: ' + json.dumps(response) + '\n\n').encode())
                    self.wfile.flush()

            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                methods.append((payload['method'], payload.get('params')))
                method = payload['method']
                result = {'protocolVersion': '2024-11-05'} if method == 'initialize' else (
                    {'tools': [{'name': 'browser_tabs'}]} if method == 'tools/list' else {'isError': False})
                if 'id' in payload:
                    messages.put({'jsonrpc': '2.0', 'id': payload['id'], 'result': result})
                self.send_response(202)
                self.send_header('Content-Length', '0')
                self.end_headers()

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            result = probe_sse(f'http://127.0.0.1:{server.server_port}/sse', timeout=5)
            self.assertTrue(result['read_only_browser_tabs_succeeded'])
            self.assertEqual([x[0] for x in methods],
                             ['initialize', 'notifications/initialized', 'tools/list', 'tools/call'])
            self.assertEqual(methods[-1][1], {'name': 'browser_tabs', 'arguments': {'action': 'list'}})
        finally:
            stopped.set()
            server.shutdown()
            server.server_close()

    def test_exact_cpu_and_memory_boundary(self):
        self.assertTrue(fits([(5, 21*1024)] * 4, (5, 21*1024)))
        self.assertFalse(fits([(5, 21*1024)] * 5, (1, 1)))
        self.assertTrue(fits([(5, 1024)] * 4, (8, MEMORY_MB_BUDGET - 4096)))
        self.assertFalse(fits([(5, 1024)] * 4, (8.01, 1024)))
        self.assertFalse(fits([(5, 1024)] * 4, (8, MEMORY_MB_BUDGET - 4096 + 1)))

    def test_mixed_queue_never_oversubscribes(self):
        pending = [(4, 16384), (2, 4096), (4, 8192), (3, 12288), (2, 32768)] * 20
        active = []
        peak = [0, 0, 0]
        while pending or active:
            for request in pending[:]:
                if fits(active, request):
                    active.append(request)
                    pending.remove(request)
            peak = [max(peak[0], sum(x[0] for x in active)),
                    max(peak[1], sum(x[1] for x in active)), max(peak[2], len(active))]
            self.assertLessEqual(peak[0], CPU_BUDGET)
            self.assertLessEqual(peak[1], MEMORY_MB_BUDGET)
            self.assertLessEqual(peak[2], 5)
            if active:
                active.pop(0)

    def test_all_tasks_get_same_fixed_whole_phase_slot(self):
        profile = control.load_profile()
        for task, record in profile['tasks'].items():
            manifest_task = {'task': task, 'main_resources': record['agent'],
                             'verifier_effective_resources': record['verifier']}
            self.assertEqual(weights(manifest_task, profile), (5, 21*1024))
        with self.assertRaisesRegex(ValueError, 'lacks'):
            weights({'task': 'unknown'}, profile)

    def test_disk_reservations_include_live_trials_and_floor(self):
        gib = 1024**3
        snapshot = {'total': 500*gib, 'free': 230*gib}
        self.assertTrue(control.disk_fits(snapshot, [10*1024]*4, 10*1024))
        self.assertTrue(control.disk_fits(snapshot, [50*1024], 50*1024))
        self.assertFalse(control.disk_fits(snapshot, [50*1024]*2, 50*1024))
        self.assertTrue(control.disk_fits({'total': 500*gib, 'free': 150*gib}, [], 50*1024))
        self.assertFalse(control.disk_fits({'total': 500*gib, 'free': 150*gib-1}, [], 50*1024))

    def test_invalid_worker_results_are_typed_without_copying_body(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'result.json'
            for raw in ['[1,2]', 'null', '"private body"', '{broken']:
                path.write_text(raw)
                self.assertEqual(control.read_worker_result(path),
                    {'controller_error': {'type': 'InvalidWorkerResult'}})
            path.unlink()
            self.assertEqual(control.read_worker_result(path),
                {'controller_error': {'type': 'InvalidWorkerResult'}})

    def test_zero_and_one_rewards_are_terminal(self):
        for reward in [0, 1]:
            self.assertEqual(classify({'verifier_result': {'rewards': {'reward': reward}}}), 'complete')

    def test_agent_error_never_infra_retry(self):
        for error in ['AgentTimeoutError', 'AgentSetupTimeoutError', 'RuntimeError', 'AgentProcessError']:
            self.assertEqual(classify({'exception_info': {'exception_type': error}}), 'needs_review')
        self.assertEqual(classify({'exception_info': {'exception_type': 'AgentTimeoutError'},
                                   'verifier_result': {'rewards': {'reward': 0}}}), 'complete')
        self.assertEqual(classify({'exception_info': {'exception_type': 'AgentProcessError'},
                                   'verifier_result': {'rewards': {'reward': 0}}}), 'needs_review')

    def test_known_provider_and_configuration_faults_override_zero_reward(self):
        for fault in ['AgentAuthenticationError', 'ApiOverloadedError',
                      'ModelNotFoundError', 'UnsupportedModelParametersError']:
            result = {'exception_info': {'exception_type': fault},
                      'verifier_result': {'rewards': {'reward': 0}}}
            self.assertEqual(classify(result), 'needs_review')
            self.assertEqual([next_slot_status(result, attempt) for attempt in [1, 2, 3]],
                             ['needs_review', 'needs_review', 'needs_review'])
        # Exhausting the Agent's own context/output strategy remains an Agent
        # outcome, unlike a broken provider configuration.
        for fault in ['OutputTokenExceededError', 'ContextWindowExceededError']:
            self.assertEqual(classify({'exception_info': {'exception_type': fault},
                'verifier_result': {'rewards': {'reward': 0}}}), 'complete')

    def test_only_explicit_infrastructure_can_retry(self):
        self.assertEqual(classify({'exception_info': {'exception_type': 'EnvironmentStartTimeoutError'}}),
                         'infra_failure')
        self.assertEqual(classify({}), 'needs_review')
        self.assertEqual(classify({'verifier_result': {'rewards': {}}}), 'needs_review')
        provider_failure = {'exception_info': {'exception_type': 'EnvironmentStartTimeoutError'}}
        self.assertEqual([next_slot_status(provider_failure, a) for a in [1, 2, 3]],
                         ['pending', 'pending', 'infra_failure'])
        self.assertEqual(next_slot_status({'verifier_result': {'rewards': {'reward': 0}}}, 1),
                         'complete')

    def test_persistence_and_freeze_across_reopen(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'state.sqlite3'
            db = db_connect(path)
            freeze(db, {'model': 'deepseek-flash', 'effort': 'max'})
            db.execute('INSERT INTO slots VALUES(?,?,?,?,?,?,?)', ('s', 'task', 1, 2, 4096, 10240, 'running'))
            db.execute('INSERT INTO attempts VALUES(?,?,?,?,?,?,?)', ('s', 1, 'running', 1., None, 123, '/result'))
            db.commit()
            db.close()
            db = db_connect(path)
            self.assertEqual(db.execute('SELECT status FROM slots').fetchone()['status'], 'running')
            freeze(db, {'model': 'deepseek-flash', 'effort': 'max'})
            with self.assertRaisesRegex(RuntimeError, 'Frozen run input changed'):
                freeze(db, {'effort': 'low'})
            db.close()

    def test_host_lock_across_different_job_directories(self):
        with tempfile.TemporaryDirectory() as temp:
            lock = Path(temp) / '.resource-controller.lock'
            with host_lock(lock):
                with self.assertRaises(BlockingIOError):
                    with host_lock(lock):
                        self.fail('Concurrent resource owner admitted')
            with host_lock(lock):
                pass

    def test_frozen_504_plan_preserves_slot_ids(self):
        repository = Path(__file__).resolve().parents[3]
        plan = repository / 'docs/terminal-bench-4-evaluation/trial-plan.jsonl'
        manifest = repository / 'docs/terminal-bench-4-evaluation/task-manifest.json'
        if not plan.exists():
            plan = Path('/data/tb4/plans/terminal-bench-4-evaluation/trial-plan.jsonl')
            manifest = plan.with_name('task-manifest.json')
        if not plan.exists():
            self.skipTest('Repository planning assets unavailable on deployment host')
        tasks = [x['task'] for x in json.loads(manifest.read_text())['tasks']]
        for agent, server in [('codex', 'serverA'), ('claude-code', 'serverB'), ('skills-agent', 'serverC')]:
            slots = selected_slots(plan, agent, server, tasks)
            self.assertEqual(len(slots), 168)
            self.assertTrue(all(agent in x['slot_id'] for x in slots))


if __name__ == '__main__':
    unittest.main()
