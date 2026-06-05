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

Inspect or stop the running bridge from another terminal:

```bash
uv run colab-cli status
uv run colab-cli status --json
uv run colab-cli stop
```

Start a fresh bridge intentionally, stopping a reachable existing bridge first:

```bash
uv run colab-cli connect --replace
```

When `colab-cli` runs on a remote machine but the browser is on your local
machine, print an SSH port-forward command for the browser-side `localhost`
bridge:

```bash
uv run colab-cli connect --browser-ssh-host oracle --timeout 600
```

Run the printed `ssh -N -L ... oracle` command on the browser machine, then
open the printed Colab URL. This is needed because Colab connects to
`localhost:<mcpProxyPort>` from the browser machine.

List tools exposed by the connected Colab session:

```bash
uv run colab-cli tools
uv run colab-cli tools --json
```

Call one exposed tool by name with JSON arguments:

```bash
uv run colab-cli call run_cell --json '{"code": "print(1)"}'
```

Print a standalone Colab worker bootstrap cell:

```bash
uv run colab-cli bootstrap
```

Paste the printed cell into a fresh Colab notebook. The cell downloads the
published bootstrap script, installs OpenSSH, starts Tailscale userspace
networking, starts a Cloudflare quick tunnel, and prints a `READY` block:

```text
HOSTNAME=...
COLAB_TAILSCALE_IP=100.x.y.z
CLOUDFLARE_HOST=https://....trycloudflare.com
```

If you do not pass `--tailscale-auth-key`, Colab prints an interactive
Tailscale login URL during setup. Use an ephemeral auth key for unattended
startup:

```bash
uv run colab-cli bootstrap --tailscale-auth-key tskey-auth-...
```

Set up SSH in the connected Colab notebook and connect:

```bash
uv run colab-cli ssh
```

The `ssh` command creates or reuses `~/.ssh/colab_cli_ed25519`, appends a
setup cell to the connected notebook, starts OpenSSH and a Cloudflare quick
tunnel inside Colab, then writes a managed `Host colab-ssh` block to
`~/.ssh/config`. After setup you can reconnect with:

```bash
ssh colab-ssh
```

Use `--setup-only` when you only want to refresh local SSH config:

```bash
uv run colab-cli ssh --setup-only
```

Pass SSH arguments after `--`:

```bash
uv run colab-cli ssh -- -L 8888:localhost:8888
```

By default the notebook setup cell loads:

```text
https://raw.githubusercontent.com/alanzchen/colab-cli/main/scripts/colab_ssh_bootstrap.py
```

Set `COLAB_CLI_SSH_BOOTSTRAP_URL` or pass `--bootstrap-url` to test a fork or a
temporary copy before the repository is public.

Use `--timeout SECONDS` on commands that wait on a connection or runtime
response. Use `colab-cli connect --no-open` when you want to copy the printed
Colab URL into a browser yourself. Use `colab-cli stop` when the bridge was
started in another terminal and you want it to shut down cleanly.

## Manual Smoke Test

1. In terminal 1, run `uv run colab-cli connect --timeout 60`.
2. Allow the command to open the Colab scratch notebook.
3. Wait for Colab to connect to the local bridge.
4. In terminal 2, run `uv run colab-cli status`.
5. Confirm the terminal reports the bridge as running.
6. Run `uv run colab-cli tools --timeout 10`.
7. Confirm the terminal prints the discovered tool list.
8. Run `uv run colab-cli stop`.
9. Confirm terminal 1 exits without a traceback.

Full end-to-end testing requires a real browser session in Google Colab.
