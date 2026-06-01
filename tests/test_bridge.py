import asyncio
from contextlib import asynccontextmanager
from unittest.mock import Mock

import pytest

from colab_cli.bridge import ColabBridge, ColabConnectionTimeoutError
from colab_cli.transport import ColabTransport


class FakeConnectionLive:
    def __init__(self, is_set=False):
        self._event = asyncio.Event()
        if is_set:
            self._event.set()

    def is_set(self):
        return self._event.is_set()

    async def wait(self):
        await self._event.wait()

    def set(self):
        self._event.set()


class FakeServer:
    def __init__(self):
        self.port = 4321
        self.token = "test-token"
        self.connection_live = FakeConnectionLive()
        self.read_stream = object()
        self.write_stream = object()
        self.exited = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        self.exited = True


@pytest.mark.asyncio
async def test_bridge_builds_colab_url_and_opens_browser():
    server = FakeServer()
    opened = Mock()

    async with ColabBridge(server_factory=lambda: server, browser_open=opened) as bridge:
        assert bridge.url == (
            "https://colab.research.google.com/notebooks/empty.ipynb"
            "#mcpProxyToken=test-token&mcpProxyPort=4321"
        )
        bridge.open_browser()

    opened.assert_called_once_with(bridge.url)
    assert server.exited is True


@pytest.mark.asyncio
async def test_wait_for_connection_returns_when_live():
    server = FakeServer()

    async with ColabBridge(server_factory=lambda: server) as bridge:
        server.connection_live.set()
        await bridge.wait_for_connection(timeout=0.1)

    assert server.exited is True


@pytest.mark.asyncio
async def test_wait_for_connection_raises_timeout_with_url():
    server = FakeServer()

    async with ColabBridge(server_factory=lambda: server) as bridge:
        with pytest.raises(ColabConnectionTimeoutError) as exc_info:
            await bridge.wait_for_connection(timeout=0.01)

    assert "https://colab.research.google.com/notebooks/empty.ipynb" in str(
        exc_info.value
    )


def test_transport_repr_and_session_streams(monkeypatch):
    server = FakeServer()
    session_args = {}

    @asynccontextmanager
    async def fake_client_session(read_stream, write_stream, **kwargs):
        session_args["read_stream"] = read_stream
        session_args["write_stream"] = write_stream
        session_args["kwargs"] = kwargs
        yield "session"

    monkeypatch.setattr("colab_cli.transport.ClientSession", fake_client_session)
    transport = ColabTransport(server)

    async def run():
        async with transport.connect_session(foo="bar") as session:
            assert session == "session"

    asyncio.run(run())

    assert repr(transport) == "<ColabCliTransport>"
    assert session_args == {
        "read_stream": server.read_stream,
        "write_stream": server.write_stream,
        "kwargs": {"foo": "bar"},
    }
