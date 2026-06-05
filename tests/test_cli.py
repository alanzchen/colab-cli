import asyncio
import importlib.metadata
from types import SimpleNamespace

from fastmcp.client.client import CallToolResult
from mcp.types import TextContent
import pytest

from colab_cli import cli
from colab_cli.bridge import ColabConnectionTimeoutError
from colab_cli.runtime import RuntimeServerError


class FakeStdout:
    def __init__(self):
        self.text = ""
        self.flush_count = 0

    def write(self, text):
        self.text += text

    def flush(self):
        self.flush_count += 1


class FakeStderr(FakeStdout):
    pass


class FakeRuntimeClient:
    def __init__(
        self,
        tools=None,
        result=None,
        status_result=None,
        shutdown_result=None,
        error=None,
    ):
        self.tools = tools or []
        self.result = result
        self.status_result = status_result
        self.shutdown_result = shutdown_result
        self.error = error
        self.calls = []

    async def list_tools(self, *, timeout):
        self.calls.append(("list_tools", timeout))
        if self.error:
            raise self.error
        return self.tools

    async def call_tool(self, name, arguments, *, timeout):
        self.calls.append(("call_tool", name, arguments, timeout))
        if self.error:
            raise self.error
        return self.result

    async def status(self, *, timeout):
        self.calls.append(("status", timeout))
        if self.error:
            raise self.error
        return self.status_result

    async def shutdown(self, *, timeout):
        self.calls.append(("shutdown", timeout))
        if self.error:
            raise self.error
        return self.shutdown_result


class FakeSshManager:
    def __init__(self, exit_code=0):
        self.exit_code = exit_code
        self.calls = []

    async def setup_and_maybe_connect(
        self,
        *,
        setup_only,
        ssh_args,
        timeout,
        setup_timeout,
    ):
        self.calls.append((setup_only, ssh_args, timeout, setup_timeout))
        return self.exit_code


class FakeBridge:
    def __init__(self):
        self.url = "https://colab.example/connect"
        self.server = SimpleNamespace(port=24680)
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


async def cancelled_sleep(seconds):
    raise asyncio.CancelledError


class FakeMcpClient:
    def __init__(self, transport):
        self.transport = transport

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass


class FakeRuntimeServer:
    instances = []
    wait_error = None

    @classmethod
    def reset(cls, *, wait_error=None):
        cls.instances.clear()
        cls.wait_error = wait_error

    def __init__(self, mcp_client):
        self.mcp_client = mcp_client
        self.host = "127.0.0.1"
        self.port = 9876
        self.started = False
        self.closed = False
        self.waited_for_shutdown = False
        self.__class__.instances.append(self)

    async def start(self):
        self.started = True

    async def wait_for_shutdown(self):
        self.waited_for_shutdown = True
        if self.__class__.wait_error is not None:
            raise self.__class__.wait_error

    async def close(self):
        self.closed = True


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


def test_parser_accepts_lifecycle_commands():
    parser = cli.build_parser()

    status = parser.parse_args(["status", "--json"])
    stop = parser.parse_args(["stop"])
    replace = parser.parse_args(["connect", "--replace"])
    remote_browser = parser.parse_args(
        ["connect", "--browser-ssh-host", "oracle"]
    )

    assert status.command == "status"
    assert status.output_json is True
    assert stop.command == "stop"
    assert replace.command == "connect"
    assert replace.replace is True
    assert remote_browser.browser_ssh_host == "oracle"


def test_parser_accepts_ssh_command():
    parser = cli.build_parser()

    args = parser.parse_args(
        ["ssh", "--setup-only", "--alias", "gpu", "--", "whoami"]
    )

    assert args.command == "ssh"
    assert args.setup_only is True
    assert args.alias == "gpu"
    assert args.ssh_args == ["--", "whoami"]


def test_parser_accepts_bootstrap_command():
    parser = cli.build_parser()

    args = parser.parse_args(
        [
            "bootstrap",
            "--workspace",
            "/content/work",
            "--tailscale-auth-key",
            "tskey-test",
            "--hostname-prefix",
            "worker",
            "--no-cloudflare",
        ]
    )

    assert args.command == "bootstrap"
    assert args.workspace == "/content/work"
    assert args.tailscale_auth_key == "tskey-test"
    assert args.hostname_prefix == "worker"
    assert args.no_cloudflare is True


def test_parse_json_object_rejects_invalid_json():
    with pytest.raises(cli.CliUsageError, match="Invalid JSON"):
        cli.parse_json_object("{bad json")


