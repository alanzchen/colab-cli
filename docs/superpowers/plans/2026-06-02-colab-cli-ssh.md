# Colab CLI SSH Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `colab-cli ssh` to set up key-based SSH in the active Colab notebook using a repo-owned bootstrap script.

**Architecture:** Put SSH workflow logic in `src/colab_cli/ssh.py`, keep `cli.py` as argument parsing and output orchestration, and publish the Colab-side setup code as `scripts/colab_ssh_bootstrap.py`. The local command uses existing Colab MCP tools through `RuntimeClient`; the Colab script owns OpenSSH/cloudflared setup and prints a JSON marker the CLI parses.

**Tech Stack:** Python 3.13, argparse, asyncio, subprocess, OpenSSH, cloudflared, pytest, ruff.

---

## File Structure

- Create `src/colab_cli/ssh.py`: local SSH workflow helpers, setup cell builder, output parser, SSH config writer, key/cloudflared checks, and workflow class.
- Create `scripts/colab_ssh_bootstrap.py`: public Colab bootstrap script fetched from GitHub raw.
- Create `tests/test_ssh.py`: pure unit tests for SSH helpers and workflow behavior with fake runtime/subprocess layers.
- Modify `src/colab_cli/cli.py`: add `ssh` parser and command handler.
- Modify `tests/test_cli.py`: parser and CLI command tests.
- Modify `README.md`: document `colab-cli ssh` usage and publish-time bootstrap URL override.

## Task 1: SSH Helper Module

**Files:**
- Create: `src/colab_cli/ssh.py`
- Create: `tests/test_ssh.py`

- [ ] **Step 1: Write failing tests**

Add tests for:

```python
def test_default_bootstrap_url_uses_inferred_github_repo(monkeypatch):
    monkeypatch.delenv("COLAB_CLI_SSH_BOOTSTRAP_URL", raising=False)
    assert ssh.default_bootstrap_url() == (
        "https://raw.githubusercontent.com/alanzchen/colab-cli/main/"
        "scripts/colab_ssh_bootstrap.py"
    )

def test_default_bootstrap_url_uses_env_override(monkeypatch):
    monkeypatch.setenv("COLAB_CLI_SSH_BOOTSTRAP_URL", "https://example/setup.py")
    assert ssh.default_bootstrap_url() == "https://example/setup.py"

def test_build_setup_cell_includes_url_key_and_workspace():
    code = ssh.build_setup_cell(
        bootstrap_url="https://example/setup.py",
        public_key="ssh-ed25519 AAAA test",
        workspace="/content/work",
    )
    assert "https://example/setup.py" in code
    assert "ssh-ed25519 AAAA test" in code
    assert "/content/work" in code

def test_parse_setup_result_extracts_json_marker():
    result = {
        "structured_content": {
            "outputs": [
                {
                    "output_type": "stream",
                    "text": [
                        "noise\n",
                        'COLAB_CLI_SSH_JSON={"hostname":"x.trycloudflare.com","user":"root","workspace":"/content/work"}\n',
                    ],
                }
            ]
        }
    }
    assert ssh.parse_setup_result(result)["hostname"] == "x.trycloudflare.com"

def test_render_ssh_config_replaces_managed_block(tmp_path):
    config = tmp_path / "config"
    config.write_text("Host old\n\tHostName old\n")
    ssh.write_ssh_config(
        config_path=config,
        alias="colab-ssh",
        hostname="x.trycloudflare.com",
        identity_file=tmp_path / "key",
        cloudflared_path="/opt/homebrew/bin/cloudflared",
        known_hosts_file=tmp_path / "known_hosts",
    )
    text = config.read_text()
    assert "Host colab-ssh" in text
    assert "x.trycloudflare.com" in text
    assert "BEGIN COLAB-CLI SSH colab-ssh" in text
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
uv run pytest tests/test_ssh.py -v
```

Expected: fail because `colab_cli.ssh` does not exist.

- [ ] **Step 3: Implement helpers**

Create `src/colab_cli/ssh.py` with:

- constants for default URL, key path, alias, workspace, known hosts;
- `default_bootstrap_url()`;
- `build_setup_cell(bootstrap_url, public_key, workspace)`;
- `parse_setup_result(result)`;
- `write_ssh_config(config_path, alias, hostname, identity_file, cloudflared_path, known_hosts_file)`;
- `find_cloudflared()`;
- `ensure_keypair(key_path, subprocess_run)` using `ssh-keygen` only when key files are absent.

- [ ] **Step 4: Run tests to verify pass**

Run:

```bash
uv run pytest tests/test_ssh.py -v
```

Expected: all `tests/test_ssh.py` tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/colab_cli/ssh.py tests/test_ssh.py
git commit --no-gpg-sign -m "feat: add colab ssh helpers"
```

## Task 2: SSH Workflow And CLI Command

**Files:**
- Modify: `src/colab_cli/ssh.py`
- Modify: `src/colab_cli/cli.py`
- Modify: `tests/test_ssh.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Write failing tests**

Add workflow tests with a fake runtime client:

