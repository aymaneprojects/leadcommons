# Run Pépite on your own server

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
mkdir -p ~/pepite && cd ~/pepite
BASE=https://raw.githubusercontent.com/aymaneprojects/leadcommons/main/deploy
curl -fsSLO $BASE/Caddyfile
curl -fsSL $BASE/docker-compose.server.yml -o docker-compose.yml
curl -fsSL $BASE/.env.example -o .env
chmod 600 .env
```

## 4. Fill in `.env`

```sh
openssl rand -hex 24   # POSTGRES_PASSWORD
openssl rand -hex 32   # PEPITE_SECRET_KEY
docker run --rm -it ghcr.io/aymaneprojects/leadcommons:latest pepite hash-password   # PEPITE_PASSWORD_HASH
```

Edit `.env`: set `DOMAIN`, the three generated values, and keep the single quotes around the hash.

## 5. Start

```sh
docker compose up -d
docker compose logs -f app
```

On first start the app downloads the companies of the country you set as `PEPITE_LOCALE` in `.env` (for example `PEPITE_LOCALE=fr_FR`). Without it the app starts with its small bundled sample. Download the countries you need once:

```sh
docker compose exec app pepite pack download --country FR
```

(or use Settings > Data packs after you log in; repeat `--country` for more). Caddy requests a certificate; this takes under a minute once DNS is right. Open `https://your-domain` and log in with the password you hashed. The stack also runs a `worker` service that sends sequence emails and checks replies (see [engage.md](engage.md)).

Check health: `curl https://your-domain/healthz`.

## Backups

People, lists, sequences and deals are in the Postgres volume. Mailbox passwords in that database are encrypted with a key file, `secret.key`, kept in the `app-data` volume: back it up too, or you will have to type the passwords again. The pack files can be re-downloaded.

```sh
cd ~/pepite
docker compose exec -T db pg_dump -U pepite pepite | gzip > backup-$(date +%F).sql.gz
docker compose cp app:/data/secret.key ./secret.key
```

Restore into a fresh database:

```sh
gunzip -c backup-2026-01-01.sql.gz | docker compose exec -T db psql -U pepite pepite
```

Automate with cron, for example `0 3 * * * cd ~/pepite && docker compose exec -T db pg_dump -U pepite pepite | gzip > ~/backups/pepite-$(date +\%F).sql.gz`, and copy the files off the server.

## Updates

```sh
cd ~/pepite
docker compose pull
docker compose up -d
```

To pin a version, replace `:latest` in `docker-compose.yml` with a tag such as `:0.1.0`.

## Troubleshooting

- Certificate not issued: check DNS and that ports 80/443 are reachable; see `docker compose logs caddy`.
- Cannot log in over HTTP: the cookie is HTTPS-only (`PEPITE_COOKIE_SECURE=1`); use the HTTPS address.
- Hash rejected: it contains `$`, so keep it in single quotes in `.env`.
