import json
import os
from pathlib import Path
import platform
import re
import shutil
import socket
import subprocess
import time
import urllib.request


SSH_PORT = 2222
SETUP_JSON_PREFIX = "COLAB_CLI_SSH_JSON="
WORKER_JSON_PREFIX = "COLAB_CLI_WORKER_JSON="
CLOUDFLARED_DIR = Path("/content/.colab-cli")
CLOUDFLARED_PID = Path("/tmp/colab_cli_cloudflared.pid")
CLOUDFLARED_LOG = Path("/tmp/colab_cli_cloudflared.log")
SSHD_PID = Path("/tmp/colab_cli_sshd.pid")
SSHD_CONFIG = Path("/etc/ssh/sshd_config")
SSHD_DROP_IN = Path("/etc/ssh/sshd_config.d/00-colab-cli.conf")
TAILSCALED_PID = Path("/tmp/colab_cli_tailscaled.pid")
TAILSCALED_LOG = Path("/tmp/colab_cli_tailscaled.log")


def run(command, *, check=True):
    env = os.environ.copy()
    env["DEBIAN_FRONTEND"] = "noninteractive"
    return subprocess.run(command, check=check, env=env, text=True)


def ensure_openssh_server():
    if Path("/usr/sbin/sshd").exists():
        return
    run(["apt-get", "update", "-qq"])
    run(["apt-get", "install", "-y", "-qq", "openssh-server"])


def ensure_worker_packages():
    if (
        Path("/usr/sbin/sshd").exists()
        and shutil.which("curl")
        and shutil.which("rsync")
    ):
        return
    run(["apt-get", "update", "-qq"])
    run(
        [
            "apt-get",
            "install",
            "-y",
            "-qq",
            "openssh-server",
            "curl",
            "ca-certificates",
            "rsync",
        ]
    )


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
                "ListenAddress 127.0.0.1",
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


def _sshd_options(port):
    return [
        "-p",
        str(port),
        "-o",
        "ListenAddress=127.0.0.1",
        "-o",
        "PermitRootLogin=yes",
        "-o",
        "PasswordAuthentication=no",
        "-o",
        "PubkeyAuthentication=yes",
        "-o",
        "KbdInteractiveAuthentication=no",
        "-o",
        "ChallengeResponseAuthentication=no",
        "-o",
        "AuthorizedKeysFile=/root/.ssh/authorized_keys",
        "-o",
        f"PidFile={SSHD_PID}",
    ]


def _stop_existing_sshd(port):
    if SSHD_PID.exists():
        try:
            pid = int(SSHD_PID.read_text(encoding="utf-8").strip())
        except ValueError:
            SSHD_PID.unlink(missing_ok=True)
        else:
            try:
                os.kill(pid, 15)
            except ProcessLookupError:
                pass
            SSHD_PID.unlink(missing_ok=True)
    subprocess.run(["pkill", "-f", f"sshd.*-p {port}"], check=False)


def _wait_for_sshd(port, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return
        except OSError:
            time.sleep(0.2)
    raise RuntimeError(f"sshd did not start on 127.0.0.1:{port}")


def start_sshd(port=SSH_PORT):
    Path("/run/sshd").mkdir(parents=True, exist_ok=True)
    Path("/var/run/sshd").mkdir(parents=True, exist_ok=True)
    run(["ssh-keygen", "-A"])
    _stop_existing_sshd(port)
    options = _sshd_options(port)
    run(["/usr/sbin/sshd", "-t", *options])
    run(["/usr/sbin/sshd", *options])
    _wait_for_sshd(port)


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


def ensure_tailscale():
    if Path("/usr/bin/tailscale").exists():
        return
    run(["bash", "-lc", "curl -fsSL https://tailscale.com/install.sh | sh"])


def _stop_existing_tailscaled():
    pid = None
    if TAILSCALED_PID.exists():
        try:
            pid = int(TAILSCALED_PID.read_text(encoding="utf-8").strip())
        except ValueError:
            TAILSCALED_PID.unlink(missing_ok=True)
        else:
            try:
                os.kill(pid, 15)
            except ProcessLookupError:
                pid = None
            TAILSCALED_PID.unlink(missing_ok=True)
    subprocess.run(["pkill", "-x", "tailscaled"], check=False)
    if pid is None:
        return

    deadline = time.time() + 5
    while time.time() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.2)


