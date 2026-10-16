"""Full 168-slot controller simulation. No approval file or real worker exists."""
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from . import control


class FakeSchedulerTest(unittest.TestCase):
    def exercise(self, mode="normal"):
        """No real approval, worker, model, or environment is created."""
        repository = Path(__file__).resolve().parents[3]
        source = repository / 'docs/terminal-bench-4-evaluation'
        if not source.exists():
            source = Path('/data/tb4/plans/terminal-bench-4-evaluation')
        if not source.exists():
            self.skipTest('Frozen planning inputs unavailable')
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            args = SimpleNamespace(manifest=base/'manifest.json', agent_config=base/'agent.json',
                resource_profile=control.PROFILE_PATH, trial_plan=base/'plan.jsonl',
                agent='codex', server='serverA', output=base/'runs/formal-v5',
                dataset=base/'dataset', prewarm_summary=base/'prewarm.json',
                resource_probe_summary=base/'resource-probe.json')
            args.manifest.write_bytes((source/'task-manifest.json').read_bytes())
            args.trial_plan.write_bytes((source/'trial-plan.jsonl').read_bytes())
            pinned_model = json.loads(args.trial_plan.read_text().splitlines()[0])['model']
            args.agent_config.write_text(json.dumps({'import_path': 'mock:NeverImported', 'model_name': pinned_model}))
            manifest = json.loads(args.manifest.read_text())
            profile = control.load_profile()
            checksum = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
            experiment = json.loads(args.trial_plan.read_text().splitlines()[0])['slot_id'].split('__')[0]
            approval = {'experiment': experiment, 'allow_formal': True,
                'manifest_sha256': checksum(args.manifest), 'agent_config_sha256': checksum(args.agent_config),
                'resource_profile_sha256': checksum(args.resource_profile),
                'trial_plan_sha256': checksum(args.trial_plan), 'agent': args.agent, 'server': args.server}

            # Mock the approval interface in memory. Never create an unlock file.
            args.unlock = SimpleNamespace(is_file=lambda: True, read_text=lambda: json.dumps(approval))
            args.prewarm_summary.write_text(json.dumps({'passed': 112, 'failed': 0, 'retained_image_phases': 112,
                'manifest_sha256': checksum(args.manifest)}))
            args.resource_probe_summary.write_text('{}')
            weights = {task['task']: control.weights(task, profile) for task in manifest['tasks']}
            calls, outcomes, observed = [], {}, []
            package = base/'control/tb4_control'
            package.mkdir(parents=True)
            catalog = package.parent/'assets/codex/models.json'
            catalog.parent.mkdir(parents=True)
            catalog.write_text('{"fixture":"v1"}')
            releases = base/'releases'
            releases.mkdir()
            wheel = releases/'agent.whl'
            wheel.write_bytes(b'fixture-release-v1')

            class Worker:
                def __init__(self, command, **kwargs):
                    self.pid = 10000 + len(calls)
                    self.command = command
                    result_path = Path(command[command.index('--result-file') + 1])
                    task = Path(command[command.index('--task-dir') + 1]).name
                    slot = result_path.parent.parent.name
                    attempts = outcomes.get(slot, 0) + 1
                    outcomes[slot] = attempts
                    calls.append((slot, attempts))
                    # Observe real persisted admission state before this worker
                    # is inserted, then account for the current reservation.
                    db = sqlite3.connect(args.output/'state.sqlite3')
                    count, cpu, memory = db.execute("SELECT count(*),coalesce(sum(cpus),0),coalesce(sum(memory_mb),0) FROM slots WHERE status='running'").fetchone()
                    db.close()
                    observed.append((count + 1, cpu + weights[task][0], memory + weights[task][1]))
                    if mode == 'provider' and len(calls) == 1:
                        result = {'exception_info': {'exception_type': 'ApiOverloadedError', 'exception_message': 'PRIVATE gateway body'},
                                  'verifier_result': {'rewards': {'reward': 0}}}
                    elif mode == 'normal' and slot == calls[0][0] and attempts == 1:
                        result = {'exception_info': {'exception_type': 'EnvironmentStartTimeoutError'}}
                    else:
                        # A scored Agent timeout is terminal, including zero.
                        result = {'verifier_result': {'rewards': {'reward': 0}},
                                  'exception_info': {'exception_type': 'AgentTimeoutError'}}
                    if mode == 'nested' and len(calls) == 1:
                        result = {'controller_error': 'PRIVATE non-mapping error',
                                  'verifier_result': {'rewards': {'reward': 0}}}
                    result_path.write_text('{broken JSON' if mode == 'corrupt' and len(calls) == 1 else json.dumps(result))

                def poll(self):
                    return 0

            with patch.object(control, 'ROOT', base), \
                 patch.object(control, 'PACKAGE', package), \
                 patch.object(control, 'verify_boundary', return_value={}), \
                 patch.object(control, 'disk_check', return_value={'total': 500*1024**3, 'free': (101 if mode == 'disk' else 400)*1024**3}), \
                 patch.object(control, 'run', return_value=manifest['benchmark']['commit']), \
                 patch.object(control, 'verify_source_checkout', return_value=manifest['benchmark']['commit']), \
                 patch.object(control, 'verify_retained_image_readiness', return_value=None), \
                 patch.object(control, 'verify_dependency_readiness', return_value=None), \
                 patch.object(control, 'verify_resource_readiness', return_value=None), \
                 patch.object(control, 'verify_coexistence_readiness', return_value=None), \
                 patch.object(control.subprocess, 'Popen', Worker), \
                 patch.object(control.time, 'sleep', return_value=None):
                first_report = control.formal(args)
                if mode in ['provider', 'disk', 'corrupt', 'nested']:
                    expected = {'complete': 4, 'needs_review': 1, 'pending': 163} if mode != 'disk' else {'pending': 168}
                    self.assertEqual(first_report['slots'], expected)
                    self.assertEqual(len(calls), 0 if mode == 'disk' else 5)
                    pause = json.loads((args.output/'ADMISSION_PAUSED.json').read_text())
                    self.assertEqual(pause['reason'], {'provider': 'provider_or_setup_failure',
                        'disk': 'disk_reservation_unavailable', 'corrupt': 'unclassified_execution_failure',
                        'nested': 'unclassified_execution_failure'}[mode])
                    if mode == 'corrupt':
                        self.assertEqual(pause['error_type'], 'InvalidWorkerResult')
                    self.assertNotIn('PRIVATE', json.dumps(pause))
                    self.assertTrue((args.output/'PAUSE_ADMISSION').exists())
                    previous = len(calls)
                    control.formal(args)
                    self.assertEqual(len(calls), previous)
                    return
                self.assertTrue(first_report['all_planned_slots_complete'])
                self.assertEqual(first_report['slots'], {'complete': 168})
                before_resume = len(calls)
                control.formal(args)
                self.assertEqual(len(calls), before_resume)
                original_prewarm = args.prewarm_summary.read_bytes()
                rewarmed = json.loads(original_prewarm)
                rewarmed['results'] = [{'task': 'fixture', 'phase': 'agent',
                    'containers': [{'image': 'sha256:replacement-after-explicit-prewarm'}]}]
                args.prewarm_summary.write_text(json.dumps(rewarmed))
                with self.assertRaisesRegex(RuntimeError, 'prewarm_summary_sha256'):
                    control.formal(args)
                self.assertEqual(len(calls), before_resume)
                args.prewarm_summary.write_bytes(original_prewarm)
                control.formal(args)
                self.assertEqual(len(calls), before_resume)
                catalog.write_text('{"fixture":"v2"}')
                with self.assertRaisesRegex(RuntimeError, 'adapter_assets_sha256'):
                    control.formal(args)
                catalog.write_text('{"fixture":"v1"}')
                wheel.write_bytes(b'fixture-release-v2')
                with self.assertRaisesRegex(RuntimeError, 'release_assets_sha256'):
                    control.formal(args)
                wheel.write_bytes(b'fixture-release-v1')
                # Even a newly approved config cannot silently change an
                # existing batch; the SQLite replay lock remains authoritative.
                args.agent_config.write_text(json.dumps({'import_path': 'mock:ChangedNeverImported', 'model_name': pinned_model}))
                approval['agent_config_sha256'] = checksum(args.agent_config)
                with self.assertRaisesRegex(RuntimeError, 'Frozen run input changed'):
                    control.formal(args)
                self.assertEqual(len(calls), before_resume)
            db = sqlite3.connect(args.output/'state.sqlite3')
            counts = dict(db.execute('SELECT status,count(*) FROM slots GROUP BY status'))
            attempt_count = db.execute('SELECT count(*) FROM attempts').fetchone()[0]
            db.close()
            self.assertEqual(counts, {'complete': 168})
            self.assertEqual(attempt_count, 169)
            self.assertEqual(len(outcomes), 168)
            self.assertEqual(sorted(outcomes.values()).count(2), 1)
            self.assertTrue(all(count <= 5 and cpu <= 28 and memory <= 107*1024
                                for count, cpu, memory in observed))
            self.assertFalse(list(base.rglob('FORMAL_APPROVED.json')))

    def test_persistent_168_slots_with_one_infra_replacement(self):
        self.exercise()

    def test_provider_failure_pauses_drains_and_persists_without_body(self):
        self.exercise('provider')

    def test_no_disk_candidate_stays_pending_and_pauses(self):
        self.exercise('disk')

    def test_corrupt_worker_result_pauses_and_drains_without_resampling(self):
        self.exercise('corrupt')

    def test_nonmapping_nested_error_pauses_and_drains_without_body(self):
        self.exercise('nested')


if __name__ == '__main__':
    unittest.main()
