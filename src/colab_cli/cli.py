import argparse
import asyncio
from collections.abc import Callable, Sequence
import json
import sys
from typing import Any, TextIO

from colab_cli.bridge import ColabBridge, ColabConnectionTimeoutError
from colab_cli.json_util import to_jsonable
from colab_cli.runtime import (
    RuntimeClient,
    RuntimeServer,
    RuntimeServerError,
    RuntimeState,
    clear_state,
    write_state,
)


class CliUsageError(ValueError):
    pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="colab-cli",
        description="Connect a terminal command to a Google Colab browser session.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    connect = subparsers.add_parser(
        "connect",
        help="Open Colab and keep the bridge running.",
    )
    connect.add_argument("--timeout", type=float, default=60.0)
    connect.add_argument("--no-open", action="store_true")

    tools = subparsers.add_parser(
        "tools",
        help="List tools exposed by the Colab session.",
    )
    tools.add_argument("--timeout", type=float, default=60.0)
    tools.add_argument("--no-open", action="store_true")
    tools.add_argument("--json", dest="output_json", action="store_true")

    call = subparsers.add_parser("call", help="Call one Colab tool by name.")
    call.add_argument("tool_name")
    call.add_argument(
        "--json",
        required=True,
        help="JSON object arguments for the tool.",
    )
    call.add_argument("--timeout", type=float, default=60.0)
    call.add_argument("--no-open", action="store_true")

    return parser


def parse_json_object(value: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise CliUsageError(f"Invalid JSON: {exc.msg}") from exc
    if not isinstance(parsed, dict):
        raise CliUsageError("JSON arguments must be an object")
    return parsed


def tool_to_row(tool: Any) -> tuple[str, str]:
    name = getattr(tool, "name", "")
    description = getattr(tool, "description", "") or ""
    return str(name), str(description)


def print_tools_table(tools: Sequence[Any], stdout: TextIO) -> None:
    rows = [tool_to_row(tool) for tool in tools]
    if not rows:
        stdout.write("No tools exposed by the connected Colab session.\n")
        return
    name_width = max(len("NAME"), *(len(name) for name, _ in rows))
    stdout.write(f"{'NAME'.ljust(name_width)}  DESCRIPTION\n")
    for name, description in rows:
        stdout.write(f"{name.ljust(name_width)}  {description}\n")


def write_status(stdout: TextIO, text: str) -> None:
    stdout.write(text)
    stdout.flush()


async def run_connect(
    args: argparse.Namespace,
    stdout: TextIO,
    bridge_factory: Callable[[], ColabBridge],
    mcp_client_factory: Callable[[Any], Any],
    runtime_server_factory: Callable[[Any], RuntimeServer],
    sleep: Callable[[float], Any],
    state_file: Any = None,
) -> int:
    async with bridge_factory() as bridge:
        if not args.no_open:
            bridge.open_browser()
        write_status(stdout, f"Colab URL: {bridge.url}\n")
        write_status(stdout, "Waiting for Colab browser connection...\n")
        await bridge.wait_for_connection(args.timeout)
        from colab_cli.transport import ColabTransport

        async with mcp_client_factory(ColabTransport(bridge.server)) as mcp_client:
            runtime_server = runtime_server_factory(mcp_client)
            await runtime_server.start()
            write_state(
                RuntimeState(host=runtime_server.host, port=runtime_server.port),
                state_file,
            )
            write_status(stdout, "Connected. Press Ctrl+C to stop the bridge.\n")
            write_status(
                stdout,
                "Local control server: "
                f"{runtime_server.host}:{runtime_server.port}\n",
            )
            try:
                while True:
                    await sleep(3600)
            finally:
                clear_state(state_file)
                await runtime_server.close()


async def run_async(
    argv: Sequence[str] | None = None,
    *,
    runtime_client_factory: Callable[[], Any] | None = None,
    bridge_factory: Callable[[], ColabBridge] = ColabBridge,
    mcp_client_factory: Callable[[Any], Any] | None = None,
    runtime_server_factory: Callable[[Any], RuntimeServer] = RuntimeServer,
    sleep: Callable[[float], Any] = asyncio.sleep,
    state_file: Any = None,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if mcp_client_factory is None:
            from fastmcp import Client

            mcp_client_factory = Client

        if args.command == "connect":
            return await run_connect(
                args,
                stdout,
                bridge_factory,
                mcp_client_factory,
                runtime_server_factory,
                sleep,
                state_file,
            )

        if runtime_client_factory is None:
            runtime_client_factory = RuntimeClient

        if args.command == "tools":
            client = runtime_client_factory()
            tools = await client.list_tools(timeout=args.timeout)
            if args.output_json:
                stdout.write(json.dumps(to_jsonable(tools), indent=2) + "\n")
            else:
                print_tools_table(tools, stdout)
            return 0

        if args.command == "call":
            arguments = parse_json_object(args.json)
            client = runtime_client_factory()
            result = await client.call_tool(
                args.tool_name,
                arguments,
                timeout=args.timeout,
            )
            stdout.write(json.dumps(to_jsonable(result), indent=2) + "\n")
            return 0

        raise CliUsageError(f"Unknown command: {args.command}")
    except CliUsageError as exc:
        stderr.write(f"{exc}\n")
        return 2
    except ColabConnectionTimeoutError as exc:
        stderr.write(f"{exc}\n")
        return 1
    except RuntimeServerError as exc:
        stderr.write(f"{exc}\n")
        return 1
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        stderr.write(f"{type(exc).__name__}: {exc}\n")
        return 1


def main() -> None:
    raise SystemExit(asyncio.run(run_async()))
