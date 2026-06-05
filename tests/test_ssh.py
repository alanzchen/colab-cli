from pathlib import Path
import runpy
from subprocess import CompletedProcess

from colab_cli import ssh

ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP_SCRIPT = ROOT / "scripts" / "colab_ssh_bootstrap.py"


class FakeRuntimeClient:
    def __init__(self):
        self.calls = []

    async def call_tool(self, name, arguments, *, timeout):
        self.calls.append((name, arguments, timeout))
        if name == "get_cells":
            return {"structured_content": {"cells": [{"id": "first"}]}}
        if name == "add_code_cell":
            return {"structured_content": {"newCellId": "setup-cell"}}
        if name == "run_code_cell":
            return {
                "structured_content": {
                    "outputs": [
                        {
                            "output_type": "stream",
                            "text": [
                                (
                                    'COLAB_CLI_SSH_JSON={"hostname":'
                                    '"x.trycloudflare.com","user":"root",'
                                    '"workspace":"/content/work"}\n'
                                )
                            ],
                        }
                    ]
                }
            }
        raise AssertionError(f"unexpected tool: {name}")


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


def test_build_worker_setup_cell_calls_worker_entrypoint():
    code = ssh.build_worker_setup_cell(
        bootstrap_url="https://example/setup.py",
        public_key="ssh-ed25519 AAAA test",
        workspace="/content/work",
        tailscale_auth_key="tskey-test",
        hostname_prefix="worker",
        start_cloudflare=False,
        port=2244,
    )

    assert "https://example/setup.py" in code
    assert "namespace['setup_worker'](" in code
    assert "ssh-ed25519 AAAA test" in code
    assert "tskey-test" in code
    assert "hostname_prefix=\"worker\"" in code
    assert "start_cloudflare=False" in code
    assert "port=2244" in code


def test_bootstrap_script_is_public_setup_contract():
    text = BOOTSTRAP_SCRIPT.read_text(encoding="utf-8")

    assert "def setup(" in text
    assert "def setup_worker(" in text
    assert "COLAB_CLI_SSH_JSON=" in text
    assert "COLAB_CLI_WORKER_JSON=" in text
    assert "colab_ssh" not in text


def test_bootstrap_setup_prints_marker_when_helpers_are_stubbed(
    monkeypatch, capsys, tmp_path
):
    namespace = runpy.run_path(str(BOOTSTRAP_SCRIPT))
    globals_ = namespace["setup"].__globals__

    monkeypatch.setitem(globals_, "ensure_openssh_server", lambda: None)
    monkeypatch.setitem(globals_, "configure_sshd", lambda port: None)
    monkeypatch.setitem(globals_, "install_public_key", lambda public_key: None)
    monkeypatch.setitem(globals_, "start_sshd", lambda port: None)
    monkeypatch.setitem(globals_, "ensure_cloudflared", lambda: tmp_path / "cloudflared")
    monkeypatch.setitem(
        globals_,
        "start_cloudflared_tunnel",
        lambda cloudflared_path, port: "x.trycloudflare.com",
    )

    namespace["setup"](
        public_key="ssh-ed25519 AAAA test",
        workspace=str(tmp_path / "workspace"),
    )

    out = capsys.readouterr().out
    assert "COLAB_CLI_SSH_JSON=" in out
    assert '"hostname": "x.trycloudflare.com"' in out
    assert '"user": "root"' in out
    assert f'"workspace": "{tmp_path / "workspace"}"' in out


