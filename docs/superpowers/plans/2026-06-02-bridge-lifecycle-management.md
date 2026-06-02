# Bridge Lifecycle Management Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `status`, `stop`, and `connect --replace` lifecycle controls for the running Colab bridge.

**Architecture:** Extend the existing localhost runtime control server with management messages instead of adding process killing or a background daemon. `connect` will wait on the runtime server's shutdown event, so Ctrl+C and `stop` share the same cleanup path.

**Tech Stack:** Python 3.13, argparse, asyncio streams, pytest, ruff.

---

## File Structure

- Modify `src/colab_cli/runtime.py`: add runtime status payloads, shutdown event handling, client management calls, and stale-state clearing helpers.
- Modify `src/colab_cli/cli.py`: add parser entries and command handlers for `status`, `stop`, and `connect --replace`.
- Modify `tests/test_runtime.py`: cover server/client management round trips and missing/stale runtime cases.
- Modify `tests/test_cli.py`: cover parser shape, human/json status output, stop behavior, and replace behavior.
- Modify `README.md`: document lifecycle commands.

## Task 1: Runtime Management Protocol

**Files:**
- Modify: `src/colab_cli/runtime.py`
- Test: `tests/test_runtime.py`

- [ ] **Step 1: Write failing tests**

Add tests that start a `RuntimeServer`, write its state file, and verify:

```python
status = await RuntimeClient(state_file=state_file).status(timeout=1)
assert status == {
    "state": "running",
    "reachable": True,
    "host": server.host,
    "port": server.port,
}

result = await RuntimeClient(state_file=state_file).shutdown(timeout=1)
assert result == {"stopping": True}
assert server.shutdown_requested is True
```

Add a stale-state test:

```python
write_state(RuntimeState(host="127.0.0.1", port=9), state_file)

status = await RuntimeClient(state_file=state_file).status(timeout=0.1)

assert status["state"] == "stale"
assert status["reachable"] is False
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
uv run pytest tests/test_runtime.py::test_runtime_client_reports_status_from_server tests/test_runtime.py::test_runtime_client_requests_server_shutdown tests/test_runtime.py::test_runtime_client_reports_stale_status -v
```

Expected: fail because `RuntimeClient.status`, `RuntimeClient.shutdown`, and server shutdown state do not exist.

- [ ] **Step 3: Implement runtime management**

Add `RuntimeServer.shutdown_requested`, `RuntimeServer.wait_for_shutdown()`, and `RuntimeServer.request_shutdown()`.

Extend `_dispatch()`:

```python
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
```

Add `RuntimeClient.status(timeout=...)` and `RuntimeClient.shutdown(timeout=...)`. `status()` should return missing/stale dictionaries instead of raising for missing state or refused connections.

- [ ] **Step 4: Run tests to verify pass**

Run the same targeted pytest command. Expected: all selected tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/colab_cli/runtime.py tests/test_runtime.py
git commit --no-gpg-sign -m "feat: add runtime lifecycle protocol"
```

## Task 2: CLI Lifecycle Commands

**Files:**
- Modify: `src/colab_cli/cli.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write failing parser and command tests**

Add parser coverage:

```python
status = parser.parse_args(["status", "--json"])
stop = parser.parse_args(["stop"])
replace = parser.parse_args(["connect", "--replace"])
assert status.command == "status"
assert status.output_json is True
assert stop.command == "stop"
assert replace.replace is True
```

Extend `FakeRuntimeClient` with `status()` and `shutdown()` calls. Add tests:

