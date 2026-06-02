# Bridge Lifecycle Management Design

## Context

`colab-cli connect` now owns a long-running Colab browser/WebSocket bridge and
publishes a small local runtime server. `tools` and `call` discover that server
through `~/.cache/colab-cli/server.json`.

The current lifecycle gap is operational: once `connect` is running, users can
only inspect or stop it from the terminal where it was launched. If that
terminal is hidden, detached, or being driven by Codex, the user has to inspect
processes manually.

## Goals

- Show whether a bridge is known and reachable.
- Stop the running bridge cleanly from another terminal.
- Allow a new `connect` invocation to replace a running bridge intentionally.
- Keep the bridge foreground-owned; do not introduce a background daemon yet.
- Preserve the existing `tools` and `call` behavior.

## Non-Goals

- Do not add log storage, background process spawning, or service supervision.
- Do not manage Colab-side processes such as SSH tunnels or notebook kernels.
- Do not support multiple simultaneous named bridge sessions.
- Do not use OS-specific process killing as the primary stop mechanism.

## CLI Surface

Add these commands and options:

```bash
colab-cli status
colab-cli status --json
colab-cli stop
colab-cli connect --replace
```

`status` reads the runtime state file and probes the local runtime server. The
human output should distinguish:

- no state file exists;
- state exists but no server is reachable;
- a server is reachable, including its host and port.

`status --json` prints a stable object such as:

```json
{
  "state": "running",
  "reachable": true,
  "host": "127.0.0.1",
  "port": 12345
}
```

`stop` sends a shutdown request to the runtime server. The running `connect`
process should clean up the state file, close its local runtime server, close
the Colab bridge, and exit normally.

`connect --replace` first attempts the same shutdown request as `stop`. If no
running bridge is reachable, it clears stale state and continues. If a running
bridge cannot be stopped cleanly, it reports the failure instead of silently
starting a competing bridge.

## Architecture

Extend the runtime control protocol in `runtime.py` with management methods:

- `status`: returns server identity and a simple running state.
- `shutdown`: schedules runtime server shutdown and returns before the process
  exits.

`RuntimeServer` should expose an internal shutdown event. `run_connect` should
wait on that event instead of sleeping forever. `KeyboardInterrupt` and
`shutdown` then share the same `finally` path for state cleanup and bridge
closure.

`RuntimeClient` should gain `status()`, `shutdown()`, and a lightweight
reachability helper. `cli.py` should remain responsible for argument parsing,
exit codes, and human/json formatting.

The state file remains the discovery mechanism. It should not store a PID yet;
the reachable runtime server is the source of truth for clean lifecycle
operations.

## Error Handling

- `status` exits `0` for all inspectable states and prints whether the bridge is
  missing, stale, or running.
- `stop` exits `0` when it stops a running bridge or when no bridge is running.
  It should clear stale state after reporting it.
- `stop` exits `1` only when state exists but a reachable server returns an
  unexpected management failure.
- `connect --replace` exits `1` if a reachable bridge refuses shutdown.
- Runtime shutdown requests should be idempotent.

## Testing

Use test-first coverage for:

- parser support for `status`, `status --json`, `stop`, and `connect --replace`;
- runtime server/client `status` and `shutdown` round trips;
- CLI output for missing, stale, and running status;
- `stop` clearing stale state and stopping a reachable runtime server;
- `connect --replace` invoking shutdown before starting a new bridge;
- existing `tools`, `call`, and `connect` behavior remaining intact.

Manual verification should include starting `colab-cli connect`, confirming
`status` reports it, stopping it with `colab-cli stop`, and confirming the
original `connect` process exits without a traceback.
