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

Exit criteria:

- v3.0 has a clear launch story:
  - "Your Plex request ops dashboard"
  - "Know what happened after the request"
  - "Debug notification gaps in one place"

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
6. `v3.0` - Digests, docs, screenshots, and release polish.

## Open Decisions

- Should raw webhook payloads be stored encrypted, sanitized-only, or not at all?
- How long should timeline and webhook diagnostic rows be retained by default?
- Should user status pages be enabled by default or opt-in?
- Should v3.0 keep SQLite-only support, or add optional Postgres guidance for
  heavier event histories?
- Which push provider should follow Pushover first: ntfy, Gotify, Discord, or
  Apprise?
