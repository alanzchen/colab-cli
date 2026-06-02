import asyncio
import contextlib
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from colab_cli.json_util import to_jsonable


class RuntimeServerError(RuntimeError):
    pass


class RuntimeConnectionError(RuntimeServerError):
    pass


@dataclass(frozen=True)
class RuntimeState:
    host: str
    port: int


def default_state_file() -> Path:
    return Path.home() / ".cache" / "colab-cli" / "server.json"


def write_state(state: RuntimeState, state_file: Path | None = None) -> None:
    path = state_file or default_state_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"host": state.host, "port": state.port}), encoding="utf-8")


def read_state(state_file: Path | None = None) -> RuntimeState:
    path = state_file or default_state_file()
    if not path.exists():
        raise RuntimeServerError(
            "No running colab-cli connect process found. Start one with "
            "`colab-cli connect`."
        )
    payload = json.loads(path.read_text(encoding="utf-8"))
    return RuntimeState(host=payload["host"], port=int(payload["port"]))


def clear_state(state_file: Path | None = None) -> None:
    path = state_file or default_state_file()
    with contextlib.suppress(FileNotFoundError):
        path.unlink()


class RuntimeServer:
    def __init__(self, mcp_client: Any, host: str = "127.0.0.1"):
        self._mcp_client = mcp_client
        self.host = host
        self.port = 0
        self._server: asyncio.Server | None = None
        self._shutdown_event = asyncio.Event()

    @property
    def shutdown_requested(self) -> bool:
        return self._shutdown_event.is_set()

    def request_shutdown(self) -> None:
        self._shutdown_event.set()

    async def wait_for_shutdown(self) -> None:
        await self._shutdown_event.wait()

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle_client, self.host, 0)
        socket = self._server.sockets[0]
        self.port = int(socket.getsockname()[1])

    async def close(self) -> None:
        if self._server is None:
            return
        self._server.close()
        await self._server.wait_closed()
        self._server = None

    async def serve_forever(self) -> None:
        if self._server is None:
            raise RuntimeServerError("Runtime server has not been started")
        await self._server.serve_forever()

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            raw_request = await reader.readline()
            request = json.loads(raw_request.decode("utf-8"))
            result = await self._dispatch(request)
            response = {"ok": True, "result": to_jsonable(result)}
        except Exception as exc:
            response = {
                "ok": False,
                "error_type": type(exc).__name__,
                "error": str(exc),
            }
        writer.write((json.dumps(response) + "\n").encode("utf-8"))
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    async def _dispatch(self, request: dict[str, Any]) -> Any:
        method = request.get("method")
        if method == "status":
            return {
                "state": "running",
                "reachable": True,
                "host": self.host,
                "port": self.port,
            }
        if method == "shutdown":
            self.request_shutdown()
            return {"stopping": True}
        if method == "list_tools":
            return await self._mcp_client.list_tools()
        if method == "call_tool":
            return await self._mcp_client.call_tool(
                request["name"],
                request.get("arguments", {}),
            )
        raise RuntimeServerError(f"Unknown runtime method: {method}")


class RuntimeClient:
    def __init__(self, state_file: Path | None = None):
        self._state_file = state_file

    async def list_tools(self, *, timeout: float) -> Any:
        return await self._request({"method": "list_tools"}, timeout=timeout)

    async def call_tool(self, name: str, arguments: dict[str, Any], *, timeout: float) -> Any:
        return await self._request(
            {"method": "call_tool", "name": name, "arguments": arguments},
            timeout=timeout,
        )

    async def status(self, *, timeout: float) -> dict[str, Any]:
        try:
            state = read_state(self._state_file)
        except RuntimeServerError:
            return {"state": "missing", "reachable": False}

        try:
            return await self._request_to_state(
                state,
                {"method": "status"},
                timeout=timeout,
            )
        except RuntimeConnectionError:
            return {
                "state": "stale",
                "reachable": False,
                "host": state.host,
                "port": state.port,
            }

    async def shutdown(self, *, timeout: float) -> dict[str, Any]:
        try:
            state = read_state(self._state_file)
        except RuntimeServerError:
            return {"stopping": False, "state": "missing", "reachable": False}

        try:
            return await self._request_to_state(
                state,
                {"method": "shutdown"},
                timeout=timeout,
            )
        except RuntimeConnectionError:
            clear_state(self._state_file)
            return {
                "stopping": False,
                "state": "stale",
                "reachable": False,
                "host": state.host,
                "port": state.port,
            }

    async def _request(self, payload: dict[str, Any], *, timeout: float) -> Any:
        state = read_state(self._state_file)
        return await self._request_to_state(state, payload, timeout=timeout)

    async def _request_to_state(
        self,
        state: RuntimeState,
        payload: dict[str, Any],
        *,
        timeout: float,
    ) -> Any:
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(state.host, state.port),
                timeout=timeout,
            )
        except OSError as exc:
            raise RuntimeConnectionError(
                "No running colab-cli connect process is reachable. Start one with "
                "`colab-cli connect`."
            ) from exc

        writer.write((json.dumps(payload) + "\n").encode("utf-8"))
        await writer.drain()
        try:
            raw_response = await asyncio.wait_for(reader.readline(), timeout=timeout)
        finally:
            writer.close()
            await writer.wait_closed()

        response = json.loads(raw_response.decode("utf-8"))
        if not response.get("ok"):
            raise RuntimeServerError(response.get("error", "Runtime request failed"))
        return response["result"]
