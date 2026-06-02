from pathlib import Path

from colab_cli import ssh


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
