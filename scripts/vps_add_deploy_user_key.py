import os
import time
from dataclasses import dataclass
from pathlib import Path

import paramiko


REPO_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class VPS:
    host: str
    user: str
    password: str


def _require_env(name: str) -> str:
    v = os.environ.get(name, "").strip()
    if not v:
        raise SystemExit(f"Missing env var: {name}")
    return v


class SSH:
    def __init__(self, vps: VPS):
        self.vps = vps
        self.client = None

    def __enter__(self) -> "SSH":
        c = paramiko.SSHClient()
        c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        c.connect(
            self.vps.host,
            username=self.vps.user,
            password=self.vps.password,
            timeout=20,
            banner_timeout=20,
            auth_timeout=20,
        )
        self.client = c
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.client:
            self.client.close()
            self.client = None

    def run(self, cmd: str, check: bool = True) -> tuple[int, str, str]:
        stdin, stdout, stderr = self.client.exec_command(cmd)
        out = stdout.read().decode("utf-8", errors="replace")
        err = stderr.read().decode("utf-8", errors="replace")
        rc = stdout.channel.recv_exit_status()
        if check and rc != 0:
            raise RuntimeError(f"Command failed ({rc}): {cmd}\n{err}".strip())
        return rc, out, err

    def put_bytes(self, remote_path: str, content: bytes, mode: int = 0o600) -> None:
        sftp = self.client.open_sftp()
        try:
            tmp = f"{remote_path}.tmp-{int(time.time())}"
            with sftp.file(tmp, "wb") as f:
                f.write(content)
            sftp.chmod(tmp, mode)
            try:
                sftp.remove(remote_path)
            except OSError:
                pass
            sftp.rename(tmp, remote_path)
        finally:
            sftp.close()


def main() -> int:
    vps = VPS(
        host=_require_env("VPS_HOST"),
        user=_require_env("VPS_USER"),
        password=_require_env("VPS_PASS"),
    )

    deploy_user = os.environ.get("DEPLOY_USER", "deploy").strip() or "deploy"
    pubkey_path = Path(_require_env("DEPLOY_PUBKEY_PATH"))
    pubkey = pubkey_path.read_text(encoding="utf-8").strip() + "\n"

    with SSH(vps) as ssh:
        # Create user + group (idempotent).
        ssh.run(
            "set -euo pipefail; "
            f"id -u {deploy_user} >/dev/null 2>&1 || useradd -m -s /bin/bash {deploy_user}; "
            # Ensure docker group exists and user is in it (for docker socket access).
            "getent group docker >/dev/null 2>&1 || groupadd docker; "
            f"usermod -aG docker {deploy_user}"
        )

        # Setup authorized_keys
        ssh.run(
            "set -euo pipefail; "
            f"install -d -m 0700 -o {deploy_user} -g {deploy_user} /home/{deploy_user}/.ssh; "
            f"touch /home/{deploy_user}/.ssh/authorized_keys; "
            f"chown {deploy_user}:{deploy_user} /home/{deploy_user}/.ssh/authorized_keys; "
            f"chmod 0600 /home/{deploy_user}/.ssh/authorized_keys"
        )

        # Append key if missing
        ssh.run(
            "set -euo pipefail; "
            f"grep -qxF {repr(pubkey.strip())} /home/{deploy_user}/.ssh/authorized_keys || "
            f"printf %s {repr(pubkey)} >> /home/{deploy_user}/.ssh/authorized_keys"
        )

        # Make /opt/langfuse readable by deploy (but not world-readable).
        # This is required because docker compose reads /opt/langfuse/.env.
        ssh.run(
            "set -euo pipefail; "
            "if [ -d /opt/langfuse ]; then "
            f"chown -R root:{deploy_user} /opt/langfuse; "
            "chmod 0750 /opt/langfuse; "
            f"chmod 0640 /opt/langfuse/.env || true; "
            "chmod 0644 /opt/langfuse/docker-compose.yml /opt/langfuse/Caddyfile || true; "
            "fi"
        )

    print(f"[ok] Added deploy user/key on VPS. User: {deploy_user}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

