"""Persistent generic MCP client exposed through the Agent's native shell tool."""

import argparse
import asyncio
import base64
from contextlib import AsyncExitStack
import json
import os
from pathlib import Path
import socket
import sys
import uuid


async def serve(config_path, socket_path):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.sse import sse_client
    from mcp.client.stdio import stdio_client
    from mcp.client.streamable_http import streamablehttp_client

    configurations = {item["name"]: item for item in json.loads(Path(config_path).read_text())}
    sessions, locks = {}, {}
    async with AsyncExitStack() as stack:
        async def session_for(name):
            if name not in configurations:
                raise ValueError(f"Unknown MCP server: {name}")
            if name not in sessions:
                config = configurations[name]
                transport = config.get("transport", "sse")
                if transport == "stdio":
                    streams = await stack.enter_async_context(stdio_client(StdioServerParameters(
                        command=config["command"], args=config.get("args", []), env=dict(os.environ))))
                elif transport in {"streamable-http", "http"}:
                    streams = await stack.enter_async_context(streamablehttp_client(config["url"]))
                elif transport == "sse":
                    streams = await stack.enter_async_context(sse_client(config["url"]))
                else:
                    raise ValueError(f"Unsupported MCP transport: {transport}")
                client = await stack.enter_async_context(ClientSession(streams[0], streams[1]))
                await client.initialize()
                sessions[name] = client
            return sessions[name]

        async def handle(reader, writer):
            try:
                request = json.loads(await reader.readline())
                name = request.get("server")
                if request["operation"] == "servers":
                    result = {"servers": list(configurations)}
                else:
                    lock = locks.setdefault(name, asyncio.Lock())
                    async with lock:
                        client = await session_for(name)
                        if request["operation"] == "list":
                            result = (await client.list_tools()).model_dump(mode="json")
                        elif request["operation"] == "call":
                            result = (await client.call_tool(request["tool"], request.get("arguments", {}))).model_dump(mode="json")
                        else:
                            raise ValueError("Unknown operation")
                response = {"result": result}
            except Exception as exc:
                response = {"error": str(exc), "exception_type": type(exc).__name__}
            writer.write((json.dumps(response) + "\n").encode())
            await writer.drain()
            writer.close()
            await writer.wait_closed()

        # SDK transports use AnyIO cancel scopes: enter and exit them in this
        # same owning task, rather than opening them in short-lived handlers.
        for name in configurations:
            await session_for(name)
        path = Path(socket_path)
        path.unlink(missing_ok=True)
        server = await asyncio.start_unix_server(handle, path=socket_path, limit=16 * 1024 * 1024)
        os.chmod(socket_path, 0o600)
        async with server:
            await server.serve_forever()


def call(socket_path, request, image_dir):
    with socket.socket(socket.AF_UNIX) as client:
        client.settimeout(180)
        client.connect(socket_path)
        client.sendall((json.dumps(request) + "\n").encode())
        with client.makefile("rb") as source:
            response = json.loads(source.readline())
    if "error" in response:
        print(json.dumps(response))
        return 1
    result = response["result"]
    attachments = []
    for block in result.get("content", []):
        if block.get("type") != "image":
            continue
        data = base64.b64decode(block.pop("data"), validate=True)
        mime = block.get("mimeType", "application/octet-stream")
        suffix = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp", "image/gif": ".gif"}.get(mime, ".bin")
        destination = Path(image_dir) / (uuid.uuid4().hex + suffix)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
        block["saved_path"] = str(destination)
        attachments.append({"type": "image", "path": str(destination), "label": "MCP image observation"})
    if attachments:
        print(json.dumps({"skills_agent_feedback": 1, "attachments": attachments, "mcp_result": result}))
    else:
        print(json.dumps(result))
    return int(bool(result.get("isError")))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--socket", default="/opt/tb4-skills/mcp.sock")
    sub = parser.add_subparsers(dest="operation", required=True)
    server = sub.add_parser("serve")
    server.add_argument("--config", required=True)
    sub.add_parser("servers")
    listing = sub.add_parser("list")
    listing.add_argument("server")
    calling = sub.add_parser("call")
    calling.add_argument("server")
    calling.add_argument("tool")
    calling.add_argument("arguments", help="JSON object, or @path/to/arguments.json")
    args = parser.parse_args()
    if args.operation == "serve":
        asyncio.run(serve(args.config, args.socket))
        return 0
    request = {"operation": args.operation}
    if args.operation != "servers":
        request["server"] = args.server
    if args.operation == "call":
        text = Path(args.arguments[1:]).read_text() if args.arguments.startswith("@") else args.arguments
        request.update(tool=args.tool, arguments=json.loads(text))
        if not isinstance(request["arguments"], dict):
            parser.error("arguments must be a JSON object")
    return call(args.socket, request, "/logs/agent/mcp-images")


if __name__ == "__main__":
    sys.exit(main())
