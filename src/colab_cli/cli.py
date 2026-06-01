import argparse
import asyncio
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, is_dataclass
import json
import sys
from typing import Any, TextIO

from colab_cli.bridge import ColabBridge, ColabConnectionTimeoutError
from colab_cli.client import ColabCliClient


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


def to_jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, Mapping):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [to_jsonable(item) for item in value]
    if hasattr(value, "__dict__"):
        return {
            key: to_jsonable(item)
            for key, item in vars(value).items()
            if not key.startswith("_")
        }
    return value


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


async def run_connect(
    args: argparse.Namespace,
    stdout: TextIO,
    bridge_factory: Callable[[], ColabBridge],
    sleep: Callable[[float], Any],
) -> int:
    async with bridge_factory() as bridge:
        if not args.no_open:
            bridge.open_browser()
        stdout.write(f"Colab URL: {bridge.url}\n")
        stdout.write("Waiting for Colab browser connection...\n")
        await bridge.wait_for_connection(args.timeout)
        stdout.write("Connected. Press Ctrl+C to stop the bridge.\n")
        while True:
            await sleep(3600)


async def run_async(
    argv: Sequence[str] | None = None,
    *,
    client_factory: Callable[[], ColabCliClient] = ColabCliClient,
    bridge_factory: Callable[[], ColabBridge] = ColabBridge,
    sleep: Callable[[float], Any] = asyncio.sleep,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "connect":
            return await run_connect(args, stdout, bridge_factory, sleep)

        client = client_factory()
        if args.command == "tools":
            tools = await client.list_tools(
                timeout=args.timeout,
                open_browser=not args.no_open,
            )
            if args.output_json:
                stdout.write(json.dumps(to_jsonable(tools), indent=2) + "\n")
            else:
                print_tools_table(tools, stdout)
            return 0

        if args.command == "call":
            arguments = parse_json_object(args.json)
            result = await client.call_tool(
                args.tool_name,
                arguments,
                timeout=args.timeout,
                open_browser=not args.no_open,
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
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        stderr.write(f"{type(exc).__name__}: {exc}\n")
        return 1


def main() -> None:
    raise SystemExit(asyncio.run(run_async()))
