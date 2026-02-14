import os
import socket
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

import paramiko


REPO_ROOT = Path(__file__).resolve().parents[1]
DEPLOY_DIR = REPO_ROOT / "deploy" / "vps"


@dataclass(frozen=True)
class VPSConfig:
    host: str
    user: str
    password: str
    domain_web: str
    domain_s3: str


def _require_env(name: str) -> str:
    val = os.environ.get(name, "").strip()
    if not val:
        raise SystemExit(f"Missing env var: {name}")
    return val


def _read_file(p: Path) -> bytes:
    return p.read_bytes()


def _gen_base64(nbytes: int = 32) -> str:
    import secrets
    import base64

    return base64.b64encode(secrets.token_bytes(nbytes)).decode("ascii")


def _gen_hex(nbytes: int = 32) -> str:
    import secrets

    return secrets.token_hex(nbytes)


def _resolve_a(name: str) -> Optional[str]:
    try:
        infos = socket.getaddrinfo(name, None, family=socket.AF_INET)
    except socket.gaierror:
        return None
    if not infos:
        return None
    # Return the first A record IP we see.
    return infos[0][4][0]


class SSH:
    def __init__(self, host: str, user: str, password: str, timeout_s: int = 20):
        self.host = host
        self.user = user
        self.password = password
        self.timeout_s = timeout_s
        self.client: Optional[paramiko.SSHClient] = None

    def __enter__(self) -> "SSH":
        c = paramiko.SSHClient()
        c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        c.connect(
            self.host,
            username=self.user,
            password=self.password,
            timeout=self.timeout_s,
            banner_timeout=self.timeout_s,
            auth_timeout=self.timeout_s,
        )
        self.client = c
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.client:
            self.client.close()
            self.client = None

    def run(self, cmd: str, check: bool = True) -> Tuple[int, str, str]:
        assert self.client is not None
        stdin, stdout, stderr = self.client.exec_command(cmd)
        out = stdout.read().decode("utf-8", errors="replace")
        err = stderr.read().decode("utf-8", errors="replace")
        code = stdout.channel.recv_exit_status()
        if check and code != 0:
            raise RuntimeError(f"Command failed ({code}): {cmd}\n{err}".strip())
        return code, out, err

    def put_bytes(self, remote_path: str, content: bytes, mode: int = 0o600) -> None:
        assert self.client is not None
        sftp = self.client.open_sftp()
        try:
            tmp = f"{remote_path}.tmp-{int(time.time())}"
            with sftp.file(tmp, "wb") as f:
                f.write(content)
            sftp.chmod(tmp, mode)
            # Some SFTP servers refuse to overwrite on rename; remove destination first.
            try:
                sftp.remove(remote_path)
            except OSError:
                pass
            sftp.rename(tmp, remote_path)
        finally:
            sftp.close()


