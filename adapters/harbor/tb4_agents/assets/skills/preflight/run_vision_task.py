"""Run only this bounded local synthetic; never takes a benchmark task path."""

import asyncio
import hashlib
import json
import os
from pathlib import Path
import time

from harbor.models.trial.config import AgentConfig, EnvironmentConfig, ResourceMode, TaskConfig, TrialConfig
from harbor.models.environment_type import EnvironmentType
from harbor.trial.trial import Trial

from adapters.harbor.tb4_control.control import verify_boundary


async def main():
    verify_boundary()
    task = Path(__file__).parent / "vision_task"
    config_file = Path(__file__).resolve().parents[1] / "agent.json"
    agent = AgentConfig.model_validate_json(config_file.read_text())
    output = Path(os.environ.get("TB4_PREFLIGHT_RUNS_DIR", "/data/tb4/runs/preflight-skills"))
    name = "vision-child-" + time.strftime("%Y%m%dT%H%M%S", time.gmtime())
    config = TrialConfig(task=TaskConfig(path=task), trial_name=name, trials_dir=output,
        agent=agent, environment=EnvironmentConfig(type=EnvironmentType.DOCKER,
            cpu_enforcement_policy=ResourceMode.LIMIT, memory_enforcement_policy=ResourceMode.LIMIT))
    print(str(output / name), flush=True)
    trial = await Trial.create(config)
    result = await trial.run()
    (output / name / "controller-result.json").write_text(result.model_dump_json(indent=2) + "\n")
    print(json.dumps({"reward": result.verifier_result.rewards if result.verifier_result else None,
        "exception_type": result.exception_info.exception_type if result.exception_info else None}), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
