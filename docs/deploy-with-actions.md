# Deploy from GitHub Actions

The workflow `.github/workflows/deploy.yml` ("Deploy to my server") connects to your VPS over SSH, writes the configuration, pulls the latest image and restarts the stack. It uses plain `ssh` and `scp`; your secrets are never passed to a third-party action.

## Prepare the server

1. Create a DNS `A` record for your domain (see [server.md](server.md), step 1).
2. Install Docker on the server (step 2) and open ports 80/443.
3. Create a dedicated SSH key: `ssh-keygen -t ed25519 -f lc_deploy -N ""`. Add `lc_deploy.pub` to `~/.ssh/authorized_keys` of a user who can run Docker.

## Add repository secrets

Settings, Secrets and variables, Actions, New repository secret.

| Secret | Required | Value |
| --- | --- | --- |
| `SSH_HOST` | yes | server IP or hostname |
| `SSH_USER` | yes | SSH user |
| `SSH_PRIVATE_KEY` | yes | content of `lc_deploy` (private key) |
| `SSH_PORT` | no | default 22 |
| `DOMAIN` | yes | e.g. `leads.example.com` |
| `POSTGRES_PASSWORD` | yes | `openssl rand -hex 24` |
| `LEADCOMMONS_PASSWORD_HASH` | yes | output of `leadcommons hash-password` |
| `LEADCOMMONS_SECRET_KEY` | yes | `openssl rand -hex 32` |

## Run

Actions tab, "Deploy to my server", Run workflow. The job copies `docker-compose.server.yml`, `Caddyfile` and a generated `.env` to `~/leadcommons` on the server, runs `docker compose pull && docker compose up -d`, and finally checks `https://$DOMAIN/healthz`.

Run it again whenever you want to update. If your fork publishes its own image, change the image name in `deploy/docker-compose.server.yml`.
