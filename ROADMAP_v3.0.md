# BingeAlert v3.0 Roadmap

## Product Positioning

BingeAlert v3.0 should become the operations cockpit for Plex homelab admins
who use Seerr, Sonarr, and Radarr.

The core promise:

> BingeAlert tells you what happened after the request.

Seerr owns request intake. Sonarr and Radarr own acquisition. Plex owns
playback. BingeAlert should own the operational gap between them:

- Did the request actually become watchable?
- Did the right people get notified?
- If not, where did the chain break?
- What should the admin look at today?

## Release Goals

- Give admins a reason to open BingeAlert daily.
- Make notification and webhook debugging obvious.
- Reduce "is my show ready yet?" messages.
- Turn system health from a passive table into an action queue.
- Add enough user-facing value to market BingeAlert as more than an email relay.

## Guiding Principles

- Prefer clear operational answers over raw logs.
- Every alert should link to a relevant action or timeline.
- Preserve BingeAlert's focused lane; do not clone Seerr or Tautulli.
- Keep advanced diagnostics available, but make the default view calm and useful.
- Make all new background DB sessions follow the documented session lifecycle.

---

## Phase 0 - Foundation and Release Track

Purpose: prepare the codebase for larger v3 work without destabilizing v2.x.

Deliverables:

- Dedicated branch: `codex/v3.0-ops-cockpit`.
- This roadmap document.
- Decide schema names and retention rules for event/timeline data.
- Decide whether v3.0 includes one database migration or several smaller ones.
- Add internal feature flags if large UI sections need to land incrementally.

Exit criteria:

- Roadmap accepted.
- Data model direction chosen.
- First implementation phase can start without changing product scope.

---

## Phase 1 - Daily Admin Home

Purpose: make the first screen answer "what needs attention today?"

Deliverables:

- Replace the current stats-first dashboard with a Daily Admin Home.
- Add a prioritized action queue:
  - services down
  - failed notifications
  - pending notifications past due
  - stuck imports/downloads
  - open Seerr issues
  - requests downloaded but not visible in Plex
  - low disk/root-folder warnings
- Add "last 24 hours" activity cards:
  - requests fulfilled
  - users notified
  - issues reported/resolved
  - webhook failures
  - service outages
- Add one-click jumps from each card to the relevant filtered tab.

Implementation notes:

- Build a compact `/admin/daily-brief` endpoint.
- Reuse existing health, notification, issue, and activity data first.
- Avoid adding new polling-heavy service calls in this phase.

Exit criteria:

- An admin can open BingeAlert and know what needs attention in under 30 seconds.
- The dashboard still works when one integration is down.

---

## Phase 2 - Request Timeline

Purpose: answer "why did or didn't this notify?"

Deliverables:

- Add a timeline view for each request:
  - requested
  - approved
  - grabbed
  - imported
  - Plex indexed
  - notification queued
  - notification sent
  - notification failed
  - issue reported/resolved
- Add timeline links from Requests, Notifications, Issues, and Daily Home.
- Add timeline badges for broken states:
  - no matching webhook
  - Plex not indexed yet
  - notification delayed for batching
  - skipped by dedupe ledger
  - blocked by stale lookback

Implementation notes:

- Initial v3 slice: add `/admin/requests/{request_id}/timeline` and synthesize
  useful events from current rows so old requests are immediately useful.
- Later hardening slice: add a `request_event_log` or equivalent append-only
  table for exact grabbed/imported/indexed transitions.
- Log new events from webhooks, reconciliation, notification processing, and issue
  handling.
- Keep payloads sanitized. Store event facts, not raw secrets.

Exit criteria:

- For any request, the admin can identify the current state and last successful
  step.

---

## Phase 3 - Webhook Inbox and Replay

Purpose: make setup and webhook debugging self-service.

Deliverables:

- Add Webhook Inbox tab:
  - received time
  - source service
  - event type
  - processing result
  - matched request/user
  - error summary
  - timeline link
- Add sanitized payload viewer.
- Add replay button for recent webhook events.
- Add filters for failed/unmatched/replayed events.
- Add setup test helpers:
  - simulate Sonarr import
  - simulate Radarr import
  - simulate Seerr issue
  - simulate service-health alert

Implementation notes:

- Add `webhook_event_log`.
- Store sanitized payload snapshots with retention limits.
- Replay should run the same handler path with a replay marker and protection
  against duplicate sends unless the admin explicitly overrides.
- Initial v3 slice adds the `webhook_event_log` table, sanitized inbox UI,
  payload viewer, failed/unmatched filters, Daily Home failure jump, and guarded
  replay for Sonarr/Radarr/Seerr webhooks.
- Seerr `ISSUE_CREATED` and `ISSUE_COMMENT` replay is blocked for now; those
  issue paths need stronger issue-level dedupe before manual replay is safe.
