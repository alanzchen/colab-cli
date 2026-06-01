import importlib.metadata
from types import SimpleNamespace

import pytest

from colab_cli import cli
from colab_cli.bridge import ColabConnectionTimeoutError


class FakeStdout:
    def __init__(self):
        self.text = ""

    def write(self, text):
        self.text += text


class FakeStderr(FakeStdout):
    pass


class FakeClient:
    def __init__(self, tools=None, result=None, error=None):
        self.tools = tools or []
        self.result = result
        self.error = error
        self.calls = []

    async def list_tools(self, *, timeout, open_browser):
        self.calls.append(("list_tools", timeout, open_browser))
        if self.error:
            raise self.error
        return self.tools

    async def call_tool(self, name, arguments, *, timeout, open_browser):
        self.calls.append(("call_tool", name, arguments, timeout, open_browser))
        if self.error:
            raise self.error
        return self.result


class FakeBridge:
    def __init__(self):
        self.url = "https://colab.example/connect"
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


async def interrupting_sleep(seconds):
    raise KeyboardInterrupt


def test_console_script_points_to_cli_main():
    scripts = importlib.metadata.entry_points(group="console_scripts")
    colab_cli = [script for script in scripts if script.name == "colab-cli"]

    assert len(colab_cli) == 1
    assert colab_cli[0].value == "colab_cli.cli:main"


def test_parser_accepts_core_commands():
    parser = cli.build_parser()

    connect = parser.parse_args(["connect", "--timeout", "5"])
    tools = parser.parse_args(["tools", "--json"])
    call = parser.parse_args(["call", "run_cell", "--json", '{"code":"print(1)"}'])

    assert connect.command == "connect"
    assert connect.timeout == 5
    assert tools.command == "tools"
    assert tools.output_json is True
    assert call.command == "call"
    assert call.tool_name == "run_cell"


def test_parse_json_object_rejects_invalid_json():
    with pytest.raises(cli.CliUsageError, match="Invalid JSON"):
        cli.parse_json_object("{bad json")


def test_parse_json_object_rejects_non_object_json():
    with pytest.raises(cli.CliUsageError, match="JSON arguments must be an object"):
        cli.parse_json_object('["not", "object"]')


def test_to_jsonable_uses_model_dump():
    value = SimpleNamespace(model_dump=lambda mode: {"mode": mode})

    assert cli.to_jsonable(value) == {"mode": "json"}


@pytest.mark.asyncio
async def test_tools_command_prints_json():
    stdout = FakeStdout()
    stderr = FakeStderr()
    tool = SimpleNamespace(
        name="run_cell",
        description="Run a cell",
        inputSchema={"type": "object"},
    )
    client = FakeClient(tools=[tool])

    code = await cli.run_async(
        ["tools", "--json", "--timeout", "3"],
        client_factory=lambda: client,
        stdout=stdout,
        stderr=stderr,
    )

    assert code == 0
    assert '"name": "run_cell"' in stdout.text
    assert client.calls == [("list_tools", 3.0, True)]
    assert stderr.text == ""


@pytest.mark.asyncio
async def test_tools_command_prints_table():
    stdout = FakeStdout()
    stderr = FakeStderr()
    tool = SimpleNamespace(name="run_cell", description="Run a cell")
    client = FakeClient(tools=[tool])

    code = await cli.run_async(
        ["tools"],
        client_factory=lambda: client,
        stdout=stdout,
        stderr=stderr,
    )

    assert code == 0
    assert "run_cell" in stdout.text
    assert "Run a cell" in stdout.text


@pytest.mark.asyncio
async def test_connect_command_opens_waits_and_exits_130_on_interrupt():
    stdout = FakeStdout()
    stderr = FakeStderr()
    bridge = FakeBridge()

    code = await cli.run_async(
        ["connect", "--timeout", "4"],
        bridge_factory=lambda: bridge,
        sleep=interrupting_sleep,
        stdout=stdout,
        stderr=stderr,
    )

    assert code == 130
    assert bridge.opened is True
    assert bridge.waited_timeout == 4
    assert bridge.exited is True
    assert "https://colab.example/connect" in stdout.text


@pytest.mark.asyncio
async def test_call_command_prints_json_result():
    stdout = FakeStdout()
    stderr = FakeStderr()
    result = SimpleNamespace(structured_content={"ok": True})
    client = FakeClient(result=result)

    code = await cli.run_async(
        ["call", "run_cell", "--json", '{"code":"print(1)"}'],
        client_factory=lambda: client,
        stdout=stdout,
        stderr=stderr,
    )

    assert code == 0
    assert '"ok": true' in stdout.text
    assert client.calls == [
        ("call_tool", "run_cell", {"code": "print(1)"}, 60.0, True)
    ]


@pytest.mark.asyncio
async def test_usage_error_returns_code_2():
    stdout = FakeStdout()
    stderr = FakeStderr()
    client = FakeClient()

    code = await cli.run_async(
        ["call", "run_cell", "--json", "[1]"],
        client_factory=lambda: client,
        stdout=stdout,
        stderr=stderr,
    )

    assert code == 2
    assert "JSON arguments must be an object" in stderr.text


@pytest.mark.asyncio
async def test_connection_timeout_returns_code_1():
    stdout = FakeStdout()
    stderr = FakeStderr()
    error = ColabConnectionTimeoutError("https://example.invalid", 1)
    client = FakeClient(error=error)

    code = await cli.run_async(
        ["tools", "--timeout", "1"],
        client_factory=lambda: client,
        stdout=stdout,
        stderr=stderr,
    )

    assert code == 1
    assert "Timed out" in stderr.text
    assert "https://example.invalid" in stderr.text
