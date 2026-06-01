import asyncio

from mcp.shared.message import SessionMessage
from mcp.types import JSONRPCMessage, JSONRPCRequest, JSONRPCResponse
import pytest
import websockets

from colab_cli.websocket_server import ColabWebSocketServer


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "origin_domain",
    ["https://colab.google.com", "https://colab.research.google.com"],
)
async def test_successful_connection(origin_domain):
    async with ColabWebSocketServer() as server:
        client = await websockets.connect(
            f"ws://localhost:{server.port}",
            origin=origin_domain,
            subprotocols=["mcp"],
            additional_headers={"Authorization": f"Bearer {server.token}"},
            proxy=None,
        )

        assert server.connection_live.is_set()
        assert server.connection_lock.locked()

        await client.close()
        await client.wait_closed()
        await asyncio.sleep(0.1)

        assert not server.connection_live.is_set()
        assert not server.connection_lock.locked()


@pytest.mark.asyncio
async def test_unauthorized_origin_rejected():
    async with ColabWebSocketServer() as server:
        with pytest.raises(websockets.exceptions.InvalidStatus):
            await websockets.connect(
                f"ws://localhost:{server.port}",
                origin="https://wrong.example",
                subprotocols=["mcp"],
                additional_headers={"Authorization": f"Bearer {server.token}"},
                proxy=None,
            )

        assert not server.connection_live.is_set()


@pytest.mark.asyncio
async def test_incoming_message_handling():
    async with ColabWebSocketServer() as server:
        client = await websockets.connect(
            f"ws://localhost:{server.port}",
            origin="https://colab.google.com",
            subprotocols=["mcp"],
            additional_headers={"Authorization": f"Bearer {server.token}"},
            proxy=None,
        )
        test_message = JSONRPCResponse(
            jsonrpc="2.0",
            id="abc",
            result={"result": "success"},
        )

        await client.send(test_message.model_dump_json())

        received_msg = await asyncio.wait_for(server.read_stream.receive(), timeout=1)
        assert received_msg.message == JSONRPCMessage(test_message)

        await client.close()


@pytest.mark.asyncio
async def test_outgoing_message_handling():
    async with ColabWebSocketServer() as server:
        client = await websockets.connect(
            f"ws://localhost:{server.port}",
            origin="https://colab.google.com",
            subprotocols=["mcp"],
            additional_headers={"Authorization": f"Bearer {server.token}"},
            proxy=None,
        )
        test_message = JSONRPCRequest(
            jsonrpc="2.0",
            id="abc",
            method="test_method",
            params={"bar": "baz"},
        )

        await server.write_stream.send(SessionMessage(test_message))

        received_msg_str = await asyncio.wait_for(client.recv(), timeout=1)
        assert JSONRPCRequest.model_validate_json(received_msg_str) == test_message

        await client.close()


@pytest.mark.asyncio
async def test_token_in_url():
    async with ColabWebSocketServer() as server:
        client = await websockets.connect(
            f"ws://localhost:{server.port}?access_token={server.token}",
            origin="https://colab.google.com",
            subprotocols=["mcp"],
            proxy=None,
        )

        assert server.connection_live.is_set()
        assert server.connection_lock.locked()

        await client.close()
        await client.wait_closed()
        await asyncio.sleep(0.1)

        assert not server.connection_live.is_set()
        assert not server.connection_lock.locked()