def test_parse_json_object_rejects_non_object_json():
    with pytest.raises(cli.CliUsageError, match="JSON arguments must be an object"):
        cli.parse_json_object('["not", "object"]')


def test_to_jsonable_uses_model_dump():
    value = SimpleNamespace(model_dump=lambda mode: {"mode": mode})

    assert cli.to_jsonable(value) == {"mode": "json"}


def test_to_jsonable_recurses_into_model_dump_result():
    value = SimpleNamespace(
        model_dump=lambda mode: {
            "mode": mode,
            "content": [TextContent(type="text", text="hello")],
        }
    )

    assert cli.to_jsonable(value) == {
        "mode": "json",
        "content": [
            {
                "type": "text",
                "text": "hello",
                "annotations": None,
                "meta": None,
            }
        ],
    }


def test_to_jsonable_recurses_into_dataclass_result():
    value = CallToolResult(
        content=[TextContent(type="text", text='{"ok": true}')],
        structured_content={"ok": True},
        meta=None,
        data={"ok": True},
        is_error=False,
    )

    assert cli.to_jsonable(value) == {
        "content": [
            {
                "type": "text",
                "text": '{"ok": true}',
                "annotations": None,
                "meta": None,
            }
        ],
        "structured_content": {"ok": True},
        "meta": None,
        "data": {"ok": True},
        "is_error": False,
    }


def test_tool_to_row_reads_dict_tools_returned_by_runtime():
    tool = {"name": "get_cells", "description": "Gets notebook cells"}

    assert cli.tool_to_row(tool) == ("get_cells", "Gets notebook cells")


@pytest.mark.asyncio
async def test_tools_command_prints_json():
    stdout = FakeStdout()
    stderr = FakeStderr()
    tool = SimpleNamespace(
        name="run_cell",
        description="Run a cell",
        inputSchema={"type": "object"},
    )
    client = FakeRuntimeClient(tools=[tool])

    code = await cli.run_async(
        ["tools", "--json", "--timeout", "3"],
        runtime_client_factory=lambda: client,
        stdout=stdout,
        stderr=stderr,
    )

    assert code == 0
    assert '"name": "run_cell"' in stdout.text
    assert client.calls == [("list_tools", 3.0)]
    assert stderr.text == ""


@pytest.mark.asyncio
async def test_tools_command_prints_table():
    stdout = FakeStdout()
    stderr = FakeStderr()
    tool = SimpleNamespace(name="run_cell", description="Run a cell")
    client = FakeRuntimeClient(tools=[tool])

    code = await cli.run_async(
        ["tools"],
        runtime_client_factory=lambda: client,
        stdout=stdout,
        stderr=stderr,
    )

    assert code == 0
    assert "run_cell" in stdout.text
    assert "Run a cell" in stdout.text


@pytest.mark.asyncio
async def test_status_command_prints_running_status():
    stdout = FakeStdout()
    stderr = FakeStderr()
    client = FakeRuntimeClient(
        status_result={
            "state": "running",
            "reachable": True,
            "host": "127.0.0.1",
            "port": 1234,
        }
    )

    code = await cli.run_async(
        ["status"],
        runtime_client_factory=lambda: client,
        stdout=stdout,
        stderr=stderr,
    )

    assert code == 0
    assert "running" in stdout.text
    assert "127.0.0.1:1234" in stdout.text
    assert client.calls == [("status", 5.0)]
    assert stderr.text == ""


@pytest.mark.asyncio
async def test_status_command_prints_json():
    stdout = FakeStdout()
    stderr = FakeStderr()
    client = FakeRuntimeClient(
        status_result={"state": "missing", "reachable": False}
    )

    code = await cli.run_async(
        ["status", "--json"],
        runtime_client_factory=lambda: client,
        stdout=stdout,
        stderr=stderr,
    )

    assert code == 0
    assert '"state": "missing"' in stdout.text
    assert '"reachable": false' in stdout.text
    assert client.calls == [("status", 5.0)]


@pytest.mark.asyncio
async def test_stop_command_requests_shutdown():
    stdout = FakeStdout()
    stderr = FakeStderr()
    client = FakeRuntimeClient(shutdown_result={"stopping": True})

    code = await cli.run_async(
        ["stop"],
        runtime_client_factory=lambda: client,
        stdout=stdout,
        stderr=stderr,
    )

    assert code == 0
    assert "Stopping" in stdout.text
    assert client.calls == [("shutdown", 5.0)]
    assert stderr.text == ""


