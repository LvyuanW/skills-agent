"""Replay cached public runtime records under the exclusive-sidecar protocol.

This does not run tasks or models. The input is an audit export of public Harbor
trial durations, never a set of new evaluation results.
"""
import argparse
import hashlib
import heapq
import json
from pathlib import Path
import random
import statistics


def replay(model, tasks, sidecars):
    groups = {task: [] for task in tasks}
    for record in model['runs']:
        if record['task'] in groups:
            groups[record['task']].append(record)
    results, exclusive_hours = [], []
    for seed in range(100):
        randomgen = random.Random(seed)
        queue = []
        for task in sorted(groups):
            for record in randomgen.sample(groups[task], 3):
                cpu, memory = ((28, 107) if task in sidecars else
                    (max(record['cpu'], record['verifier_cpu']),
                     max(record['memory_gib'], record['verifier_memory_gib'])))
                queue.append({'task': task, 'cpu': cpu, 'memory': memory,
                              'duration': record['total'], 'i': len(queue)})
        exclusive_hours.append(sum(r['duration'] for r in queue if r['task'] in sidecars)/3600)
        queue.sort(key=lambda r: (-r['cpu']*r['memory'], r['task'], r['i']))
        active, now, cpu, memory = [], 0, 0, 0
        while queue or active:
            for record in queue[:]:
                if len(active) < 14 and cpu+record['cpu'] <= 28 and memory+record['memory'] <= 107:
                    queue.remove(record)
                    cpu += record['cpu']
                    memory += record['memory']
                    heapq.heappush(active, (now+record['duration'], record['i'], record))
            end, _, record = heapq.heappop(active)
            now = end
            cpu -= record['cpu']
            memory -= record['memory']
        results.append(now/3600)
    ordered = sorted(results)
    return {'key': model['key'], 'historical_label': model['label'],
            'median_h': round(statistics.median(results), 2),
            'p10_h_nearest_rank': round(ordered[9], 2),
            'p90_h_nearest_rank': round(ordered[89], 2),
            'mean_exclusive_trial_hours': round(statistics.mean(exclusive_hours), 2)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime-input', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text())
    models = json.loads(args.runtime_input.read_text())
    tasks = {task['task'] for task in manifest['tasks']}
    sidecars = {task['task'] for task in manifest['tasks'] if task['has_sidecars']}
    record = {'kind': 'historical_runtime_scheduling_replay', 'date': '2026-10-16',
        'not_flash_max_prediction': True, 'not_new_agent_evaluation': True,
        'no_model_calls': True,
        'source_input_sha256': hashlib.sha256(args.runtime_input.read_bytes()).hexdigest(),
        'manifest_sha256': hashlib.sha256(args.manifest.read_bytes()).hexdigest(),
        'benchmark_commit': manifest['benchmark']['commit'],
        'protocol': {'tasks': 56, 'repeats_per_task': 3, 'trials_per_historical_run': 168,
            'exclusive_tasks': sorted(sidecars), 'exclusive_slots': 27,
            'cpu_budget': 28, 'memory_gib_budget': 107, 'max_concurrent': 14,
            'sample_seeds': list(range(100)), 'sampling': 'Sample 3 of 5 public runtimes for each task.',
            'queue': 'Reserved CPU x memory descending, task ascending, sampled repeat ascending; backfill fitting requests.',
            'reservation': 'Hold heaviest phase resources for whole trial; sidecar tasks reserve entire pool.'},
        'limitations': ['Public timings use other models and Agent versions; they are not Flash/max timings.',
            'Cold preparation, local API gateway latency and current model token generation speed are not calibrated.',
            'Repeated-run uncertainty is an empirical resampling range, not a confidence interval for current Agents.'],
        'sources': ['https://www.tbench.ai/blog/terminal-bench-4-0/rollout-charts.html',
            'https://hub.harborframework.com/jobs/5aebbab5-1f8f-4cdb-8073-c6ce785a930d',
            'https://hub.harborframework.com/jobs/a1ac63a1-8a9b-4bc7-9906-2b63657ee1c2',
            'https://hub.harborframework.com/jobs/a5758f1a-9ef2-4100-9893-34a99c99bd9c'],
        'results': [replay(model, tasks, sidecars) for model in models
                    if model['key'] in ['sol', 'opus5', 'sonnet5']]}
    args.output.write_text(json.dumps(record, indent=2, ensure_ascii=False)+'\n')


if __name__ == '__main__':
    main()
