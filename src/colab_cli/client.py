from collections.abc import Callable, Mapping
from typing import Any

from fastmcp import Client

from colab_cli.bridge import ColabBridge
from colab_cli.transport import ColabTransport


class ColabCliClient:
    def __init__(
        self,
        *,
        bridge_factory: Callable[[], ColabBridge] = ColabBridge,
        client_factory: Callable[[ColabTransport], Client] = Client,
    ):
        self._bridge_factory = bridge_factory
        self._client_factory = client_factory

    async def list_tools(
        self,
        *,
        timeout: float = 60.0,
        open_browser: bool = True,
    ) -> Any:
        async with self._bridge_factory() as bridge:
            if open_browser:
                bridge.open_browser()
            await bridge.wait_for_connection(timeout)
            async with self._client_factory(ColabTransport(bridge.server)) as client:
                return await client.list_tools()

    async def call_tool(
        self,
        name: str,
        arguments: Mapping[str, Any],
        *,
        timeout: float = 60.0,
        open_browser: bool = True,
    ) -> Any:
        if not isinstance(arguments, Mapping):
            raise TypeError("Tool arguments must be a JSON object")

        async with self._bridge_factory() as bridge:
            if open_browser:
                bridge.open_browser()
            await bridge.wait_for_connection(timeout)
            async with self._client_factory(ColabTransport(bridge.server)) as client:
                return await client.call_tool(name, dict(arguments))
