"""One-off final pass after the already running prewarm service succeeds.

This is a continuation of preparation, never a timer or recurring monitor.
It does not retry a failed/incomplete initial batch and never calls a model.
"""
import argparse
import json
from pathlib import Path
import subprocess
import time
from types import SimpleNamespace

from .control import ROOT, DEPENDENCY_TASKS, prewarm, verify_dependency_readiness


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repair-reviewed-freecad-dependency', action='store_true')
    args = parser.parse_args()
    while subprocess.run(['systemctl', 'is-active', '--quiet', 'tb4-prewarm.service']).returncode == 0:
        time.sleep(10)
    output = ROOT / 'runs/prewarm'
    summary = json.loads((output / 'summary.json').read_text())
    failures = [r for r in summary['results'] if r['status'] == 'failed']
    reviewed = (args.repair_reviewed_freecad_dependency
                and all(r['task'] in DEPENDENCY_TASKS and r['phase'] == 'verifier'
                        and 'Cannot uninstall vtk 9.2.6' in r.get('error', '') for r in failures))
    if (summary.get('passed', 0) + summary.get('failed', 0) != 112
        or (summary.get('failed') and not reviewed)):
        raise RuntimeError('Initial preparation incomplete; needs diagnosis, not an automatic retry.')
    # Early API-validation phases predated explicit image-alias retention. The
    # normal cache-aware resume repairs only missing image tags and leaves all
    # other completed environments untouched.
    prewarm(SimpleNamespace(manifest=ROOT/'plans/terminal-bench-4-evaluation/task-manifest.json',
        dataset=ROOT/'src/terminal-bench', output=output, task=None, force=False,
        sample_seconds=10, max_concurrent=2))
    final = json.loads((output / 'summary.json').read_text())
    if final.get('retained_image_phases') != 112:
        raise RuntimeError('Final image inventory does not cover all 112 phases.')
    verify_dependency_readiness(final, output, ROOT/'src/terminal-bench')


if __name__ == '__main__':
    main()
