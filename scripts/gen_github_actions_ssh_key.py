import os
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = REPO_ROOT / "deploy" / "vps" / "keys"


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    key_path = OUT_DIR / "gh_actions_ed25519"
    pub_path = OUT_DIR / "gh_actions_ed25519.pub"

    if key_path.exists() or pub_path.exists():
        print(f"[error] Key files already exist: {key_path} / {pub_path}")
        print("[hint] Delete them if you want to regenerate.")
        return 2

    comment = os.environ.get("KEY_COMMENT", "github-actions-deploy").strip() or "github-actions-deploy"

    # Use ssh-keygen (available on Git for Windows / Windows OpenSSH / Linux runners).
    subprocess.check_call(
        [
            "ssh-keygen",
            "-t",
            "ed25519",
            "-N",
            "",
            "-C",
            comment,
            "-f",
            str(key_path),
        ]
    )

    pub = pub_path.read_text(encoding="utf-8").strip()
    print("[ok] Generated GitHub Actions deploy key pair.")
    print(f"[public] {pub}")
    print(f"[path] Private key: {key_path}")
    print(f"[path] Public key:  {pub_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

