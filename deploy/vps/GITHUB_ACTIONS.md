# GitHub Actions -> VPS Auto Deploy

This repo includes a workflow at `.github/workflows/deploy-vps.yml` that:
- builds `web` and `worker` Docker images from this repo
- pushes them to GHCR
- SSHes into the VPS and runs `docker compose pull` + `up -d`

## VPS Requirements
- `/opt/langfuse` exists and contains:
  - `docker-compose.yml`
  - `Caddyfile`
  - `.env` (do not store this in GitHub)
- Docker is installed.

## GitHub Secrets (required)
Add these repository secrets:
- `VPS_HOST`: VPS IP or hostname
- `VPS_USER`: SSH user on VPS (recommended: `deploy`)
- `VPS_SSH_KEY`: private key for that user (ed25519), PEM/OpenSSH format

Optional (only if GHCR packages are private):
- `GHCR_PAT`: a GitHub PAT with at least `read:packages`

## Notes
- `deploy/vps/docker-compose.yml` supports overriding images via:
  - `LANGFUSE_WEB_IMAGE`
  - `LANGFUSE_WORKER_IMAGE`
  The workflow sets these so you can deploy either official images or fork-built images.

