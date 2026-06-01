from types import SimpleNamespace

from fastmcp.client.client import CallToolResult
from mcp.types import TextContent
import pytest

from colab_cli.runtime import (
    RuntimeClient,
    RuntimeServer,
    RuntimeServerError,
    RuntimeState,
    clear_state,
    read_state,
    write_state,
)


class FakeMcpClient:
    def __init__(self):
        self.calls = []

    async def list_tools(self):
        return [
            SimpleNamespace(
                name="get_cells",
                description="Gets notebook cells",
                inputSchema={"type": "object"},
            )
        ]

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        return CallToolResult(
            content=[TextContent(type="text", text='{"cells": []}')],
            structured_content={"cells": []},
            meta=None,
            data={"cells": []},
            is_error=False,
        )


def test_state_round_trip(tmp_path):
    state_file = tmp_path / "server.json"
    state = RuntimeState(host="127.0.0.1", port=1234)

    write_state(state, state_file)

    assert read_state(state_file) == state

    clear_state(state_file)
    assert not state_file.exists()


@pytest.mark.asyncio
async def test_runtime_client_lists_tools_from_server(tmp_path):
    state_file = tmp_path / "server.json"
    server = RuntimeServer(FakeMcpClient())
    await server.start()
    write_state(RuntimeState(host=server.host, port=server.port), state_file)

    try:
        tools = await RuntimeClient(state_file=state_file).list_tools(timeout=1)
    finally:
        await server.close()

    assert tools == [
        {
            "name": "get_cells",
            "description": "Gets notebook cells",
            "inputSchema": {"type": "object"},
        }
    ]


@pytest.mark.asyncio
async def test_runtime_client_calls_tool_from_server(tmp_path):
    state_file = tmp_path / "server.json"
    mcp_client = FakeMcpClient()
    server = RuntimeServer(mcp_client)
    await server.start()
    write_state(RuntimeState(host=server.host, port=server.port), state_file)

    try:
        result = await RuntimeClient(state_file=state_file).call_tool(
            "get_cells",
            {},
            timeout=1,
        )
    finally:
        await server.close()

    assert mcp_client.calls == [("get_cells", {})]
    assert result["structured_content"] == {"cells": []}
    assert result["content"][0]["type"] == "text"


@pytest.mark.asyncio
async def test_runtime_client_reports_missing_connect_process(tmp_path):
    state_file = tmp_path / "missing.json"

    with pytest.raises(RuntimeServerError, match="No running colab-cli connect"):
        await RuntimeClient(state_file=state_file).list_tools(timeout=1)
