from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import subprocess
from typing import Any


DEFAULT_ALIAS = "colab-ssh"
DEFAULT_WORKSPACE = "/content/workspace"
DEFAULT_BOOTSTRAP_URL = (
    "https://raw.githubusercontent.com/alanzchen/colab-cli/main/"
    "scripts/colab_ssh_bootstrap.py"
)
BOOTSTRAP_URL_ENV = "COLAB_CLI_SSH_BOOTSTRAP_URL"
SETUP_JSON_PREFIX = "COLAB_CLI_SSH_JSON="


class ColabSshError(RuntimeError):
    pass


@dataclass(frozen=True)
class SshSetupInfo:
    alias: str
    hostname: str
    user: str
    workspace: str


def default_key_path() -> Path:
    return Path.home() / ".ssh" / "colab_cli_ed25519"


def default_config_path() -> Path:
    return Path.home() / ".ssh" / "config"


def default_known_hosts_path() -> Path:
    return Path.home() / ".cache" / "colab-cli" / "ssh_known_hosts"


def default_bootstrap_url() -> str:
    return os.environ.get(BOOTSTRAP_URL_ENV, DEFAULT_BOOTSTRAP_URL)


def build_setup_cell(*, bootstrap_url: str, public_key: str, workspace: str) -> str:
    return "\n".join(
        [
            "import urllib.request",
            "namespace = {}",
            f"bootstrap_url = {json.dumps(bootstrap_url)}",
            "source = urllib.request.urlopen(bootstrap_url).read().decode('utf-8')",
            "exec(compile(source, bootstrap_url, 'exec'), namespace)",
            "namespace['setup'](",
            f"    public_key={json.dumps(public_key)},",
            f"    workspace={json.dumps(workspace)},",
            ")",
        ]
    )


def _iter_output_text(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
        return
    if isinstance(value, Mapping):
        for key in ("text", "content"):
            item = value.get(key)
            if item is not None:
                yield from _iter_output_text(item)
        outputs = value.get("outputs")
        if outputs is not None:
            yield from _iter_output_text(outputs)
        structured = value.get("structured_content")
        if structured is not None:
            yield from _iter_output_text(structured)
        data = value.get("data")
        if data is not None:
            yield from _iter_output_text(data)
        content = value.get("content")
        if content is not None:
            yield from _iter_output_text(content)
        return
    if isinstance(value, Iterable):
        for item in value:
            yield from _iter_output_text(item)


def parse_setup_result(result: Any) -> dict[str, Any]:
    for text in _iter_output_text(result):
        for line in text.splitlines():
            line = line.strip()
            if line.startswith(SETUP_JSON_PREFIX):
                payload = line[len(SETUP_JSON_PREFIX) :]
                return json.loads(payload)
    raise ColabSshError("Colab SSH setup did not return a setup JSON marker")


def find_cloudflared() -> str:
    path = shutil.which("cloudflared")
    if path is None:
        raise ColabSshError(
            "cloudflared is required for SSH. Install it with "
            "`brew install cloudflared` or pass a cloudflared path."
        )
    return path


def ensure_keypair(
    key_path: Path,
    *,
    subprocess_run: Callable[..., subprocess.CompletedProcess[Any]] = subprocess.run,
) -> str:
    public_key_path = Path(f"{key_path}.pub")
    if key_path.exists() and public_key_path.exists():
        return public_key_path.read_text(encoding="utf-8").strip()

    key_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess_run(
        [
            "ssh-keygen",
            "-t",
            "ed25519",
            "-N",
            "",
            "-f",
            str(key_path),
            "-C",
            "colab-cli",
        ],
        check=True,
    )
    return public_key_path.read_text(encoding="utf-8").strip()


def write_ssh_config(
    *,
    config_path: Path,
    alias: str,
    hostname: str,
    identity_file: Path,
    cloudflared_path: str,
    known_hosts_file: Path,
) -> None:
    config_path.parent.mkdir(parents=True, exist_ok=True)
    known_hosts_file.parent.mkdir(parents=True, exist_ok=True)
    begin = f"# BEGIN COLAB-CLI SSH {alias}"
    end = f"# END COLAB-CLI SSH {alias}"
    block = "\n".join(
        [
            begin,
            f"Host {alias}",
            f"\tHostName {hostname}",
            "\tUser root",
            f"\tIdentityFile {identity_file}",
            "\tIdentitiesOnly yes",
            f"\tUserKnownHostsFile {known_hosts_file}",
            "\tStrictHostKeyChecking no",
            f"\tProxyCommand {cloudflared_path} access ssh --hostname %h",
            end,
            "",
        ]
    )

    existing = ""
    if config_path.exists():
        existing = config_path.read_text(encoding="utf-8")

    start = existing.find(begin)
    finish = existing.find(end)
    if start != -1 and finish != -1 and finish >= start:
        finish += len(end)
        while finish < len(existing) and existing[finish] in "\r\n":
            finish += 1
        updated = existing[:start].rstrip() + "\n\n" + block + existing[finish:]
    else:
        updated = existing.rstrip() + "\n\n" + block if existing.strip() else block

    config_path.write_text(updated, encoding="utf-8")
