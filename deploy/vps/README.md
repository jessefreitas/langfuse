# Langfuse VPS Deployment (Docker + Caddy + TLS)

This deploy uses the official Langfuse Docker images plus Postgres, Redis, ClickHouse, and MinIO.

It exposes:
- `https://langfuse.omniforge.com.br` (Langfuse web)

## Prereqs
- A VPS running Ubuntu (22.04/24.04 recommended).
- DNS `A` records pointing to your VPS IP:
  - `langfuse.omniforge.com.br` -> `<VPS_IP>`
- Ports open on the VPS firewall/security-group: `80/tcp`, `443/tcp`, `22/tcp`.

## Install Docker (Ubuntu)
Run on the VPS:

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl gnupg
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo \"$VERSION_CODENAME\") stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
sudo systemctl enable --now docker
```

## Configure Langfuse
On the VPS:

```bash
sudo mkdir -p /opt/langfuse
sudo chown -R $USER:$USER /opt/langfuse
```

Copy these files into `/opt/langfuse/`:
- `deploy/vps/docker-compose.yml` -> `/opt/langfuse/docker-compose.yml`
- `deploy/vps/Caddyfile` -> `/opt/langfuse/Caddyfile`
- Create `/opt/langfuse/.env` based on `deploy/vps/.env.example`

Generate secrets (examples):

```bash
openssl rand -base64 32  # for NEXTAUTH_SECRET
openssl rand -base64 32  # for SALT
openssl rand -hex 32     # for ENCRYPTION_KEY
openssl rand -base64 32  # for POSTGRES_PASSWORD / MINIO / REDIS / CLICKHOUSE if desired
```

## Start

```bash
cd /opt/langfuse
docker compose pull
docker compose up -d
docker compose ps
```

## Notes
- This setup keeps Postgres/Redis/ClickHouse/MinIO internal to the Docker network and exposes only Caddy (80/443).
- If you do not want direct media uploads, you can remove/blank the `LANGFUSE_S3_MEDIA_*` variables, but media endpoints will not work.

## Media Upload Storage Options
- MinIO (self-hosted): you must expose a public S3 endpoint (e.g. `https://s3.example.com`) because the browser needs to reach the presigned upload URL. If you do this, enable the optional `s3.*` block in `deploy/vps/Caddyfile` and create a DNS `A` record for it.
- Cloudflare R2: set `LANGFUSE_S3_MEDIA_UPLOAD_ENDPOINT` to `https://<ACCOUNT_ID>.r2.cloudflarestorage.com` and use your R2 S3 access key/secret. You still need to configure CORS on the bucket to allow browser `PUT` from your Langfuse origin.
