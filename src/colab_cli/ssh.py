from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import subprocess
from typing import Any


DEFAULT_ALIAS = "colab-ssh"
DEFAULT_WORKSPACE = "/content/workspace"
DEFAULT_WORKER_HOSTNAME_PREFIX = "colab-worker"
DEFAULT_SSH_PORT = 2222
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


def build_worker_setup_cell(
    *,
    bootstrap_url: str,
    public_key: str,
    workspace: str,
    tailscale_auth_key: str = "",
    hostname_prefix: str = DEFAULT_WORKER_HOSTNAME_PREFIX,
    start_cloudflare: bool = True,
    port: int = DEFAULT_SSH_PORT,
) -> str:
    return "\n".join(
        [
            "import urllib.request",
            "namespace = {}",
            f"bootstrap_url = {json.dumps(bootstrap_url)}",
            "source = urllib.request.urlopen(bootstrap_url).read().decode('utf-8')",
            "exec(compile(source, bootstrap_url, 'exec'), namespace)",
            "namespace['setup_worker'](",
            f"    public_key={json.dumps(public_key)},",
            f"    workspace={json.dumps(workspace)},",
            f"    port={int(port)},",
            f"    hostname_prefix={json.dumps(hostname_prefix)},",
            f"    tailscale_auth_key={json.dumps(tailscale_auth_key)},",
            f"    start_cloudflare={bool(start_cloudflare)!r},",
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


def _cell_count(cells_result: Any) -> int:
    if isinstance(cells_result, Mapping):
        structured = cells_result.get("structured_content")
        if isinstance(structured, Mapping):
            cells = structured.get("cells")
            if isinstance(cells, list):
                return len(cells)
        data = cells_result.get("data")
        if isinstance(data, Mapping):
            cells = data.get("cells")
            if isinstance(cells, list):
                return len(cells)
    return 0


def _new_cell_id(add_result: Any) -> str:
    if isinstance(add_result, Mapping):
        for key in ("structured_content", "data"):
            value = add_result.get(key)
            if isinstance(value, Mapping) and value.get("newCellId"):
                return str(value["newCellId"])
    raise ColabSshError("Colab did not return a new setup cell id")


class ColabSshManager:
    def __init__(
        self,
        *,
        client: Any,
        alias: str = DEFAULT_ALIAS,
        workspace: str = DEFAULT_WORKSPACE,
        bootstrap_url: str | None = None,
        key_path: Path | None = None,
        config_path: Path | None = None,
        known_hosts_path: Path | None = None,
        cloudflared_path: str | None = None,
        subprocess_run: Callable[..., subprocess.CompletedProcess[Any]] = subprocess.run,
    ):
        self.client = client
        self.alias = alias
        self.workspace = workspace
        self.bootstrap_url = bootstrap_url or default_bootstrap_url()
        self.key_path = key_path or default_key_path()
        self.config_path = config_path or default_config_path()
        self.known_hosts_path = known_hosts_path or default_known_hosts_path()
        self.cloudflared_path = cloudflared_path
        self.subprocess_run = subprocess_run
        self.last_setup_info: SshSetupInfo | None = None

    async def setup(self, *, timeout: float, setup_timeout: float) -> SshSetupInfo:
        cloudflared_path = self.cloudflared_path or find_cloudflared()
        public_key = ensure_keypair(
            self.key_path,
            subprocess_run=self.subprocess_run,
        )
        setup_code = build_setup_cell(
            bootstrap_url=self.bootstrap_url,
            public_key=public_key,
            workspace=self.workspace,
        )
        cells_result = await self.client.call_tool("get_cells", {}, timeout=timeout)
        add_result = await self.client.call_tool(
            "add_code_cell",
            {
                "cellIndex": _cell_count(cells_result),
                "language": "python",
                "code": setup_code,
            },
            timeout=timeout,
        )
        cell_id = _new_cell_id(add_result)
        run_result = await self.client.call_tool(
            "run_code_cell",
            {"cellId": cell_id},
            timeout=setup_timeout,
        )
        payload = parse_setup_result(run_result)
        info = SshSetupInfo(
            alias=self.alias,
            hostname=str(payload["hostname"]),
            user=str(payload.get("user", "root")),
            workspace=str(payload.get("workspace", self.workspace)),
        )
        write_ssh_config(
            config_path=self.config_path,
            alias=self.alias,
            hostname=info.hostname,
            identity_file=self.key_path,
            cloudflared_path=cloudflared_path,
            known_hosts_file=self.known_hosts_path,
        )
        self.last_setup_info = info
        return info

    def connect(self, ssh_args: Sequence[str]) -> int:
        completed = self.subprocess_run(["ssh", self.alias, *ssh_args])
        return int(completed.returncode)

    async def setup_and_maybe_connect(
        self,
        *,
        setup_only: bool,
        ssh_args: Sequence[str],
        timeout: float,
        setup_timeout: float,
    ) -> int:
        await self.setup(timeout=timeout, setup_timeout=setup_timeout)
        if setup_only:
            return 0
        return self.connect(ssh_args)