- Setup test helpers remain a follow-up item after the inbox proves out against
  real webhook traffic.

Exit criteria:

- A new user can test webhooks without waiting for real media events.
- A failed webhook has enough context to diagnose from the UI.

---

## Phase 4 - Queue, Import, and Storage Health

Purpose: detect the most common homelab failure modes before users notice.

Deliverables:

- Sonarr/Radarr queue health:
  - stuck downloads
  - import failures
  - no seeders
  - wrong category
  - download client errors
  - grabbed but not imported after threshold
- Root folder and disk monitoring:
  - free space per configured root folder
  - low-space thresholds
  - estimated days until full if data is available
  - largest pending downloads
- Import-to-Plex lag view:
  - imported but not indexed
  - indexed but not notified
  - notified but failed delivery

Implementation notes:

- Prefer Sonarr/Radarr APIs for queue and root-folder data.
- Plex indexing checks should be bounded and cached.
- Do not poll aggressively; make intervals configurable.
- Initial v3 slice adds a live `/admin/ops-health` endpoint and Health-tab
  sections for Sonarr/Radarr queue diagnostics, root-folder/disk free space, and
  import-to-Plex notification lag.
- Queue classification flags import failures, stalled/failed items, no-seeder
  hints, download-client errors, and slow queue items. The existing stuck
  download worker remains responsible for automated remediation and email alerts.
- Storage uses Sonarr/Radarr `/rootfolder` and `/diskspace` data with conservative
  built-in warning thresholds until per-admin thresholds are added.
- Import-to-Plex lag is inferred from BingeAlert tracking, notification, and
  delivery-ledger rows; live Plex library probes remain a follow-up to avoid
  expensive searches on every dashboard load.

Exit criteria:

- BingeAlert can explain acquisition/import/storage issues without requiring the
  admin to open Sonarr, Radarr, and Plex first.

---

## Phase 5 - User Status and Notification Preferences

Purpose: reduce admin interruptions and give users controlled visibility.

Deliverables:

- Magic-link user status page:
  - active requests
  - pending/waiting/downloaded/available states
  - notification history
  - calendar subscription link
  - report issue shortcut
- User notification preferences:
  - instant vs digest
  - quiet hours
  - only notify when full season is ready
  - notify on quality upgrades
  - email vs push/webhook provider where configured
- Admin controls to reset a user's magic link.

Implementation notes:

- Keep magic links long, random, revocable, and scoped.
- Preferences should default to current behavior.
- Avoid requiring users to create accounts in v3.0.
- Initial v3 slice adds a separate `users.status_token`, public `/user/{token}`
  status portal, scoped `/user/api/{token}` JSON, and per-user preference
  saving without requiring accounts.
- Admins can copy or reset each user's status/preferences link from the Users
  table. Resetting the token immediately revokes the old link.
- Notification emails now include the status/preferences link when
  `public_base_url` is configured. Calendar links remain separately revocable
  through `calendar_token`.
- Quiet hours are enforced by the notification processor for opted-in users.
- Phase 7 completes digest delivery, full-season waits, and quality-update
  preference enforcement through the shared digest worker.

Exit criteria:

- A user can answer "what is happening with my request?" without messaging the
  admin.

---

## Phase 6 - Digests, Reports, and Marketing Launch

Purpose: turn BingeAlert into a product admins remember and recommend.

Deliverables:

- Daily admin digest:
  - overnight problems
  - fulfilled requests
  - failed notifications
  - open issues
  - low disk
  - slow requests
- Weekly operations report:
  - request volume
  - fulfillment time
  - notification delivery
  - top requesters
  - recurring failures
- Optional user digest:
  - newly available items
  - still waiting
  - upcoming episodes
- Marketing docs:
  - updated README
  - screenshots
  - "Why BingeAlert?" section
  - quick-start demo path
  - comparison positioning against Seerr notifications, Tautulli, and Notifiarr

Implementation notes:

- Initial v3 slice adds shared report generation in `app/services/reporting.py`,
  `/admin/reports/ops`, manual daily digest and weekly ops-report send
  endpoints, and a Reports tab with daily trend, top requesters, recurring
  failures, and oldest waiting requests.
- The scheduled Sunday worker now sends the weekly operations report instead
  of the older notification-only summary.
- The README now frames BingeAlert as a Plex request ops dashboard, adds a
  quick demo path, and positions the project alongside Seerr, Sonarr/Radarr,
  Tautulli, Notifiarr, and broad alert hubs.
- User digest and full-season batching ship in the Phase 7 release-candidate
  pass, using the preferences introduced in Phase 5.
- Final screenshots should be captured from a populated v3.0 instance before
  tagging the public release.

Exit criteria:

- v3.0 has a clear launch story:
  - "Your Plex request ops dashboard"
  - "Know what happened after the request"
  - "Debug notification gaps in one place"

