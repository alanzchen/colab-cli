import json
import os
from pathlib import Path
import platform
import re
import subprocess
import time
import urllib.request


SSH_PORT = 2222
SETUP_JSON_PREFIX = "COLAB_CLI_SSH_JSON="
CLOUDFLARED_DIR = Path("/content/.colab-cli")
CLOUDFLARED_PID = Path("/tmp/colab_cli_cloudflared.pid")
CLOUDFLARED_LOG = Path("/tmp/colab_cli_cloudflared.log")
SSHD_CONFIG = Path("/etc/ssh/sshd_config")
SSHD_DROP_IN = Path("/etc/ssh/sshd_config.d/00-colab-cli.conf")


def run(command, *, check=True):
    env = os.environ.copy()
    env["DEBIAN_FRONTEND"] = "noninteractive"
    return subprocess.run(command, check=check, env=env, text=True)


def ensure_openssh_server():
    if Path("/usr/sbin/sshd").exists():
        return
    run(["apt-get", "update", "-qq"])
    run(["apt-get", "install", "-y", "-qq", "openssh-server"])


def _without_active_settings(text, names):
    pattern = re.compile(
        r"^\s*(" + "|".join(re.escape(name) for name in names) + r")\b",
        re.IGNORECASE,
    )
    kept = []
    for line in text.splitlines():
        if pattern.match(line) and not line.lstrip().startswith("#"):
            continue
        kept.append(line)
    return "\n".join(kept).rstrip() + "\n"


def configure_sshd(port=SSH_PORT):
    names = [
        "Port",
        "PermitRootLogin",
        "PasswordAuthentication",
        "PubkeyAuthentication",
        "KbdInteractiveAuthentication",
        "ChallengeResponseAuthentication",
        "AuthorizedKeysFile",
    ]
    if SSHD_CONFIG.exists():
        SSHD_CONFIG.write_text(
            _without_active_settings(SSHD_CONFIG.read_text(encoding="utf-8"), names),
            encoding="utf-8",
        )
    SSHD_DROP_IN.parent.mkdir(parents=True, exist_ok=True)
    SSHD_DROP_IN.write_text(
        "\n".join(
            [
                f"Port {port}",
                "PermitRootLogin yes",
                "PasswordAuthentication no",
                "PubkeyAuthentication yes",
                "KbdInteractiveAuthentication no",
                "ChallengeResponseAuthentication no",
                "AuthorizedKeysFile .ssh/authorized_keys",
                "",
            ]
        ),
        encoding="utf-8",
    )


def install_public_key(public_key):
    key = public_key.strip()
    if not key.startswith(("ssh-ed25519 ", "ssh-rsa ", "ecdsa-sha2-")):
        raise ValueError("public_key must be an OpenSSH public key")

    ssh_dir = Path("/root/.ssh")
    authorized_keys = ssh_dir / "authorized_keys"
    ssh_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    existing = ""
    if authorized_keys.exists():
        existing = authorized_keys.read_text(encoding="utf-8")
    if key not in existing.splitlines():
        with authorized_keys.open("a", encoding="utf-8") as handle:
            handle.write(key + "\n")
    ssh_dir.chmod(0o700)
    authorized_keys.chmod(0o600)


def start_sshd():
    Path("/run/sshd").mkdir(parents=True, exist_ok=True)
    Path("/var/run/sshd").mkdir(parents=True, exist_ok=True)
    run(["ssh-keygen", "-A"])
    run(["/usr/sbin/sshd", "-t"])
    restarted = subprocess.run(["service", "ssh", "restart"], text=True)
    if restarted.returncode == 0:
        return
    subprocess.run(["pkill", "-x", "sshd"], check=False)
    run(["/usr/sbin/sshd"])


def _cloudflared_asset_name():
    machine = platform.machine().lower()
    if machine in {"x86_64", "amd64"}:
        return "cloudflared-linux-amd64"
    if machine in {"aarch64", "arm64"}:
        return "cloudflared-linux-arm64"
    raise RuntimeError(f"unsupported machine for cloudflared: {machine}")


def ensure_cloudflared():
    CLOUDFLARED_DIR.mkdir(parents=True, exist_ok=True)
    binary = CLOUDFLARED_DIR / "cloudflared"
    if binary.exists() and os.access(binary, os.X_OK):
        return binary

    url = (
        "https://github.com/cloudflare/cloudflared/releases/latest/download/"
        + _cloudflared_asset_name()
    )
    urllib.request.urlretrieve(url, binary)
    binary.chmod(0o755)
    return binary


def _stop_existing_tunnel():
    if not CLOUDFLARED_PID.exists():
        return
    try:
        pid = int(CLOUDFLARED_PID.read_text(encoding="utf-8").strip())
    except ValueError:
        CLOUDFLARED_PID.unlink(missing_ok=True)
        return

    try:
        os.kill(pid, 15)
    except ProcessLookupError:
        CLOUDFLARED_PID.unlink(missing_ok=True)
        return

    deadline = time.time() + 5
    while time.time() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.2)
    else:
        try:
            os.kill(pid, 9)
        except ProcessLookupError:
            pass
    CLOUDFLARED_PID.unlink(missing_ok=True)


def _read_tunnel_hostname():
    if not CLOUDFLARED_LOG.exists():
        return None
    text = CLOUDFLARED_LOG.read_text(encoding="utf-8", errors="replace")
    match = re.search(r"https://([A-Za-z0-9-]+\.trycloudflare\.com)", text)
    if match:
        return match.group(1)
    return None


def start_cloudflared_tunnel(cloudflared_path, port=SSH_PORT, timeout=60):
    _stop_existing_tunnel()
    CLOUDFLARED_LOG.unlink(missing_ok=True)
    log_handle = CLOUDFLARED_LOG.open("w", encoding="utf-8")
    process = subprocess.Popen(
        [
            str(cloudflared_path),
            "tunnel",
            "--no-autoupdate",
            "--url",
            f"ssh://localhost:{port}",
        ],
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        text=True,
    )
    log_handle.close()
    CLOUDFLARED_PID.write_text(str(process.pid), encoding="utf-8")

    deadline = time.time() + timeout
    while time.time() < deadline:
        hostname = _read_tunnel_hostname()
        if hostname:
            return hostname
        if process.poll() is not None:
            break
        time.sleep(0.5)

    log = ""
    if CLOUDFLARED_LOG.exists():
        log = CLOUDFLARED_LOG.read_text(encoding="utf-8", errors="replace")
    raise RuntimeError("cloudflared did not publish a tunnel hostname\n" + log)


def setup(public_key, workspace="/content/workspace", port=SSH_PORT):
    workspace_path = Path(workspace)
    workspace_path.mkdir(parents=True, exist_ok=True)

    ensure_openssh_server()
    configure_sshd(port)
    install_public_key(public_key)
    start_sshd()
    cloudflared_path = ensure_cloudflared()
    hostname = start_cloudflared_tunnel(cloudflared_path, port)

    payload = {
        "hostname": hostname,
        "port": port,
        "user": "root",
        "workspace": str(workspace_path),
    }
    print(SETUP_JSON_PREFIX + json.dumps(payload, sort_keys=True))
    return payload
