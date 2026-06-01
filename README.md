# Colab CLI

`colab-cli` connects a terminal command to a Google Colab browser session. It is
a CLI wrapper around the Colab MCP bridge: instead of exposing an MCP server to
another agent, it lets you list and call the tools that the connected Colab
session exposes.

## Install

```bash
uv sync --dev
```

## Commands

Open a Colab scratch notebook and keep the local bridge running:

```bash
uv run colab-cli connect
```

Leave this process running. It owns the WebSocket connection to the Colab tab
and publishes a local control server for the other commands.

List tools exposed by the connected Colab session:

```bash
uv run colab-cli tools
uv run colab-cli tools --json
```

Call one exposed tool by name with JSON arguments:

```bash
uv run colab-cli call run_cell --json '{"code": "print(1)"}'
```

Use `--timeout SECONDS` on commands that wait on a connection or runtime
response. Use `colab-cli connect --no-open` when you want to copy the printed
Colab URL into a browser yourself.

## Manual Smoke Test

1. In terminal 1, run `uv run colab-cli connect --timeout 60`.
2. Allow the command to open the Colab scratch notebook.
3. Wait for Colab to connect to the local bridge.
4. In terminal 2, run `uv run colab-cli tools --timeout 10`.
5. Confirm the terminal prints the discovered tool list.

Full end-to-end testing requires a real browser session in Google Colab.