```python
async def test_setup_ssh_calls_colab_tools_and_returns_info(tmp_path):
    client = FakeRuntimeClient()
    manager = ssh.ColabSshManager(
        client=client,
        alias="colab-ssh",
        workspace="/content/work",
        bootstrap_url="https://example/setup.py",
        key_path=tmp_path / "key",
        config_path=tmp_path / "config",
        known_hosts_path=tmp_path / "known_hosts",
        cloudflared_path="/opt/homebrew/bin/cloudflared",
        subprocess_run=lambda args, **kwargs: CompletedProcess(args, 0),
    )
    info = await manager.setup()
    assert client.calls[0][0] == "get_cells"
    assert client.calls[1][0] == "add_code_cell"
    assert client.calls[2][0] == "run_code_cell"
    assert info.hostname == "x.trycloudflare.com"
```

Add CLI tests:

```python
def test_parser_accepts_ssh_command():
    args = cli.build_parser().parse_args(["ssh", "--setup-only", "--alias", "gpu", "--", "whoami"])
    assert args.command == "ssh"
    assert args.setup_only is True
    assert args.alias == "gpu"
    assert args.ssh_args == ["whoami"]

async def test_ssh_command_prints_setup_info():
    fake = FakeSshManager(exit_code=0)
    code = await cli.run_async(
        ["ssh", "--setup-only"],
        ssh_manager_factory=lambda **kwargs: fake,
        runtime_client_factory=lambda: FakeRuntimeClient(),
        stdout=stdout,
        stderr=stderr,
    )
    assert code == 0
    assert "ssh colab-ssh" in stdout.text
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
uv run pytest tests/test_ssh.py tests/test_cli.py::test_parser_accepts_ssh_command tests/test_cli.py::test_ssh_command_prints_setup_info -v
```

Expected: fail because workflow and parser do not exist.

- [ ] **Step 3: Implement workflow and CLI**

Add `SshSetupInfo` dataclass and `ColabSshManager` with:

- `setup()` to ensure cloudflared/key, call Colab tools, parse setup JSON, write config;
- `connect(ssh_args)` to run `ssh <alias> [ssh_args]` via injectable subprocess runner;
- `setup_and_maybe_connect(setup_only, ssh_args)` to return final exit code.

Add `ssh` parser in `cli.py` with `nargs=argparse.REMAINDER` for trailing args. Add `ssh_manager_factory` injection to `run_async`, defaulting to `ColabSshManager`.

- [ ] **Step 4: Run tests to verify pass**

Run the same targeted pytest command. Expected: selected tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/colab_cli/ssh.py src/colab_cli/cli.py tests/test_ssh.py tests/test_cli.py
git commit --no-gpg-sign -m "feat: add colab ssh command"
```

## Task 3: Public Colab Bootstrap Script

**Files:**
- Create: `scripts/colab_ssh_bootstrap.py`
- Test: `tests/test_ssh.py`

- [ ] **Step 1: Write failing bootstrap tests**

Add tests that load the script as text and assert it exposes:

```python
source = Path("scripts/colab_ssh_bootstrap.py").read_text()
assert "def setup(" in source
assert "COLAB_CLI_SSH_JSON=" in source
assert "ssh://localhost:" in source
assert "cloudflared" in source
assert "authorized_keys" in source
```

- [ ] **Step 2: Run test to verify failure**

Run:

```bash
uv run pytest tests/test_ssh.py::test_bootstrap_script_contains_required_entrypoints -v
```

Expected: fail because the script file does not exist.

- [ ] **Step 3: Implement bootstrap script**

Create `scripts/colab_ssh_bootstrap.py` with `setup(public_key, workspace="/content/workspace")`. The script should:

- install `openssh-server` via `apt-get` if `sshd` is absent;
- create workspace and `/root/.ssh/authorized_keys`;
- configure `/etc/ssh/sshd_config` for root public-key login and port `2222`;
- restart or launch `sshd`;
- download `cloudflared-linux-amd64` if `./cloudflared` is absent;
- start `./cloudflared tunnel --url ssh://localhost:2222 --logfile ./cloudflared-colab-cli.log --metrics localhost:45679`;
- parse the generated `trycloudflare.com` hostname from the log;
- print `COLAB_CLI_SSH_JSON=<json>`.

- [ ] **Step 4: Run test to verify pass**

Run the same targeted test. Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add scripts/colab_ssh_bootstrap.py tests/test_ssh.py
git commit --no-gpg-sign -m "feat: add colab ssh bootstrap script"
```

## Task 4: Documentation And Verification

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Update README**

Add `colab-cli ssh` examples:

```bash
uv run colab-cli connect --replace
uv run colab-cli ssh --setup-only
ssh colab-ssh
uv run colab-cli ssh -- whoami
```

Document `COLAB_CLI_SSH_BOOTSTRAP_URL`.

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
git commit --no-gpg-sign -m "docs: document colab ssh command"
```

- [ ] **Step 4: Manual smoke test**

Start a bridge and run setup:

```bash
uv run colab-cli connect --replace --timeout 180
python3 -m http.server 8765 --directory scripts
COLAB_CLI_SSH_BOOTSTRAP_URL=http://host.docker.internal:8765/colab_ssh_bootstrap.py uv run colab-cli ssh --setup-only
```

For the real publish path, use a reachable raw GitHub URL or a temporary local HTTP server serving `scripts/colab_ssh_bootstrap.py`, then verify:

```bash
ssh colab-ssh 'echo alive && hostname && pwd'
```