def test_bootstrap_setup_uses_same_port_for_sshd_and_tunnel(monkeypatch, tmp_path):
    namespace = runpy.run_path(str(BOOTSTRAP_SCRIPT))
    globals_ = namespace["setup"].__globals__
    calls = []

    monkeypatch.setitem(globals_, "ensure_openssh_server", lambda: None)
    monkeypatch.setitem(globals_, "configure_sshd", lambda port: None)
    monkeypatch.setitem(globals_, "install_public_key", lambda public_key: None)
    monkeypatch.setitem(globals_, "ensure_cloudflared", lambda: tmp_path / "cloudflared")
    monkeypatch.setitem(
        globals_,
        "start_sshd",
        lambda port: calls.append(("sshd", port)),
    )
    monkeypatch.setitem(
        globals_,
        "start_cloudflared_tunnel",
        lambda cloudflared_path, port: calls.append(("tunnel", port))
        or "x.trycloudflare.com",
    )

    namespace["setup"](
        public_key="ssh-ed25519 AAAA test",
        workspace=str(tmp_path / "workspace"),
        port=2244,
    )

    assert calls == [("sshd", 2244), ("tunnel", 2244)]


def test_bootstrap_setup_worker_prints_ready_block_when_helpers_are_stubbed(
    monkeypatch, capsys, tmp_path
):
    namespace = runpy.run_path(str(BOOTSTRAP_SCRIPT))
    globals_ = namespace["setup_worker"].__globals__

    monkeypatch.setitem(globals_, "ensure_worker_packages", lambda: None)
    monkeypatch.setitem(globals_, "configure_sshd", lambda port: None)
    monkeypatch.setitem(globals_, "install_public_key", lambda public_key: None)
    monkeypatch.setitem(globals_, "start_sshd", lambda port: None)
    monkeypatch.setitem(globals_, "ensure_tailscale", lambda: None)
    monkeypatch.setitem(globals_, "start_tailscaled", lambda: None)
    monkeypatch.setitem(globals_, "tailscale_up", lambda hostname, auth_key: None)
    monkeypatch.setitem(globals_, "start_tailscale_serve", lambda port: True)
    monkeypatch.setitem(globals_, "tailscale_ip", lambda: "100.64.0.1")
    monkeypatch.setitem(globals_, "ensure_cloudflared", lambda: tmp_path / "cloudflared")
    monkeypatch.setitem(
        globals_,
        "start_cloudflared_tunnel",
        lambda cloudflared_path, port: "x.trycloudflare.com",
    )
    monkeypatch.setitem(globals_, "_worker_hostname", lambda prefix: "worker-123")

    payload = namespace["setup_worker"](
        public_key="ssh-ed25519 AAAA test",
        workspace=str(tmp_path / "workspace"),
        port=2244,
        hostname_prefix="worker",
        tailscale_auth_key="tskey-test",
    )

    out = capsys.readouterr().out
    assert payload["hostname"] == "worker-123"
    assert payload["tailscale_ip"] == "100.64.0.1"
    assert payload["cloudflare_url"] == "https://x.trycloudflare.com"
    assert "COLAB_CLI_WORKER_JSON=" in out
    assert "READY" in out
    assert "HOSTNAME=worker-123" in out
    assert "COLAB_TAILSCALE_IP=100.64.0.1" in out
    assert "CLOUDFLARE_HOST=https://x.trycloudflare.com" in out


def test_parse_setup_result_extracts_json_marker():
    result = {
        "structured_content": {
            "outputs": [
                {
                    "output_type": "stream",
                    "text": [
                        "noise\n",
                        (
                            'COLAB_CLI_SSH_JSON={"hostname":"x.trycloudflare.com",'
                            '"user":"root","workspace":"/content/work"}\n'
                        ),
                    ],
                }
            ]
        }
    }

    assert ssh.parse_setup_result(result)["hostname"] == "x.trycloudflare.com"


def test_parse_setup_result_reads_text_from_content_fallback():
    result = {
        "content": [
            {
                "type": "text",
                "text": (
                    'COLAB_CLI_SSH_JSON={"hostname":"x.trycloudflare.com",'
                    '"user":"root","workspace":"/content/work"}'
                ),
            }
        ]
    }

    assert ssh.parse_setup_result(result)["workspace"] == "/content/work"


