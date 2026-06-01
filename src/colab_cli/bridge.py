import asyncio
from types import TracebackType
from typing import Callable
import webbrowser

from colab_cli.websocket_server import COLAB, SCRATCH_PATH, ColabWebSocketServer


class ColabConnectionTimeoutError(RuntimeError):
    def __init__(self, url: str, timeout: float):
        super().__init__(
            f"Timed out after {timeout:g}s waiting for Colab to connect. Open {url}"
        )
        self.url = url
        self.timeout = timeout


class ColabBridge:
    def __init__(
        self,
        *,
        server_factory: Callable[[], ColabWebSocketServer] = ColabWebSocketServer,
        browser_open: Callable[[str], object] = webbrowser.open_new,
    ):
        self._server_factory = server_factory
        self._browser_open = browser_open
        self._server: ColabWebSocketServer | None = None

    async def __aenter__(self) -> "ColabBridge":
        self._server = await self._server_factory().__aenter__()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        if self._server is not None:
            await self._server.__aexit__(exc_type, exc_val, exc_tb)

    @property
    def server(self) -> ColabWebSocketServer:
        if self._server is None:
            raise RuntimeError("Bridge has not been started")
        return self._server

    @property
    def url(self) -> str:
        server = self.server
        return (
            f"{COLAB}{SCRATCH_PATH}"
            f"#mcpProxyToken={server.token}&mcpProxyPort={server.port}"
        )

    def open_browser(self) -> None:
        self._browser_open(self.url)

    def is_connected(self) -> bool:
        return self.server.connection_live.is_set()

    async def wait_for_connection(self, timeout: float) -> None:
        try:
            await asyncio.wait_for(self.server.connection_live.wait(), timeout=timeout)
        except TimeoutError as exc:
            raise ColabConnectionTimeoutError(self.url, timeout) from exc