---

## Phase 7 - Release Candidate and Launch Hardening

Purpose: close exposed-but-inert preferences, verify upgrades, and make the
branch safe to publish as `v3.0.0`.

Delivered:

- Scheduled digest worker:
  - optional daily admin operations digest
  - daily grouped user digest at a configurable UTC hour
  - quiet-hour recheck before delivery
  - full-season waits based on monitored Sonarr episode file state
  - manual user-digest run from Reports
- Notification consistency:
  - digest/full-season rows are handed off by the normal processor
  - coming-soon mail uses the shared queue instead of bypassing preferences
  - quality-update opt-outs are enforced
  - successful grouped sends update the durable dedupe ledger and tracking state
- Operations hardening:
  - SMTP health alerts are regression-tested to never use SMTP email
  - sanitized webhook events default to 30-day retention
  - notification processing no longer initializes Sonarr for movie-only mail
  - fixed a local `settings` shadow that crashed normal notification delivery
- Interface hardening:
  - Settings shows one section at a time
  - each editable section has exactly one immediately visible save action
  - desktop and mobile checks found no page-level horizontal overflow
- Release verification:
  - standard-library regression suite for digests, season completion, dedupe,
    SMTP alert routing, webhook sanitization/retention, and quality preferences
  - real Alembic upgrade test from `0005_notification_delivery_log` through head
  - CI test workflow on pull requests and `codex/**` branches

Release decisions:

- Webhook payloads are stored sanitized-only and pruned after 30 days by default.
- User status pages remain token-authenticated, enabled by default, and revocable.
- v3.0 remains SQLite-only; Postgres guidance is deferred until real event-history
  volume demonstrates a need.

Exit criteria:

- Fresh schema and `v2.3.5` upgrade tests pass.
- Notification delivery preferences affect runtime behavior.
- Desktop/mobile dashboard and user portal checks pass without incoherent overlap.
- Prod drift check passes immediately before the public tag is created.

---

## Phase 8 - Staging Soak and Production Launch

Purpose: run the release candidate beside production under its real hostname,
prove the upgrade and operational workflows, then publish `v3.0.0`.

Status: in progress on `codex/v3.0-ops-cockpit`.

Deliverables:

- Isolated branch-built Docker stack:
  - `bingealert-v3-dev` project and container
  - `bingealert:v3-staging` local image
  - `127.0.0.1:8010` reverse-proxy target
  - separate `data-v3-dev/` persistence
  - `https://binge.dev.marlintodd.com` public URL
- Production-like smoke testing:
  - setup and migration behavior
  - service and worker health
  - SMTP/Pushover routing to staging destinations
  - sanitized webhook ingest and replay
  - reports, timelines, user portal, and digest delivery
- At least 24 hours of staging soak with worker, database, and notification
  error review.
- Fresh production file inventory and drift check after staging passes.
- Merge, `v3.0.0` tag, GitHub release, multi-architecture GHCR package, and
  post-deployment verification.

Safety rules:

- Staging never mounts production `./data/`.
- SMTP and alert routes use staging-only destinations during the soak.
- Live Seerr/Sonarr/Radarr webhooks are not mirrored until duplicate user
  notifications are contained.
- The public tag is not created before the fresh production drift check.

Exit criteria:

- The branch runs at `binge.dev.marlintodd.com` for at least 24 hours without
  unexplained worker failures, notification duplication, or database errors.
- Upgrade and rollback steps are rehearsed from an isolated backup.
- Production drift is zero or explicitly reconciled.
- `v3.0.0` images pass `linux/amd64` and `linux/arm64` package verification.

---

## Stretch Candidates

These are useful, but should not block v3.0 unless they become easy while
building the core phases.

- Tautulli integration for "requested item was actually watched."
- Quality-upgrade notifications.
- Collection/watchlist automation hints.
- Apprise, ntfy, Gotify, and Discord provider expansion.
- Multi-instance routing presets beyond anime: 4K, kids, documentaries.
- Exportable support bundle with sanitized config, health, and recent events.
- Built-in onboarding checklist and setup wizard improvements.

## Suggested v3.0 Milestones

1. `v3.0-alpha1` - Daily Admin Home.
2. `v3.0-alpha2` - Request Timeline.
3. `v3.0-beta1` - Webhook Inbox and Replay.
4. `v3.0-beta2` - Queue, Import, and Storage Health.
5. `v3.0-rc1` - User Status and Preferences.
6. `v3.0-rc2` - Digests, reports, preferences, and release hardening.
7. Branch staging - `binge.dev.marlintodd.com` deployment and soak.
8. `v3.0` - Drift check, final package verification, tag, and public release.

## Open Decisions

- Which push provider should follow Pushover first: ntfy, Gotify, Discord, or
  Apprise?