@pytest.mark.asyncio
async def test_ssh_command_prints_setup_info():
    stdout = FakeStdout()
    stderr = FakeStderr()
    fake = FakeSshManager(exit_code=0)

    code = await cli.run_async(
        ["ssh", "--setup-only"],
        ssh_manager_factory=lambda **kwargs: fake,
        runtime_client_factory=lambda: FakeRuntimeClient(),
        stdout=stdout,
        stderr=stderr,
    )

    assert code == 0
    assert "ssh colab-ssh" in stdout.text
    assert fake.calls == [(True, [], 60.0, 300.0)]
    assert stderr.text == ""


@pytest.mark.asyncio
async def test_ssh_command_strips_argument_separator_before_connecting():
    stdout = FakeStdout()
    stderr = FakeStderr()
    fake = FakeSshManager(exit_code=0)

    code = await cli.run_async(
        ["ssh", "--", "whoami"],
        ssh_manager_factory=lambda **kwargs: fake,
        runtime_client_factory=lambda: FakeRuntimeClient(),
        stdout=stdout,
        stderr=stderr,
    )

    assert code == 0
    assert fake.calls == [(False, ["whoami"], 60.0, 300.0)]
    assert stderr.text == ""


@pytest.mark.asyncio
async def test_bootstrap_command_prints_worker_setup_cell(monkeypatch, tmp_path):
    from colab_cli import ssh

    stdout = FakeStdout()
    stderr = FakeStderr()
    key_path = tmp_path / "key"
    monkeypatch.setattr(ssh, "ensure_keypair", lambda path: "ssh-ed25519 AAAA test")

    code = await cli.run_async(
        [
            "bootstrap",
            "--bootstrap-url",
            "https://example/setup.py",
            "--key-path",
            str(key_path),
            "--workspace",
            "/content/work",
            "--no-cloudflare",
        ],
        stdout=stdout,
        stderr=stderr,
    )

    assert code == 0
    assert "https://example/setup.py" in stdout.text
    assert "namespace['setup_worker'](" in stdout.text
    assert "ssh-ed25519 AAAA test" in stdout.text
    assert "start_cloudflare=False" in stdout.text
    assert stderr.text == ""


@pytest.mark.asyncio
async def test_connect_command_opens_waits_and_exits_130_on_interrupt(tmp_path):
    stdout = FakeStdout()
    stderr = FakeStderr()
    bridge = FakeBridge()
    state_file = tmp_path / "server.json"
    FakeRuntimeServer.reset(wait_error=KeyboardInterrupt)

    code = await cli.run_async(
        ["connect", "--timeout", "4"],
        bridge_factory=lambda: bridge,
        mcp_client_factory=FakeMcpClient,
        runtime_server_factory=FakeRuntimeServer,
        sleep=interrupting_sleep,
        state_file=state_file,
        stdout=stdout,
        stderr=stderr,
    )

    assert code == 130
    assert bridge.opened is True
    assert bridge.waited_timeout == 4
    assert bridge.exited is True
    assert FakeRuntimeServer.instances[0].started is True
    assert FakeRuntimeServer.instances[0].waited_for_shutdown is True
    assert FakeRuntimeServer.instances[0].closed is True
    assert "https://colab.example/connect" in stdout.text
    assert stdout.flush_count >= 3
    assert not state_file.exists()


@pytest.mark.asyncio
async def test_connect_remote_browser_prints_port_forward_and_does_not_open(tmp_path):
    stdout = FakeStdout()
    stderr = FakeStderr()
    bridge = FakeBridge()
    state_file = tmp_path / "server.json"
    FakeRuntimeServer.reset(wait_error=KeyboardInterrupt)

    code = await cli.run_async(
        ["connect", "--browser-ssh-host", "oracle", "--timeout", "4"],
        bridge_factory=lambda: bridge,
        mcp_client_factory=FakeMcpClient,
        runtime_server_factory=FakeRuntimeServer,
        state_file=state_file,
        stdout=stdout,
        stderr=stderr,
    )

    assert code == 130
    assert bridge.opened is False
    assert "ssh -N -L 24680:127.0.0.1:24680 oracle" in stdout.text
    assert "Open the Colab URL after the forward is running." in stdout.text


@pytest.mark.asyncio
async def test_connect_command_exits_130_on_cancelled_sleep(tmp_path):
    stdout = FakeStdout()
    stderr = FakeStderr()
    bridge = FakeBridge()
    state_file = tmp_path / "server.json"
    FakeRuntimeServer.reset(wait_error=asyncio.CancelledError)

    code = await cli.run_async(
        ["connect", "--timeout", "4"],
        bridge_factory=lambda: bridge,
        mcp_client_factory=FakeMcpClient,
        runtime_server_factory=FakeRuntimeServer,
        sleep=cancelled_sleep,
        state_file=state_file,
        stdout=stdout,
        stderr=stderr,
    )

    assert code == 130
    assert FakeRuntimeServer.instances[0].closed is True
    assert not state_file.exists()


