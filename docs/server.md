# Run LeadCommons on your own server

A small VPS (1 vCPU, 2 GB RAM) is enough. You get HTTPS, a login and a Postgres database.

## 1. DNS

Create an `A` record (and `AAAA` if you use IPv6) for the name you want, for example `leads.example.com`, pointing at your server's IP. Wait until `dig +short leads.example.com` returns it.

## 2. Install Docker

On Debian or Ubuntu:

```sh
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker "$USER"   # log out and back in afterwards
```

Open ports 80 and 443 in your firewall (`sudo ufw allow 80,443/tcp`; also `443/udp` for HTTP/3).

## 3. Get the files

```sh
mkdir -p ~/leadcommons && cd ~/leadcommons
BASE=https://raw.githubusercontent.com/aymaneprojects/leadcommons/main/deploy
curl -fsSLO $BASE/Caddyfile
curl -fsSL $BASE/docker-compose.server.yml -o docker-compose.yml
curl -fsSL $BASE/.env.example -o .env
chmod 600 .env
```

## 4. Fill in `.env`

```sh
openssl rand -hex 24   # POSTGRES_PASSWORD
openssl rand -hex 32   # LEADCOMMONS_SECRET_KEY
docker run --rm -it ghcr.io/aymaneprojects/leadcommons:latest leadcommons hash-password   # LEADCOMMONS_PASSWORD_HASH
```

Edit `.env`: set `DOMAIN`, the three generated values, and keep the single quotes around the hash.

## 5. Start

```sh
docker compose up -d
docker compose logs -f app
```

On first start the app downloads the latest company pack. Caddy requests a certificate; this takes under a minute once DNS is right. Open `https://your-domain` and log in with the password you hashed.

Check health: `curl https://your-domain/healthz`.

## Backups

Everything personal is in the Postgres volume (and the pack files, which can be re-downloaded).

```sh
cd ~/leadcommons
docker compose exec -T db pg_dump -U leadcommons leadcommons | gzip > backup-$(date +%F).sql.gz
```

Restore into a fresh database:

```sh
gunzip -c backup-2026-01-01.sql.gz | docker compose exec -T db psql -U leadcommons leadcommons
```

Automate with cron, for example `0 3 * * * cd ~/leadcommons && docker compose exec -T db pg_dump -U leadcommons leadcommons | gzip > ~/backups/lc-$(date +\%F).sql.gz`, and copy the files off the server.

## Updates

```sh
cd ~/leadcommons
docker compose pull
docker compose up -d
```

To pin a version, replace `:latest` in `docker-compose.yml` with a tag such as `:0.1.0`.

## Troubleshooting

- Certificate not issued: check DNS and that ports 80/443 are reachable; see `docker compose logs caddy`.
- Cannot log in over HTTP: the cookie is HTTPS-only (`LEADCOMMONS_COOKIE_SECURE=1`); use the HTTPS address.
- Hash rejected: it contains `$`, so keep it in single quotes in `.env`.
