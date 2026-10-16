"""Deterministic shell transport probe, restricted to our synthetic task."""
import json

from harbor.agents.base import BaseAgent
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext


class ShellTransportProbe(BaseAgent):
    @staticmethod
    def name():
        return 'tb4-shell-transport-check'

    def version(self):
        return '1'

    async def setup(self, environment: BaseEnvironment):
        pass

    async def run(self, instruction: str, environment: BaseEnvironment, context: AgentContext):
        if not instruction.startswith('This is a synthetic infrastructure preflight, not a benchmark task.'):
            raise ValueError('This deterministic probe is forbidden on benchmark tasks.')
        command = "test -f /app/input.txt && cat /app/input.txt && printf 'TB4_PREFLIGHT_OK\\n' > /app/answer.txt && sha256sum /app/answer.txt"
        result = await environment.exec(command=command, timeout_sec=30)
        if result.return_code:
            raise RuntimeError('Synthetic shell read/write probe failed.')
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        (self.logs_dir / 'transport-probe.json').write_text(json.dumps({
            'no_model_calls': True, 'synthetic_task_only': True,
            'stdout': result.stdout, 'return_code': result.return_code}, indent=2))
        context.n_input_tokens = 0
        context.n_output_tokens = 0
        context.n_cache_tokens = 0
