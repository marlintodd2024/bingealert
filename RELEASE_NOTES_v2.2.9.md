# BingeAlert v2.2.9 - update center and dependency audit

A polish and release-management update for the footer update checker added in
v2.2.7.

This release does not include a database migration.

---

## Added

### Admin update center

The Settings page now has a System & Updates panel with:

- current and latest BingeAlert versions
- last update-check timestamp
- source commit metadata when the Docker image was built with it
- latest release notes preview
- manual Check Updates button
- Docker/GHCR image details and update command
- links to Dependabot alerts, dependency graph, and the dependency audit workflow

### Update banner and toast

When a newer BingeAlert release exists, the admin dashboard now shows a visible
update banner and a once-per-session update toast, in addition to the footer
link.

### Release notes preview

The version API now returns the latest GitHub release name, publish date, URL,
and release notes body. The admin dashboard renders that body as plain text in
a modal so release notes can be inspected before opening GitHub.

### Manual refresh

`/api/version?refresh=1` bypasses the server-side update-check cache. The admin
Check Updates button uses this to fetch the latest release immediately.

### Docker image metadata

Docker builds now stamp the image with the source commit SHA and tag. The admin
update panel shows those values when available and links to the GHCR package.

---

## Changed

### Better version comparison

The update checker now uses SemVer-aware comparison, including prerelease
ordering, instead of a simple numeric tuple.

---

## CI

### Dependency audit gate

Added a `Dependency Audit` workflow:

- runs `pip-audit` against `requirements.txt` on pushes, pull requests, weekly
  schedule, and manual dispatch
- runs GitHub Dependency Review on pull requests

---

## Upgrade

```bash
cd /path/to/your/bingealert
sed -i -E 's|bingealert:2\.[0-9]+\.[0-9]+|bingealert:2.2.9|' docker-compose.yml
docker compose pull
docker compose up -d --force-recreate
```

After upgrading, the dashboard footer should read `v2.2.9`.
