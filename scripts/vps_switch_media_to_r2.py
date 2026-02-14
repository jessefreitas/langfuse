import os
import re
import sys
import time
from dataclasses import dataclass
from typing import Dict, Tuple

import paramiko


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


def _parse_env(content: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip()
        if len(v) >= 2 and ((v[0] == v[-1] == '"') or (v[0] == v[-1] == "'")):
            v = v[1:-1]
        out[k] = v
    return out


def _format_env(d: Dict[str, str], original: str) -> str:
    """
    Keep original ordering/comments where possible, but update values for keys in d.
    Add missing keys at end.
    """
    existing_keys = set()
    lines = original.splitlines()
    out_lines = []
    for line in lines:
        m = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$", line)
        if not m:
            out_lines.append(line)
            continue
        k = m.group(1)
        existing_keys.add(k)
        if k in d:
            out_lines.append(f'{k}="{d[k]}"')
        else:
            out_lines.append(line)
    # Append any missing keys.
    missing = [k for k in d.keys() if k not in existing_keys]
    if missing:
        out_lines.append("")
        out_lines.append("# Added by vps_switch_media_to_r2.py")
        for k in missing:
            out_lines.append(f'{k}="{d[k]}"')
    return "\n".join(out_lines) + "\n"


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

    def run(self, cmd: str, check: bool = True) -> Tuple[int, str, str]:
        stdin, stdout, stderr = self.client.exec_command(cmd)
        out = stdout.read().decode("utf-8", errors="replace")
        err = stderr.read().decode("utf-8", errors="replace")
        rc = stdout.channel.recv_exit_status()
        if check and rc != 0:
            raise RuntimeError(f"Command failed ({rc}): {cmd}\n{err}".strip())
        return rc, out, err

    def get_text(self, remote_path: str) -> str:
        sftp = self.client.open_sftp()
        try:
            with sftp.file(remote_path, "rb") as f:
                return f.read().decode("utf-8", errors="replace")
        finally:
            sftp.close()

    def put_text(self, remote_path: str, content: str, mode: int = 0o600) -> None:
        b = content.encode("utf-8")
        sftp = self.client.open_sftp()
        try:
            tmp = f"{remote_path}.tmp-{int(time.time())}"
            with sftp.file(tmp, "wb") as f:
                f.write(b)
            sftp.chmod(tmp, mode)
            # Some SFTP servers refuse to overwrite on rename; remove destination first.
            try:
                sftp.remove(remote_path)
            except OSError:
                pass
            sftp.rename(tmp, remote_path)
        finally:
            sftp.close()


def _strip_s3_vhost_from_caddyfile(caddyfile: str) -> str:
    """
    Remove the block for s3.langfuse.omniforge.com.br (or any `s3.<something>` host),
    leaving the main site intact.
    """
    # Simple heuristic: remove any top-level block whose label starts with "s3.".
    # Caddyfile blocks look like:
    #   host { ... }
    lines = caddyfile.splitlines()
    out = []
    i = 0
    while i < len(lines):
        line = lines[i]
        m = re.match(r"^\s*(s3\.[^\s{]+)\s*\{\s*$", line)
        if not m:
            out.append(line)
            i += 1
            continue
        # skip until matching closing brace at same nesting level
        depth = 0
        # consume header
        depth += line.count("{") - line.count("}")
        i += 1
        while i < len(lines) and depth > 0:
            depth += lines[i].count("{") - lines[i].count("}")
            i += 1
        # also skip any blank line immediately following
        while i < len(lines) and lines[i].strip() == "":
            i += 1
    return "\n".join(out).rstrip() + "\n"


def main() -> int:
    vps = VPS(
        host=_require_env("VPS_HOST"),
        user=_require_env("VPS_USER"),
        password=_require_env("VPS_PASS"),
    )

    r2_account_id = _require_env("R2_ACCOUNT_ID")
    r2_access_key = _require_env("R2_ACCESS_KEY_ID")
    r2_secret_key = _require_env("R2_SECRET_ACCESS_KEY")
    r2_bucket = _require_env("R2_MEDIA_BUCKET")

    # Cloudflare docs: presigned URLs must use <ACCOUNT_ID>.r2.cloudflarestorage.com (not custom domains).
    r2_endpoint = f"https://{r2_account_id}.r2.cloudflarestorage.com"

    with SSH(vps) as ssh:
        env_path = "/opt/langfuse/.env"
        caddy_path = "/opt/langfuse/Caddyfile"

        env_text = ssh.get_text(env_path)
        env_vars = _parse_env(env_text)

        # Update media vars only; keep existing event storage as-is.
        updates = {
            "LANGFUSE_S3_MEDIA_UPLOAD_BUCKET": r2_bucket,
            "LANGFUSE_S3_MEDIA_UPLOAD_REGION": "auto",
            "LANGFUSE_S3_MEDIA_UPLOAD_ACCESS_KEY_ID": r2_access_key,
            "LANGFUSE_S3_MEDIA_UPLOAD_SECRET_ACCESS_KEY": r2_secret_key,
            "LANGFUSE_S3_MEDIA_UPLOAD_ENDPOINT": r2_endpoint,
            # Safer default for R2: path-style avoids requiring bucket DNS like <bucket>.<account>.r2....
            "LANGFUSE_S3_MEDIA_UPLOAD_FORCE_PATH_STYLE": "true",
        }

        env_vars.update(updates)
        new_env_text = _format_env(env_vars, env_text)
        ssh.put_text(env_path, new_env_text, mode=0o600)

        # Remove s3.* vhost from Caddyfile to stop ACME retries.
        caddy_text = ssh.get_text(caddy_path)
        new_caddy_text = _strip_s3_vhost_from_caddyfile(caddy_text)
        if new_caddy_text != caddy_text:
            ssh.put_text(caddy_path, new_caddy_text, mode=0o644)

        # Restart to pick up env changes.
        ssh.run("set -euo pipefail; cd /opt/langfuse; docker compose up -d")
        ssh.run("set -euo pipefail; cd /opt/langfuse; docker compose restart caddy langfuse-web langfuse-worker")
        ssh.run("set -euo pipefail; cd /opt/langfuse; docker compose ps", check=False)

    print("[ok] Switched LANGFUSE media storage to Cloudflare R2.")
    print("[next] Configure CORS on the R2 bucket to allow browser PUT from https://langfuse.omniforge.com.br.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