def build_env_file(cfg: VPSConfig) -> bytes:
    # Generate fresh secrets and store them server-side in /opt/langfuse/.env.
    nextauth_secret = _gen_base64(32)
    salt = _gen_base64(32)
    encryption_key = _gen_hex(32)  # 64 hex chars

    postgres_password = _gen_base64(24)
    redis_auth = _gen_base64(24)
    clickhouse_password = _gen_base64(24)
    minio_root_password = _gen_base64(24)

    minio_root_user = "minio"

    # Langfuse uses the MinIO root credentials for S3-compatible access here.
    s3_access_key = minio_root_user
    s3_secret_key = minio_root_password

    lines = [
        f'NEXTAUTH_URL="https://{cfg.domain_web}"',
        f'NEXTAUTH_SECRET="{nextauth_secret}"',
        f'SALT="{salt}"',
        f'ENCRYPTION_KEY="{encryption_key}"',
        "",
        'POSTGRES_USER="postgres"',
        f'POSTGRES_PASSWORD="{postgres_password}"',
        'POSTGRES_DB="postgres"',
        f'DATABASE_URL="postgresql://postgres:{postgres_password}@postgres:5432/postgres"',
        "",
        f'REDIS_AUTH="{redis_auth}"',
        "",
        'CLICKHOUSE_USER="clickhouse"',
        f'CLICKHOUSE_PASSWORD="{clickhouse_password}"',
        "",
        f'MINIO_ROOT_USER="{minio_root_user}"',
        f'MINIO_ROOT_PASSWORD="{minio_root_password}"',
        "",
        'LANGFUSE_S3_EVENT_UPLOAD_BUCKET="langfuse"',
        'LANGFUSE_S3_EVENT_UPLOAD_REGION="auto"',
        f'LANGFUSE_S3_EVENT_UPLOAD_ACCESS_KEY_ID="{s3_access_key}"',
        f'LANGFUSE_S3_EVENT_UPLOAD_SECRET_ACCESS_KEY="{s3_secret_key}"',
        'LANGFUSE_S3_EVENT_UPLOAD_ENDPOINT="http://minio:9000"',
        'LANGFUSE_S3_EVENT_UPLOAD_FORCE_PATH_STYLE="true"',
        'LANGFUSE_S3_EVENT_UPLOAD_PREFIX="events/"',
        "",
        'LANGFUSE_S3_MEDIA_UPLOAD_BUCKET="langfuse"',
        'LANGFUSE_S3_MEDIA_UPLOAD_REGION="auto"',
        f'LANGFUSE_S3_MEDIA_UPLOAD_ACCESS_KEY_ID="{s3_access_key}"',
        f'LANGFUSE_S3_MEDIA_UPLOAD_SECRET_ACCESS_KEY="{s3_secret_key}"',
        f'LANGFUSE_S3_MEDIA_UPLOAD_ENDPOINT="https://{cfg.domain_s3}"',
        'LANGFUSE_S3_MEDIA_UPLOAD_FORCE_PATH_STYLE="true"',
        'LANGFUSE_S3_MEDIA_UPLOAD_PREFIX="media/"',
        "",
    ]
    return ("\n".join(lines)).encode("utf-8")


