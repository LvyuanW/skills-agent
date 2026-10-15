---
name: task-mcp
description: Discover and call the task-provided MCP tools, including browser tools, through the shell. Retains one client session per server across calls and returns image observations as native image feedback.
---

Use this skill when the task provides MCP servers and its next step needs one of their tools.
The generic client is `/opt/tb4-skills/venv/bin/python /opt/tb4-skills/mcp_bridge.py`.

Discover configured servers with `servers`, then obtain a server's tool names and exact JSON argument schemas with `list SERVER`.
Call a discovered tool with `call SERVER TOOL '{"argument":"value"}'`. For complex arguments, save a JSON object and pass `@/absolute/path/arguments.json`.
Use the native shell tool, with `result_mode=direct` for a small tool list or image response. Image results use the Agent's existing `skills_agent_feedback` envelope; the original pixels are attached automatically.
Inspect `isError` and the command exit status, and preserve evidence of tool failures. The bridge keeps the MCP client sessions alive across shell invocations; do not start another bridge or browser unless the task requires it.

Only task-provided server definitions are configured. They do not grant access to hidden tests, reference solutions, or host files outside the sandbox.
