import os
import sys

import paramiko


def _require_env(name: str) -> str:
    val = os.environ.get(name, "").strip()
    if not val:
        raise SystemExit(f"Missing env var: {name}")
    return val


def main() -> int:
    host = _require_env("VPS_HOST")
    user = _require_env("VPS_USER")
    password = _require_env("VPS_PASS")

    cli = paramiko.SSHClient()
    cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    cli.connect(host, username=user, password=password, timeout=20, banner_timeout=20, auth_timeout=20)

    cmd = (
        "set -euo pipefail; "
        "cd /opt/langfuse; "
        "docker compose ps; "
        "echo '---'; "
        "docker compose logs --no-color --tail=80 caddy"
    )
    stdin, stdout, stderr = cli.exec_command(cmd)
    out = stdout.read().decode("utf-8", errors="replace")
    err = stderr.read().decode("utf-8", errors="replace")
    rc = stdout.channel.recv_exit_status()
    cli.close()

    # Windows consoles may not support unicode; write bytes to avoid encode errors.
    sys.stdout.buffer.write(out.encode("utf-8", errors="replace"))
    if err.strip():
        sys.stderr.buffer.write(err.encode("utf-8", errors="replace"))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