def start_tailscaled():
    _stop_existing_tailscaled()
    Path("/var/run/tailscale").mkdir(parents=True, exist_ok=True)
    log_handle = TAILSCALED_LOG.open("w", encoding="utf-8")
    process = subprocess.Popen(
        [
            "tailscaled",
            "--state=mem:",
            "--tun=userspace-networking",
            "--socks5-server=127.0.0.1:1055",
            "--outbound-http-proxy-listen=127.0.0.1:1055",
        ],
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        text=True,
    )
    log_handle.close()
    TAILSCALED_PID.write_text(str(process.pid), encoding="utf-8")
    time.sleep(3)


def tailscale_up(hostname, auth_key=""):
    command = ["tailscale", "up", f"--hostname={hostname}", "--accept-dns=false"]
    if auth_key.strip():
        command.append("--auth-key=" + auth_key.strip())
    run(command)


def start_tailscale_serve(port=SSH_PORT):
    result = run(
        [
            "tailscale",
            "serve",
            "--bg",
            f"--tcp={port}",
            f"tcp://127.0.0.1:{port}",
        ],
        check=False,
    )
    return result.returncode == 0


def tailscale_ip():
    for _ in range(5):
        try:
            output = subprocess.check_output(["tailscale", "ip", "-4"], text=True)
        except subprocess.CalledProcessError:
            output = ""
        for line in output.splitlines():
            value = line.strip()
            if value:
                return value
        time.sleep(1)
    raise RuntimeError("tailscale did not report an IPv4 address")


def _worker_hostname(prefix):
    safe_prefix = re.sub(r"[^A-Za-z0-9-]+", "-", prefix).strip("-")
    if not safe_prefix:
        safe_prefix = "colab-worker"
    return f"{safe_prefix}-{int(time.time())}"


def _print_worker_ready(payload, port):
    print(WORKER_JSON_PREFIX + json.dumps(payload, sort_keys=True))
    print("\nREADY")
    print(f"HOSTNAME={payload['hostname']}")
    print(f"COLAB_TAILSCALE_IP={payload['tailscale_ip']}")
    if payload.get("cloudflare_url"):
        print(f"CLOUDFLARE_HOST={payload['cloudflare_url']}")

    print("\nLocal Tailscale SSH test:")
    print(
        "ssh -i ~/.ssh/colab_cli_ed25519 "
        f"-p {port} -o StrictHostKeyChecking=no "
        f"root@{payload['tailscale_ip']} 'hostname && pwd'"
    )

    print("\nLocal sshfs mount:")
    print("mkdir -p /tmp/colabfs")
    print(
        "sshfs "
        f"-p {port} "
        "-o IdentityFile=~/.ssh/colab_cli_ed25519,"
        "reconnect,ServerAliveInterval=15,ServerAliveCountMax=3 "
        f"root@{payload['tailscale_ip']}:/content /tmp/colabfs"
    )

    if payload.get("cloudflare_url"):
        print("\nCloudflare SSH host for bulk upload:")
        print(payload["cloudflare_url"])


def setup(public_key, workspace="/content/workspace", port=SSH_PORT):
    workspace_path = Path(workspace)
    workspace_path.mkdir(parents=True, exist_ok=True)

    ensure_openssh_server()
    configure_sshd(port)
    install_public_key(public_key)
    start_sshd(port)
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


def setup_worker(
    public_key,
    workspace="/content/workspace",
    port=SSH_PORT,
    hostname_prefix="colab-worker",
    tailscale_auth_key="",
    start_cloudflare=True,
):
    workspace_path = Path(workspace)
    workspace_path.mkdir(parents=True, exist_ok=True)

    ensure_worker_packages()
    configure_sshd(port)
    install_public_key(public_key)
    start_sshd(port)

    hostname = _worker_hostname(hostname_prefix)
    ensure_tailscale()
    start_tailscaled()
    tailscale_up(hostname, tailscale_auth_key)
    serve_ok = start_tailscale_serve(port)
    ts_ip = tailscale_ip()

    cloudflare_host = ""
    if start_cloudflare:
        cloudflared_path = ensure_cloudflared()
        cloudflare_host = start_cloudflared_tunnel(cloudflared_path, port)

    payload = {
        "hostname": hostname,
        "port": port,
        "user": "root",
        "workspace": str(workspace_path),
        "tailscale_ip": ts_ip,
        "tailscale_serve": serve_ok,
        "cloudflare_host": cloudflare_host,
        "cloudflare_url": f"https://{cloudflare_host}" if cloudflare_host else "",
    }

    if not serve_ok:
        print(
            "WARN: tailscale serve failed; direct Tailscale SSH may not work "
            "in userspace mode."
        )
    _print_worker_ready(payload, port)
    return payload