def main() -> int:
    host = _require_env("VPS_HOST")
    user = _require_env("VPS_USER")
    password = _require_env("VPS_PASS")
    domain_web = os.environ.get("LANGFUSE_DOMAIN", "langfuse.omniforge.com.br").strip()
    domain_s3 = os.environ.get("LANGFUSE_S3_DOMAIN", f"s3.{domain_web}").strip()

    cfg = VPSConfig(
        host=host,
        user=user,
        password=password,
        domain_web=domain_web,
        domain_s3=domain_s3,
    )

    if not DEPLOY_DIR.exists():
        raise SystemExit(f"Missing deploy dir: {DEPLOY_DIR}")

    # Local DNS sanity check (helps catch obvious issues early).
    web_ip = _resolve_a(cfg.domain_web)
    s3_ip = _resolve_a(cfg.domain_s3)
    if web_ip and web_ip != cfg.host:
        print(f"[warn] {cfg.domain_web} resolves to {web_ip}, expected {cfg.host}")
    if s3_ip and s3_ip != cfg.host:
        print(f"[warn] {cfg.domain_s3} resolves to {s3_ip}, expected {cfg.host}")
    if not web_ip:
        print(f"[warn] {cfg.domain_web} does not resolve (yet)")
    if not s3_ip:
        print(f"[warn] {cfg.domain_s3} does not resolve (yet)")

    env_bytes = build_env_file(cfg)
    compose_bytes = _read_file(DEPLOY_DIR / "docker-compose.yml")
    caddyfile_bytes = _read_file(DEPLOY_DIR / "Caddyfile")

    # Replace hardcoded domains in Caddyfile, in case user changed them via env vars.
    caddy_text = caddyfile_bytes.decode("utf-8")
    caddy_text = caddy_text.replace("langfuse.omniforge.com.br", cfg.domain_web)
    caddy_text = caddy_text.replace("s3.langfuse.omniforge.com.br", cfg.domain_s3)
    caddyfile_bytes = caddy_text.encode("utf-8")

    with SSH(cfg.host, cfg.user, cfg.password) as ssh:
        # Basic info
        _, os_info_out, _ = ssh.run(
            "set -euo pipefail; uname -a; echo '---'; cat /etc/os-release",
            check=False,
        )
        if os_info_out.strip():
            print(os_info_out.strip())

        # Identify distro early (also lets us fix a broken Docker APT source from previous runs).
        _, distro_out, _ = ssh.run(
            "set -euo pipefail; . /etc/os-release; echo \"$ID\"; echo \"$VERSION_CODENAME\"",
            check=True,
        )
        parts = [p.strip() for p in distro_out.splitlines() if p.strip()]
        distro_id = parts[0] if len(parts) > 0 else ""
        distro_codename = parts[1] if len(parts) > 1 else ""

        # Clean up any wrong Docker repo config that would break apt-get update.
        if distro_id == "debian":
            ssh.run(
                "set -euo pipefail; "
                "if [ -f /etc/apt/sources.list.d/docker.list ] && "
                "grep -q 'download.docker.com/linux/ubuntu' /etc/apt/sources.list.d/docker.list; then "
                "rm -f /etc/apt/sources.list.d/docker.list; "
                "fi"
            )
        if distro_id == "ubuntu":
            ssh.run(
                "set -euo pipefail; "
                "if [ -f /etc/apt/sources.list.d/docker.list ] && "
                "grep -q 'download.docker.com/linux/debian' /etc/apt/sources.list.d/docker.list; then "
                "rm -f /etc/apt/sources.list.d/docker.list; "
                "fi"
            )

        # Ensure base tools
        ssh.run(
            "set -euo pipefail; "
            "apt-get update -y && "
            "DEBIAN_FRONTEND=noninteractive apt-get install -y ca-certificates curl gnupg openssl ufw"
        )

        # Firewall: allow SSH + HTTP(S)
        ssh.run("set -euo pipefail; ufw allow OpenSSH || true")
        ssh.run("set -euo pipefail; ufw allow 80/tcp || true")
        ssh.run("set -euo pipefail; ufw allow 443/tcp || true")
        ssh.run("set -euo pipefail; ufw --force enable || true", check=False)

        # Install Docker if missing (Debian/Ubuntu).
        _, docker_rc_out, _ = ssh.run(
            "set -euo pipefail; command -v docker >/dev/null 2>&1; echo $?",
            check=False,
        )
        docker_missing = docker_rc_out.strip() != "0"

        if docker_missing:
            if distro_id not in ("debian", "ubuntu") or not distro_codename:
                raise SystemExit(
                    f"Unsupported distro for automatic Docker install (id={distro_id!r}, codename={distro_codename!r})"
                )
            docker_repo_os = "debian" if distro_id == "debian" else "ubuntu"

            ssh.run(
                "set -euo pipefail; "
                "install -m 0755 -d /etc/apt/keyrings; "
                f"curl -fsSL https://download.docker.com/linux/{docker_repo_os}/gpg | gpg --dearmor --batch --yes --no-tty -o /etc/apt/keyrings/docker.gpg; "
                "chmod a+r /etc/apt/keyrings/docker.gpg; "
                "echo "
                f"\"deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/{docker_repo_os} "
                f"{distro_codename} stable\" "
                "> /etc/apt/sources.list.d/docker.list; "
                "apt-get update -y; "
                "DEBIAN_FRONTEND=noninteractive apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin; "
                "systemctl enable --now docker"
            )

        # Prepare /opt/langfuse
        ssh.run("set -euo pipefail; mkdir -p /opt/langfuse; chmod 0755 /opt/langfuse")

        # Upload files
        ssh.put_bytes("/opt/langfuse/docker-compose.yml", compose_bytes, mode=0o644)
        ssh.put_bytes("/opt/langfuse/Caddyfile", caddyfile_bytes, mode=0o644)
        ssh.put_bytes("/opt/langfuse/.env", env_bytes, mode=0o600)

        # Pull & start
        ssh.run("set -euo pipefail; cd /opt/langfuse; docker compose pull")
        ssh.run("set -euo pipefail; cd /opt/langfuse; docker compose up -d")

        # Quick status
        ssh.run("set -euo pipefail; cd /opt/langfuse; docker compose ps", check=False)

    print("[ok] Langfuse stack deployed to /opt/langfuse")
    print("[note] Secrets were generated and stored in /opt/langfuse/.env on the VPS.")
    print(f"[next] Ensure DNS A records for {cfg.domain_web} and {cfg.domain_s3} point to {cfg.host} before TLS issuance.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
