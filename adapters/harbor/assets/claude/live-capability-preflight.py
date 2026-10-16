"""Real, paid, synthetic Claude image/subagent check; never a TB4 task."""
import asyncio
import hashlib
import importlib.util
import json
import shutil
import time
from pathlib import Path

from harbor.models.environment_type import EnvironmentType
from harbor.models.trial.config import AgentConfig, EnvironmentConfig, ResourceMode, TaskConfig, TrialConfig
from harbor.trial.trial import Trial

ROOT = Path('/data/tb4')
ASSETS = Path(__file__).resolve().parent
SOURCE = ASSETS.parents[1] / 'tb4_control/smoke_task'


async def main():
    base = ROOT / 'runs/preflight/claude' / ('capability-' + time.strftime('%Y%m%dT%H%M%S', time.gmtime()))
    task = base / 'task'
    shutil.copytree(SOURCE, task)
    spec = importlib.util.spec_from_file_location('offline_fixture', ASSETS / 'offline-protocol-smoke.py')
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    (task / 'environment/smoke.png').write_bytes(fixture.png())
    with (task / 'environment/Dockerfile').open('a') as stream:
        stream.write('\nCOPY smoke.png /app/smoke.png\n')
    (task / 'task.toml').write_text((task / 'task.toml').read_text().replace('local/tb4-synthetic-preflight','local/tb4-claude-capability-preflight').replace('artifacts = ["/app/answer.txt"]','artifacts = ["/app/answer.txt", "/app/color.txt"]'))
    (task / 'instruction.md').write_text('''This is a synthetic infrastructure preflight, not a benchmark task.
Read /app/input.txt with your tools. Use the Agent tool to launch one general-purpose
child, selecting model haiku if the tool offers this parameter. Ask that child to
inspect /app/smoke.png using its Read image tool and write the image's dominant color
as a single lowercase word followed by one newline to /app/color.txt. The child must
actually read the PNG; do not tell it the expected color. Wait for the child, read
its color.txt, then create /app/answer.txt containing exactly TB4_PREFLIGHT_OK and one
newline. Do not modify input.txt or smoke.png. Report the color and SHA256 of answer.txt.
''')
    with (task / 'tests/test.sh').open('a') as stream:
        stream.write("\npython3 - <<'CHECK'\nfrom pathlib import Path\nif Path('/app/color.txt').read_bytes() != b'red\\n':\n    Path('/logs/verifier/reward.txt').write_text('0')\nCHECK\n")
    config = AgentConfig.model_validate_json((ASSETS / 'smoke-agent.json').read_text())
    trial = await Trial.create(TrialConfig(task=TaskConfig(path=task), agent=config,
        trial_name='trial', trials_dir=base,
        environment=EnvironmentConfig(type=EnvironmentType.DOCKER,
            cpu_enforcement_policy=ResourceMode.LIMIT, memory_enforcement_policy=ResourceMode.LIMIT)))
    result = await trial.run()
    (base / 'controller-result.json').write_text(result.model_dump_json(indent=2)+'\n')
    print(json.dumps({'kind':'live_synthetic_capability_only','benchmark_task':False,
        'result_path':str(base / 'controller-result.json'),
        'verifier_result':result.verifier_result.model_dump(mode='json') if result.verifier_result else None,
        'exception':result.exception_info.type if result.exception_info else None,
        'fixture_png_sha256':hashlib.sha256(fixture.png()).hexdigest()}))


if __name__ == '__main__':
    asyncio.run(main())
