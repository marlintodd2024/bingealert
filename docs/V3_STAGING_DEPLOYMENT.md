# BingeAlert v3 staging deployment

This runbook deploys the `codex/v3.0-ops-cockpit` branch alongside an existing
BingeAlert installation at:

`https://binge.dev.marlintodd.com`

The staging stack builds directly from the checked-out branch. It does not use
the production `latest` image and does not share the production database.

## Isolation

| Resource | v3 staging value |
|---|---|
| Compose project | `bingealert-v3-dev` |
| Container | `bingealert-v3-dev` |
| Local image | `bingealert:v3-staging` |
| Host listener | `127.0.0.1:8010` |
| Persistent data | `./data-v3-dev/` |
| Public URL | `https://binge.dev.marlintodd.com` |

Never mount the production `./data/` directory into this stack. A migration
rehearsal must use a separate SQLite backup, not the live database files.

## Deploy

Run these commands on the Docker host from a separate staging clone:

```bash
git fetch origin
git switch codex/v3.0-ops-cockpit
git pull --ff-only origin codex/v3.0-ops-cockpit

mkdir -p data-v3-dev
sudo chown -R 1000:1000 data-v3-dev

export DOCKER_GID=$(stat -c '%g' /var/run/docker.sock)
export BINGEALERT_BUILD_SHA=$(git rev-parse --short HEAD)
export BINGEALERT_BUILD_TAG=v3-staging

docker compose -f docker-compose.v3-staging.yml up -d --build
docker compose -f docker-compose.v3-staging.yml ps
```

The first start opens the normal setup wizard. The compose file supplies
`PUBLIC_BASE_URL=https://binge.dev.marlintodd.com`, so status, calendar, and
email links point at the staging hostname.

## Reverse proxy

Point the existing reverse proxy or Cloudflare Tunnel hostname at:

`http://127.0.0.1:8010`

The loopback bind keeps the staging port off the LAN and Internet. If the
reverse proxy or tunnel itself runs in Docker, connect it through a controlled
Docker network or set `BINGEALERT_V3_BIND_IP` to a host address protected by a
firewall. Do not publish port 8010 directly to the Internet.

Caddy needs only:

```caddyfile
binge.dev.marlintodd.com {
    reverse_proxy 127.0.0.1:8010
}
```

For nginx, preserve client/protocol headers and disable response buffering so
the Activity log stream remains live:

```nginx
server {
    listen 443 ssl http2;
    server_name binge.dev.marlintodd.com;

    location / {
        proxy_pass http://127.0.0.1:8010;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_buffering off;
        proxy_read_timeout 3600;
    }
}
```

Set `trusted_proxy_cidrs` in the setup wizard to the network containing the
immediate proxy. Keep authentication enabled for this Internet-facing host.

## Safe staging setup

The staging instance can read the same Plex, Seerr, Sonarr, and Radarr APIs,
but outbound and mutating behavior needs deliberate isolation:

1. Configure SMTP to a test mailbox or SMTP sink, not the normal user relay.
2. Leave Pushover/webhook alerts disabled until their staging destination is set.
3. Keep issue auto-fix in `manual` mode.
4. Do not add the staging webhook URLs to live Seerr, Sonarr, or Radarr until
   duplicate notifications cannot reach real users.
5. Use a dedicated webhook secret before accepting public webhook traffic.

Use the services' LAN addresses or Docker DNS names in setup. Do not enter
`127.0.0.1` or `localhost` for Plex, Seerr, Sonarr, or Radarr: inside the
BingeAlert container those names refer to BingeAlert itself, and loopback
integration targets are rejected.

When mirrored webhooks are enabled, use these staging URLs:

```text
https://binge.dev.marlintodd.com/webhooks/jellyseerr
https://binge.dev.marlintodd.com/webhooks/sonarr
https://binge.dev.marlintodd.com/webhooks/radarr
```

## Smoke test

```bash
curl -fsS http://127.0.0.1:8010/health/
curl -fsS http://127.0.0.1:8010/api/version
docker compose -f docker-compose.v3-staging.yml logs --tail=200 bingealert
```

Then verify in the UI:

1. The footer and `/api/version` report `v3.0.0`.
2. System Health returns JSON and all configured services can be checked.
3. Every Settings category shows one save button and no global duplicate.
4. A test email and test Pushover message reach only staging destinations.
5. Webhook Inbox records a sanitized test payload and guarded replay works.
6. Reports, request timelines, user status pages, and user digest delivery work.
7. Restart the container and confirm settings, history, and worker state persist.

Run this deployment for at least 24 hours before the production tag. Review
worker failures, notification errors, service flapping, database locks, and
container restarts during the soak.

## Rehearse the production database upgrade

Use SQLite's online backup command to make a consistent copy while production
is running. Change `/path/to/production/data` to the actual production path:

```bash
docker compose -f docker-compose.v3-staging.yml down
mv data-v3-dev data-v3-dev-clean-soak
mkdir -p data-v3-dev
sqlite3 /path/to/production/data/bingealert.db \
  ".backup '$(pwd)/data-v3-dev/bingealert.db'"
sudo chown -R 1000:1000 data-v3-dev
docker compose -f docker-compose.v3-staging.yml up -d --build
```

Do not copy production `config.json`, `bingealert.db-wal`, or
`bingealert.db-shm`. Starting with only the database backup lets Alembic test
the real upgrade while the setup wizard creates staging-only service, SMTP,
alert, secret, and public-URL configuration. The original clean staging data is
preserved in `data-v3-dev-clean-soak/` and can be restored after the rehearsal.

## Update the staging instance

```bash
git pull --ff-only origin codex/v3.0-ops-cockpit
export BINGEALERT_BUILD_SHA=$(git rev-parse --short HEAD)
docker compose -f docker-compose.v3-staging.yml up -d --build
```

## Stop or roll back

```bash
docker compose -f docker-compose.v3-staging.yml down
```

That command preserves `./data-v3-dev/`. To return to an earlier tested commit,
check out that commit and rebuild the same stack. Production remains untouched.

## Final release gate

After staging passes, capture the production container file inventory and run:

```bash
./scripts/check_prod_drift.sh prod-files.txt
```

Only then merge/tag `v3.0.0` and verify the multi-architecture GHCR package.