```python
client = FakeRuntimeClient(status_result={"state": "running", "reachable": True, "host": "127.0.0.1", "port": 1234})
code = await cli.run_async(["status"], runtime_client_factory=lambda: client, stdout=stdout, stderr=stderr)
assert code == 0
assert "running" in stdout.text
assert "127.0.0.1:1234" in stdout.text

client = FakeRuntimeClient(status_result={"state": "missing", "reachable": False})
code = await cli.run_async(["status", "--json"], runtime_client_factory=lambda: client, stdout=stdout, stderr=stderr)
assert '"state": "missing"' in stdout.text

client = FakeRuntimeClient(shutdown_result={"stopping": True})
code = await cli.run_async(["stop"], runtime_client_factory=lambda: client, stdout=stdout, stderr=stderr)
assert code == 0
assert client.calls == [("shutdown", 5.0)]
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
uv run pytest tests/test_cli.py::test_parser_accepts_lifecycle_commands tests/test_cli.py::test_status_command_prints_running_status tests/test_cli.py::test_status_command_prints_json tests/test_cli.py::test_stop_command_requests_shutdown -v
```

Expected: fail because parser and command handlers do not exist.

- [ ] **Step 3: Implement CLI lifecycle commands**

Add parser entries:

```python
connect.add_argument("--replace", action="store_true")
status = subparsers.add_parser("status", help="Show bridge runtime status.")
status.add_argument("--timeout", type=float, default=5.0)
status.add_argument("--json", dest="output_json", action="store_true")
stop = subparsers.add_parser("stop", help="Stop the running bridge.")
stop.add_argument("--timeout", type=float, default=5.0)
```

Add formatting helpers for status and stop output. Add `run_async` branches:

```python
if args.command == "status":
    client = runtime_client_factory()
    status = await client.status(timeout=args.timeout)
    ...

if args.command == "stop":
    client = runtime_client_factory()
    result = await client.shutdown(timeout=args.timeout)
    ...
```

- [ ] **Step 4: Run tests to verify pass**

Run the same targeted pytest command. Expected: all selected tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/colab_cli/cli.py tests/test_cli.py
git commit --no-gpg-sign -m "feat: add bridge status and stop commands"
```

## Task 3: Connect Replace Flow

**Files:**
- Modify: `src/colab_cli/cli.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write failing replace tests**

Add tests verifying `connect --replace` calls `shutdown()` before opening a new bridge and continues when status is missing/stale:

```python
client = FakeRuntimeClient(shutdown_result={"stopping": True})
code = await cli.run_async(["connect", "--replace", "--timeout", "4"], runtime_client_factory=lambda: client, ...)
assert client.calls[0] == ("shutdown", 5.0)
assert bridge.opened is True
```

Add a failure test where `shutdown()` raises `RuntimeServerError("refused")`; assert exit code `1`, bridge not opened, and stderr contains `refused`.

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
uv run pytest tests/test_cli.py::test_connect_replace_stops_existing_runtime_before_connecting tests/test_cli.py::test_connect_replace_fails_when_shutdown_fails -v
```

Expected: fail because `connect --replace` is parsed but not acted on.

- [ ] **Step 3: Implement replace**

Before `run_connect(...)`, when `args.replace` is true:

```python
client = runtime_client_factory()
await client.shutdown(timeout=5.0)
```

Let `RuntimeServerError` propagate to the existing error handling. Treat missing/stale shutdown results as non-fatal when `RuntimeClient.shutdown()` reports them as structured results.

- [ ] **Step 4: Run tests to verify pass**

Run the same targeted pytest command. Expected: both tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/colab_cli/cli.py tests/test_cli.py
git commit --no-gpg-sign -m "feat: support replacing running bridge"
```

## Task 4: Documentation And Verification

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Update README**

Document:

```bash
uv run colab-cli status
uv run colab-cli stop
uv run colab-cli connect --replace
```

Explain that `stop` is the normal way to shut down a bridge from another terminal.

- [ ] **Step 2: Run full verification**

Run:

```bash
uv run pytest -v
uv run ruff check .
```

Expected: pytest reports all tests passing; ruff reports `All checks passed!`.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit --no-gpg-sign -m "docs: document bridge lifecycle commands"
```

- [ ] **Step 4: Manual smoke test**

Start a bridge:

```bash
uv run colab-cli connect --timeout 180
```

In another command, run:

```bash
uv run colab-cli status
uv run colab-cli stop
```

Expected: `status` reports the bridge as running, `stop` reports shutdown, and the original `connect` process exits without traceback.
