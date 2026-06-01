from types import SimpleNamespace

import pytest

from colab_cli.client import ColabCliClient


class FakeBridge:
    def __init__(self):
        self.server = SimpleNamespace(read_stream=object(), write_stream=object())
        self.opened = False
        self.waited_timeout = None
        self.exited = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        self.exited = True

    def open_browser(self):
        self.opened = True

    async def wait_for_connection(self, timeout):
        self.waited_timeout = timeout


class FakeClient:
    def __init__(self, transport):
        self.transport = transport
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass

    async def list_tools(self):
        return [
            SimpleNamespace(
                name="run_cell",
                description="Run a notebook cell",
                inputSchema={"type": "object"},
            )
        ]

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        return SimpleNamespace(structured_content={"ok": True, "name": name})


@pytest.mark.asyncio
async def test_list_tools_opens_browser_waits_and_returns_tools():
    bridge = FakeBridge()
    client = ColabCliClient(
        bridge_factory=lambda: bridge,
        client_factory=FakeClient,
    )

    tools = await client.list_tools(timeout=7, open_browser=True)

    assert bridge.opened is True
    assert bridge.waited_timeout == 7
    assert bridge.exited is True
    assert tools[0].name == "run_cell"


@pytest.mark.asyncio
async def test_call_tool_passes_arguments_and_returns_result():
    bridge = FakeBridge()
    fake_client = FakeClient
    client = ColabCliClient(
        bridge_factory=lambda: bridge,
        client_factory=fake_client,
    )

    result = await client.call_tool(
        "run_cell",
        {"code": "print('hi')"},
        timeout=9,
        open_browser=False,
    )

    assert bridge.opened is False
    assert bridge.waited_timeout == 9
    assert result.structured_content == {"ok": True, "name": "run_cell"}


@pytest.mark.asyncio
async def test_call_tool_requires_json_object_arguments():
    client = ColabCliClient(bridge_factory=FakeBridge, client_factory=FakeClient)

    with pytest.raises(TypeError, match="Tool arguments must be a JSON object"):
        await client.call_tool("run_cell", ["not", "an", "object"])
