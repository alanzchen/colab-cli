import argparse
import asyncio
from collections.abc import Callable, Mapping, Sequence
import json
from pathlib import Path
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
    connect.add_argument("--replace", action="store_true")
    connect.add_argument(
        "--browser-ssh-host",
        help=(
            "Print an SSH port-forward command for opening the Colab URL from "
            "a different machine."
        ),
    )

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

    status = subparsers.add_parser(
        "status",
        help="Show bridge runtime status.",
    )
    status.add_argument("--timeout", type=float, default=5.0)
    status.add_argument("--json", dest="output_json", action="store_true")

    stop = subparsers.add_parser(
        "stop",
        help="Stop the running bridge.",
    )
    stop.add_argument("--timeout", type=float, default=5.0)

    ssh = subparsers.add_parser(
        "ssh",
        help="Set up SSH in the connected Colab session and connect.",
    )
    ssh.add_argument("--timeout", type=float, default=60.0)
    ssh.add_argument("--setup-timeout", type=float, default=300.0)
    ssh.add_argument("--setup-only", action="store_true")
    ssh.add_argument("--alias", default="colab-ssh")
    ssh.add_argument("--workspace", default="/content/workspace")
    ssh.add_argument("--bootstrap-url")
    ssh.add_argument("--key-path")
    ssh.add_argument("--config-path")
    ssh.add_argument("--known-hosts")
    ssh.add_argument("--cloudflared-path")
    ssh.add_argument("ssh_args", nargs=argparse.REMAINDER)

    bootstrap = subparsers.add_parser(
        "bootstrap",
        help="Print a standalone Colab worker setup cell.",
    )
    bootstrap.add_argument("--workspace", default="/content/workspace")
    bootstrap.add_argument("--bootstrap-url")
    bootstrap.add_argument("--key-path")
    bootstrap.add_argument("--tailscale-auth-key", default="")
    bootstrap.add_argument("--hostname-prefix", default="colab-worker")
    bootstrap.add_argument("--port", type=int, default=2222)
    bootstrap.add_argument("--no-cloudflare", action="store_true")

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
    if isinstance(tool, Mapping):
        name = tool.get("name", "")
        description = tool.get("description", "") or ""
    else:
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


def print_runtime_status(status: Mapping[str, Any], stdout: TextIO) -> None:
    state = status.get("state")
    if state == "running":
        stdout.write(
            "Bridge running at "
            f"{status.get('host')}:{status.get('port')}.\n"
        )
        return
    if state == "stale":
        stdout.write(
            "Bridge state is stale for "
            f"{status.get('host')}:{status.get('port')}; "
            "no runtime server is reachable.\n"
        )
        return
    stdout.write("No bridge is running.\n")


def print_stop_result(result: Mapping[str, Any], stdout: TextIO) -> None:
    if result.get("stopping"):
        stdout.write("Stopping bridge...\n")
        return
    if result.get("state") == "stale":
        stdout.write(
            "Cleared stale bridge state for "
            f"{result.get('host')}:{result.get('port')}.\n"
        )
        return
    stdout.write("No bridge is running.\n")


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
        if not args.no_open and not args.browser_ssh_host:
            bridge.open_browser()
        write_status(stdout, f"Colab URL: {bridge.url}\n")
        if args.browser_ssh_host:
            port = bridge.server.port
            write_status(
                stdout,
                "Browser machine port-forward:\n"
                f"  ssh -N -L {port}:127.0.0.1:{port} {args.browser_ssh_host}\n"
                "Open the Colab URL after the forward is running.\n",
            )
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
                await runtime_server.wait_for_shutdown()
            finally:
                clear_state(state_file)
                await runtime_server.close()
    return 0


async def run_async(
    argv: Sequence[str] | None = None,
    *,
    runtime_client_factory: Callable[[], Any] | None = None,
    ssh_manager_factory: Callable[..., Any] | None = None,
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
        if args.command == "connect":
            if args.replace:
                if runtime_client_factory is None:
                    runtime_client_factory = RuntimeClient
                client = runtime_client_factory()
                await client.shutdown(timeout=5.0)

            if mcp_client_factory is None:
                from fastmcp import Client

                mcp_client_factory = Client

            return await run_connect(
                args,
                stdout,
                bridge_factory,
                mcp_client_factory,
                runtime_server_factory,
                sleep,
                state_file,
            )

        if args.command == "bootstrap":
            from colab_cli.ssh import (
                build_worker_setup_cell,
                default_bootstrap_url,
                default_key_path,
                ensure_keypair,
            )

            key_path = (
                Path(args.key_path).expanduser()
                if args.key_path
                else default_key_path()
            )
            public_key = ensure_keypair(key_path)
            stdout.write(
                build_worker_setup_cell(
                    bootstrap_url=args.bootstrap_url or default_bootstrap_url(),
                    public_key=public_key,
                    workspace=args.workspace,
                    tailscale_auth_key=args.tailscale_auth_key,
                    hostname_prefix=args.hostname_prefix,
                    start_cloudflare=not args.no_cloudflare,
                    port=args.port,
                )
                + "\n"
            )
            return 0

        if runtime_client_factory is None:
            runtime_client_factory = RuntimeClient

        if args.command == "status":
            client = runtime_client_factory()
            status = await client.status(timeout=args.timeout)
            if args.output_json:
                stdout.write(json.dumps(to_jsonable(status), indent=2) + "\n")
            else:
                print_runtime_status(status, stdout)
            return 0

        if args.command == "stop":
            client = runtime_client_factory()
            result = await client.shutdown(timeout=args.timeout)
            print_stop_result(result, stdout)
            return 0

        if args.command == "ssh":
            if ssh_manager_factory is None:
                from colab_cli.ssh import ColabSshManager

                ssh_manager_factory = ColabSshManager

            client = runtime_client_factory()
            manager = ssh_manager_factory(
                client=client,
                alias=args.alias,
                workspace=args.workspace,
                bootstrap_url=args.bootstrap_url,
                key_path=Path(args.key_path).expanduser()
                if args.key_path
                else None,
                config_path=Path(args.config_path).expanduser()
                if args.config_path
                else None,
                known_hosts_path=Path(args.known_hosts).expanduser()
                if args.known_hosts
                else None,
                cloudflared_path=args.cloudflared_path,
            )
            ssh_args = list(args.ssh_args)
            if ssh_args and ssh_args[0] == "--":
                ssh_args = ssh_args[1:]
            code = await manager.setup_and_maybe_connect(
                setup_only=args.setup_only,
                ssh_args=ssh_args,
                timeout=args.timeout,
                setup_timeout=args.setup_timeout,
            )
            stdout.write(f"SSH configured. Run: ssh {args.alias}\n")
            return code

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
    except (KeyboardInterrupt, asyncio.CancelledError):
        return 130
    except Exception as exc:
        stderr.write(f"{type(exc).__name__}: {exc}\n")
        return 1


def main() -> None:
    raise SystemExit(asyncio.run(run_async()))
