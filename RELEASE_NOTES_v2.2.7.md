# BingeAlert v2.2.7 - dependency security bump + update indicator

A small security and polish release.

This release does not include a database migration. Restart the container after
upgrading so the updated dependency set and version probe are loaded.

---

## Security

### python-multipart Dependabot alerts

Upgraded `python-multipart` from `0.0.27` to `0.0.32`.

This clears the current Dependabot alerts against the direct
`python-multipart` pin in `requirements.txt`, including the semicolon
querystring parsing and multipart header/content-length advisories reported by
GitHub.

---

## Added

### Footer update indicator

The shared version probe now checks the latest public BingeAlert GitHub release
and caches the result server-side.

When a newer release exists, the admin dashboard, login page, and first-run
setup wizard footers show:

```text
Update available: vX.Y.Z
```

The indicator links to the GitHub release. If GitHub is unreachable, the footer
quietly keeps showing only the running version.

---

## Upgrade

```bash
cd /path/to/your/bingealert
sed -i -E 's|bingealert:2\.[0-9]+\.[0-9]+|bingealert:2.2.7|' docker-compose.yml
docker compose pull
docker compose up -d --force-recreate
```

After upgrading, the dashboard footer should read `v2.2.7`. When a newer
GitHub release is published, the same footer will show the update link.