@pytest.mark.asyncio
async def test_connect_command_exits_zero_on_runtime_shutdown(tmp_path):
    stdout = FakeStdout()
    stderr = FakeStderr()
    bridge = FakeBridge()
    state_file = tmp_path / "server.json"
    FakeRuntimeServer.reset()

    code = await cli.run_async(
        ["connect", "--timeout", "4"],
        bridge_factory=lambda: bridge,
        mcp_client_factory=FakeMcpClient,
        runtime_server_factory=FakeRuntimeServer,
        state_file=state_file,
        stdout=stdout,
        stderr=stderr,
    )

    assert code == 0
    assert bridge.opened is True
    assert FakeRuntimeServer.instances[0].waited_for_shutdown is True
    assert FakeRuntimeServer.instances[0].closed is True
    assert not state_file.exists()


@pytest.mark.asyncio
async def test_connect_replace_stops_existing_runtime_before_connecting(tmp_path):
    stdout = FakeStdout()
    stderr = FakeStderr()
    bridge = FakeBridge()
    state_file = tmp_path / "server.json"
    client = FakeRuntimeClient(shutdown_result={"stopping": True})
    FakeRuntimeServer.reset()

    code = await cli.run_async(
        ["connect", "--replace", "--timeout", "4"],
        runtime_client_factory=lambda: client,
        bridge_factory=lambda: bridge,
        mcp_client_factory=FakeMcpClient,
        runtime_server_factory=FakeRuntimeServer,
        state_file=state_file,
        stdout=stdout,
        stderr=stderr,
    )

    assert code == 0
    assert client.calls[0] == ("shutdown", 5.0)
    assert bridge.opened is True
    assert FakeRuntimeServer.instances[0].started is True


@pytest.mark.asyncio
async def test_connect_replace_fails_when_shutdown_fails(tmp_path):
    stdout = FakeStdout()
    stderr = FakeStderr()
    bridge = FakeBridge()
    state_file = tmp_path / "server.json"
    client = FakeRuntimeClient(error=RuntimeServerError("refused"))
    FakeRuntimeServer.reset()

    code = await cli.run_async(
        ["connect", "--replace", "--timeout", "4"],
        runtime_client_factory=lambda: client,
        bridge_factory=lambda: bridge,
        mcp_client_factory=FakeMcpClient,
        runtime_server_factory=FakeRuntimeServer,
        state_file=state_file,
        stdout=stdout,
        stderr=stderr,
    )

    assert code == 1
    assert bridge.opened is False
    assert FakeRuntimeServer.instances == []
    assert "refused" in stderr.text


@pytest.mark.asyncio
async def test_call_command_prints_json_result():
    stdout = FakeStdout()
    stderr = FakeStderr()
    result = SimpleNamespace(structured_content={"ok": True})
    client = FakeRuntimeClient(result=result)

    code = await cli.run_async(
        ["call", "run_cell", "--json", '{"code":"print(1)"}'],
        runtime_client_factory=lambda: client,
        stdout=stdout,
        stderr=stderr,
    )

    assert code == 0
    assert '"ok": true' in stdout.text
    assert client.calls == [("call_tool", "run_cell", {"code": "print(1)"}, 60.0)]


@pytest.mark.asyncio
async def test_usage_error_returns_code_2():
    stdout = FakeStdout()
    stderr = FakeStderr()
    client = FakeRuntimeClient()

    code = await cli.run_async(
        ["call", "run_cell", "--json", "[1]"],
        runtime_client_factory=lambda: client,
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
    client = FakeRuntimeClient(error=error)

    code = await cli.run_async(
        ["tools", "--timeout", "1"],
        runtime_client_factory=lambda: client,
        stdout=stdout,
        stderr=stderr,
    )

    assert code == 1
    assert "Timed out" in stderr.text
    assert "https://example.invalid" in stderr.text


@pytest.mark.asyncio
async def test_missing_runtime_server_returns_code_1():
    stdout = FakeStdout()
    stderr = FakeStderr()
    client = FakeRuntimeClient(error=RuntimeServerError("Start colab-cli connect"))

    code = await cli.run_async(
        ["tools", "--timeout", "1"],
        runtime_client_factory=lambda: client,
        stdout=stdout,
        stderr=stderr,
    )

    assert code == 1
    assert "Start colab-cli connect" in stderr.text
