"""Exercise Harbor's real Claude installation, with zero model requests."""
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path

from harbor.job import Job
from harbor.models.job.config import JobConfig
from harbor.models.trial.config import AgentConfig, EnvironmentConfig, TaskConfig


async def main():
    assets = Path(__file__).resolve().parent
    agent_path = assets / 'agent.json'
    data = json.loads(agent_path.read_text())
    data['env'] = {'DEEPSEEK_API_KEY': 'offline-install-only-dummy'}
    agent = AgentConfig.model_validate(data)
    config = JobConfig(
        job_name='claude-install-only-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S'),
        jobs_dir=Path('/data/tb4/runs/preflight-install'),
        install_only=True,
        quiet=True,
        n_attempts=1,
        n_concurrent_trials=1,
        agents=[agent],
        tasks=[TaskConfig(path=assets.parents[1] / 'tb4_control/smoke_task')],
        environment=EnvironmentConfig(type='docker', cpu_enforcement_policy='limit', memory_enforcement_policy='limit'),
    )
    job = await Job.create(config)
    result = await job.run()
    print(json.dumps({'install_only': True, 'model_api_calls': 0,
                      'job_dir': str(job.job_dir), 'stats': result.stats.model_dump(mode='json')}))


asyncio.run(main())
