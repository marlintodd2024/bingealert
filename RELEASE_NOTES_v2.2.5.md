# BingeAlert v2.2.5 — scoped Seer TV issue fixes

A safety patch for Seer/Jellyseerr issue auto-fix handling.

This release fixes a dangerous TV issue path where a season-scoped Seer issue
could be treated as a whole-series repair. In that case BingeAlert asked Sonarr
to blacklist every episode file for the series and then started a full
`SeriesSearch`, which could queue downloads across all seasons.

This release includes a small database migration.

---

## Fixed

### Season-scoped Seer issues now stay season-scoped

Seer issue payloads can describe the affected item as fields such as:

- `Affected Season: Season 3`
- `Affected Episode: All Episodes`

BingeAlert now extracts that scope and stores it on the local issue record.
For TV issues:

- affected season + all episodes now runs a Sonarr `SeasonSearch`
- affected season + affected episode now runs an `EpisodeSearch`
- only matching episode files are blacklisted before the replacement search

### Full-series TV repairs are blocked by default

If Seer sends a TV issue without an affected season, BingeAlert now refuses to
run a whole-series blacklist/search from the issue auto-fix path. This fails
closed instead of risking deletion of every episode file in the series.

### Manual issue fixes use the same scope

The admin Issues tab now passes the saved season/episode scope to the manual
Fix action. The issue list also displays scope labels such as `S03` or
`S03E05` so admins can see what BingeAlert understood before running a fix.

---

## Database migration

Adds nullable scope columns to `reported_issues`:

- `season_number`
- `episode_number`

Existing issues remain valid and will have no scope populated.

---

## Upgrade

```bash
cd /path/to/your/bingealert
sed -i -E 's|bingealert:2\.[0-9]+\.[0-9]+|bingealert:2.2.5|' docker-compose.yml
docker compose pull
docker compose up -d --force-recreate
```

After upgrading, cancel any unwanted Sonarr queue items that were created before
this fix was installed. The guard prevents new issue auto-fixes from repeating
the whole-series behavior.
