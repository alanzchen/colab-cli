# Colab CLI Design

## Context

This project wraps `googlecolab/colab-mcp` as a command-line tool instead of an MCP server. The local repository is currently empty. The upstream repository at `https://github.com/googlecolab/colab-mcp/tree/main` is a Python package that:

- starts a local WebSocket server that accepts one Google Colab browser connection;
- opens a Colab scratch notebook with a proxy token and port in the URL fragment;
- connects a FastMCP client to the Colab browser session over that WebSocket;
- exposes the connected Colab session as dynamically discovered MCP tools.

The approved direction is to keep the upstream bridge behavior, replace the MCP server surface with terminal commands, and ship a generic CLI first. Friendly notebook aliases can be added later once the runtime tool names exposed by Colab are known.

## Goals

- Provide an installable Python CLI named `colab-cli`.
- Reuse the upstream Colab WebSocket bridge model rather than reimplementing the browser-side protocol.
- Let users connect a local terminal process to a Colab browser session.
- Let users list dynamically exposed Colab tools from the terminal.
- Let users invoke any exposed Colab tool by name with JSON arguments.
- Print machine-readable JSON for successful tool calls and actionable messages for connection or input failures.

## Non-Goals

- Do not preserve an MCP server command as the primary product surface.
- Do not guess notebook-specific friendly commands before the connected Colab runtime reveals stable tool names.
- Do not add a daemon, persistent background service, or credential store in the first version.
- Do not implement browser automation for Colab beyond opening the upstream scratch notebook URL.
- Do not fork or modify Colab's browser-side behavior.

## CLI Surface

The first version exposes three commands:

```bash
colab-cli connect
colab-cli tools
colab-cli call <tool-name> --json '{"arg": "value"}'
```

`connect` starts the local WebSocket bridge, opens the Colab scratch notebook URL containing the bridge token and port, and waits until a browser session connects. It prints connection status and leaves the process running until interrupted, so the terminal owns the bridge lifecycle.

`tools` starts the bridge, opens or reports the connection URL, waits for Colab to connect, asks the connected session for its available tools, and prints them. The default output is a readable table with names and descriptions. `--json` prints the raw tool metadata as JSON.

`call` starts the bridge, waits for the Colab session, invokes the named tool with the parsed JSON object from `--json`, and prints the tool result as JSON. Invalid JSON exits before starting the bridge.

All commands support a shared `--timeout SECONDS` option. Commands that need a Colab session fail with a timeout error if the browser does not connect in time.

## Architecture

The implementation is a small Python package under `src/colab_cli`.

`bridge.py` owns the WebSocket bridge lifecycle. It wraps the upstream `ColabWebSocketServer`, constructs the Colab scratch URL from the token and selected port, optionally opens it with `webbrowser.open_new`, waits for `connection_live`, and cleans up streams and sockets on exit.

`transport.py` adapts the bridge streams into an MCP client transport. It mirrors the upstream `ColabTransport` concept but lives in the CLI package so the CLI does not need to instantiate a FastMCP server or proxy.

`client.py` contains the terminal-facing operations: `list_tools()` and `call_tool(name, args)`. It receives a bridge factory and uses the MCP/FastMCP client APIs to talk to the connected Colab browser session.

`cli.py` contains argument parsing, JSON input parsing, output formatting, exit codes, and signal-safe command orchestration. It should be thin; behavior belongs in `bridge.py` and `client.py`.

The package will copy the small upstream bridge modules into this repository under the `colab_cli` namespace with license headers preserved. This keeps the CLI standalone and avoids depending on the upstream package's MCP server entry point.

## Data Flow

1. The user runs a `colab-cli` command.
2. The CLI starts a local WebSocket bridge on `localhost` with an ephemeral port and token.
3. The CLI opens `https://colab.research.google.com/notebooks/empty.ipynb#mcpProxyToken=<token>&mcpProxyPort=<port>`.
4. The user allows or waits for the Colab browser session to connect.
5. The CLI creates an MCP client over the bridge streams.
6. `tools` requests the tool list and formats the response.
7. `call` sends the named tool call with JSON arguments and prints the result.
8. The CLI closes the bridge when the command exits.

## Error Handling

- Invalid JSON arguments: print a concise parse error and exit with code `2`.
- Browser connection timeout: print the Colab URL and a timeout message, then exit with code `1`.
- Colab disconnects during a request: print a disconnected-session message and exit with code `1`.
- Unknown tool or tool failure: print the structured error when available and exit with code `1`.
- Keyboard interrupt: close sockets and streams before exiting with code `130`.

## Testing

Tests should be written before implementation code.

- CLI parser tests cover subcommand parsing, shared timeout options, `--json` output flags, and invalid JSON failures.
- Bridge unit tests mock `ColabWebSocketServer` and `webbrowser.open_new` to verify URL construction, wait behavior, timeout handling, and cleanup.
- Client tests use a fake MCP client/transport to verify `list_tools()` and `call_tool()` behavior without requiring a real Colab browser.
- A local WebSocket integration test can reuse upstream-style WebSocket server coverage where it does not require external Colab access.

Full end-to-end testing against a real Colab browser session is manual for the first version because it requires user interaction in Google Colab.

## Packaging And Documentation

The repository should include:

- `pyproject.toml` with a `colab-cli = "colab_cli.cli:main"` console script.
- `README.md` with install instructions, the three core commands, and a manual Colab connection smoke test.
- `LICENSE` compatible with the upstream Apache-2.0 source and preserved upstream copyright headers in copied files.

## Open Decisions Resolved

- The initial CLI is generic and dynamic rather than notebook-command-specific.
- The first version runs one foreground command at a time and does not provide a background daemon.
- Friendly aliases are explicitly deferred until the generic tool list reveals stable names.
