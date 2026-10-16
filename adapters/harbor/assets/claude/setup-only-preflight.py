#!/usr/bin/env python3
"""Harbor environment + native Claude install only; never call Agent.run()."""

import asyncio
import json
import time
from pathlib import Path

from harbor.models.environment_type import EnvironmentType
from harbor.models.trial.config import AgentConfig, EnvironmentConfig, ResourceMode, TaskConfig, TrialConfig
from harbor.trial.trial import Trial
from adapters.harbor.tb4_control.control import verify_boundary


async def main():
    verify_boundary()
    root = Path("/data/tb4")
    output = root / "runs/preflight/claude" / ("setup-only-" + time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()))
    config = TrialConfig(
        task=TaskConfig(path=Path(__file__).resolve().parents[2] / "tb4_control/smoke_task"),
        trial_name=output.name, trials_dir=output.parent,
        agent=AgentConfig(import_path="adapters.harbor.tb4_agents.claude:DeepSeekClaude",
                          model_name="deepseek-v4.1-flash[1m]",
                          kwargs={"version": "2.1.300", "reasoning_effort": "max"}),
        environment=EnvironmentConfig(type=EnvironmentType.DOCKER,
                                      cpu_enforcement_policy=ResourceMode.LIMIT,
                                      memory_enforcement_policy=ResourceMode.LIMIT),
    )
    trial = await Trial.create(config)
    trial._init_result()
    report = {"kind": "harbor_setup_only", "live_provider_calls": 0, "agent_run_called": False,
              "task": "local/tb4-synthetic-preflight", "output": str(output)}
    try:
        await trial._setup_agent_environment()
        await trial._setup_agent()
        result = await trial.agent_environment.exec(command=trial.agent.get_version_command())
        report.update(passed=result.return_code == 0 and "2.1.300" in (result.stdout or ""),
                      version_stdout=(result.stdout or "").strip(),
                      model=trial.agent._resolved_model_name(), effort=trial.agent.options.reasoning_effort)
    finally:
        await trial.agent_environment.stop(delete=True)
        report["environment_cleaned_up"] = True
        (output / "setup-only-report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))
    if not report.get("passed"):
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
