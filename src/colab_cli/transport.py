import contextlib
from collections.abc import AsyncIterator

from fastmcp.client.transports import ClientTransport
from mcp.client.session import ClientSession

from colab_cli.websocket_server import ColabWebSocketServer


class ColabTransport(ClientTransport):
    def __init__(self, wss: ColabWebSocketServer):
        self.wss = wss

    @contextlib.asynccontextmanager
    async def connect_session(self, **session_kwargs) -> AsyncIterator[ClientSession]:
        async with ClientSession(
            self.wss.read_stream,
            self.wss.write_stream,
            **session_kwargs,
        ) as session:
            yield session

    def __repr__(self) -> str:
        return "<ColabCliTransport>"