def test_write_ssh_config_replaces_managed_block(tmp_path):
    config = tmp_path / "config"
    key_path = tmp_path / "key"
    known_hosts = tmp_path / "known_hosts"
    config.write_text(
        "Host old\n"
        "\tHostName old\n\n"
        "# BEGIN COLAB-CLI SSH colab-ssh\n"
        "Host colab-ssh\n"
        "\tHostName stale.trycloudflare.com\n"
        "# END COLAB-CLI SSH colab-ssh\n",
        encoding="utf-8",
    )

    ssh.write_ssh_config(
        config_path=config,
        alias="colab-ssh",
        hostname="x.trycloudflare.com",
        identity_file=key_path,
        cloudflared_path="/opt/homebrew/bin/cloudflared",
        known_hosts_file=known_hosts,
    )

    text = config.read_text(encoding="utf-8")
    assert "Host old" in text
    assert "Host colab-ssh" in text
    assert "x.trycloudflare.com" in text
    assert "stale.trycloudflare.com" not in text
    assert "BEGIN COLAB-CLI SSH colab-ssh" in text
    assert "ProxyCommand /opt/homebrew/bin/cloudflared access ssh --hostname %h" in text
    assert f"IdentityFile {key_path}" in text
    assert f"UserKnownHostsFile {known_hosts}" in text


def test_ensure_keypair_reuses_existing_key(tmp_path):
    key_path = tmp_path / "key"
    pub_path = Path(f"{key_path}.pub")
    key_path.write_text("private", encoding="utf-8")
    pub_path.write_text("ssh-ed25519 AAAA test\n", encoding="utf-8")
    calls = []

    public_key = ssh.ensure_keypair(
        key_path,
        subprocess_run=lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    assert public_key == "ssh-ed25519 AAAA test"
    assert calls == []


async def test_setup_ssh_calls_colab_tools_and_returns_info(tmp_path):
    key_path = tmp_path / "key"
    Path(f"{key_path}.pub").write_text("ssh-ed25519 AAAA test\n", encoding="utf-8")
    key_path.write_text("private", encoding="utf-8")
    client = FakeRuntimeClient()
    manager = ssh.ColabSshManager(
        client=client,
        alias="colab-ssh",
        workspace="/content/work",
        bootstrap_url="https://example/setup.py",
        key_path=key_path,
        config_path=tmp_path / "config",
        known_hosts_path=tmp_path / "known_hosts",
        cloudflared_path="/opt/homebrew/bin/cloudflared",
        subprocess_run=lambda args, **kwargs: CompletedProcess(args, 0),
    )

    info = await manager.setup(timeout=7, setup_timeout=11)

    assert client.calls[0] == ("get_cells", {}, 7)
    assert client.calls[1][0] == "add_code_cell"
    assert client.calls[1][1]["cellIndex"] == 1
    assert client.calls[1][1]["language"] == "python"
    assert "https://example/setup.py" in client.calls[1][1]["code"]
    assert client.calls[2] == ("run_code_cell", {"cellId": "setup-cell"}, 11)
    assert info.hostname == "x.trycloudflare.com"
    assert info.alias == "colab-ssh"
    assert "x.trycloudflare.com" in (tmp_path / "config").read_text(encoding="utf-8")


def test_connect_runs_ssh_alias_with_extra_args(tmp_path):
    calls = []
    manager = ssh.ColabSshManager(
        client=FakeRuntimeClient(),
        alias="colab-ssh",
        workspace="/content/work",
        bootstrap_url="https://example/setup.py",
        key_path=tmp_path / "key",
        config_path=tmp_path / "config",
        known_hosts_path=tmp_path / "known_hosts",
        cloudflared_path="/opt/homebrew/bin/cloudflared",
        subprocess_run=lambda args, **kwargs: calls.append(args)
        or CompletedProcess(args, 23),
    )

    assert manager.connect(["whoami"]) == 23
    assert calls == [["ssh", "colab-ssh", "whoami"]]
