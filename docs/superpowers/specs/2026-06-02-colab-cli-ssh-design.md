# Colab CLI SSH Design

## Context

`colab-cli` already manages a long-running browser bridge with `connect`,
`status`, and `stop`, and can call notebook tools through the runtime server.
Manual SSH setup proved that Colab can run OpenSSH behind a Cloudflare quick
tunnel, but `colab_ssh` is not reliable enough for this CLI because it can
target the wrong SSH port and leaves setup details implicit.

The repository has no remote yet. `gh` reports the active GitHub user as
`alanzchen`, so the default published bootstrap URL is:

```text
https://raw.githubusercontent.com/alanzchen/colab-cli/main/scripts/colab_ssh_bootstrap.py
```

## Goals

- Add `colab-cli ssh` as a reproducible shortcut for setting up and connecting
  to SSH in the active Colab notebook.
- Own the Colab-side bootstrap script in this repository instead of using
  `colab_ssh`.
- Use the existing bridge runtime and notebook tools to add/run the setup cell.
- Use key-based SSH by default.
- Make the GitHub raw bootstrap URL overridable for forks and development.

## Non-Goals

- Do not add a separate `colab_ssh` dependency.
- Do not require a Cloudflare account or named tunnel.
- Do not manage long-lived Colab-side SSH service lifecycle beyond setup.
- Do not mount local folders as part of this command.
- Do not create or publish the GitHub repository in this feature.

## CLI Surface

Add:

```bash
colab-cli ssh
colab-cli ssh --setup-only
colab-cli ssh --alias colab-ssh
colab-cli ssh --workspace /content/workspace
colab-cli ssh --bootstrap-url https://raw.githubusercontent.com/OWNER/REPO/main/scripts/colab_ssh_bootstrap.py
colab-cli ssh -- whoami
```

Defaults:

- `--alias colab-ssh`
- `--workspace /content/workspace`
- `--key-path ~/.ssh/colab_cli_ed25519`
- `--known-hosts ~/.cache/colab-cli/ssh_known_hosts`
- `--bootstrap-url` from `COLAB_CLI_SSH_BOOTSTRAP_URL`, otherwise the inferred
  `alanzchen/colab-cli` raw URL
- local `cloudflared` discovered from PATH

`--setup-only` performs Colab setup and writes SSH config, but does not open an
interactive SSH session. Any arguments after `--` are passed to `ssh <alias>`.

## Architecture

Create `src/colab_cli/ssh.py` for SSH-specific behavior:

- build the Colab setup cell that downloads the public bootstrap script and
  calls `setup(public_key=..., workspace=...)`;
- create or reuse a local Ed25519 keypair;
- call Colab tools in order: `get_cells`, `add_code_cell`, `run_code_cell`;
- parse a machine-readable `COLAB_CLI_SSH_JSON=...` line from notebook output;
- write/update a managed `Host <alias>` block in `~/.ssh/config`;
- launch `ssh <alias>` unless `--setup-only` is set.

Add `scripts/colab_ssh_bootstrap.py` as the public Colab-side bootstrap script.
It installs OpenSSH server if needed, configures root key login, installs the
provided public key, starts SSHD, downloads `cloudflared`, starts a quick tunnel
to the effective SSHD port, extracts the generated hostname from the log, and
prints a single JSON result line.

`cli.py` should stay thin: parse `ssh` options, create the runtime client, call
the SSH workflow, print setup details, and return the SSH subprocess exit code.

## Error Handling

- If no bridge is running, reuse the existing runtime error telling the user to
  start `colab-cli connect`.
- If local `cloudflared` is missing, fail before touching Colab and print an
  install hint.
- If required Colab tools are missing or setup output lacks the JSON marker,
  fail with the captured setup output summarized.
- If SSH config update fails, do not launch SSH.
- If the final SSH command fails, return the SSH process exit code.

## Testing

Use test-first coverage for:

- parser support for `ssh`, `--setup-only`, `--alias`, `--workspace`,
  `--bootstrap-url`, and trailing `--` command arguments;
- default bootstrap URL inference and environment override;
- setup cell generation includes the public key, workspace, and bootstrap URL;
- setup output parser extracts the JSON marker from stream and display-style
  outputs;
- SSH config block insertion and replacement;
- CLI `ssh --setup-only` calling the workflow and printing the alias/hostname;
- CLI returning the SSH subprocess code when it launches SSH.

Manual verification should run:

```bash
colab-cli connect --replace
colab-cli ssh --setup-only
ssh colab-ssh 'echo alive && hostname && pwd'
```
