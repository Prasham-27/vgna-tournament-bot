# Data model and migration proposal

All objects live in the dedicated `vgna_tourney` PostgreSQL schema. The migration
uses `CREATE ... IF NOT EXISTS`, stable constraints, and an in-schema migration
ledger. It does not inspect, alter, or rename existing VGNA tables.

## Tables

### `teams`

One durable row per submitted team. `status` is one of:

- `pending_confirmation`
- `confirmed`
- `confirmation_failed`
- `expired`
- `withdrawn`

The row stores the stable guild ID, normalized team name, registrar Discord ID,
Discord display-name snapshots, creation/confirmation/withdrawal timestamps,
confirmation deadline, and an optional private failure note. Withdrawals and
failed confirmations update this row; they never delete it.

### `team_members`

Five rows per team submission, with stable `user_id` text, a display-name snapshot,
and membership state (`active`, `approved`, `declined`, `inactive`). A partial
unique index on active memberships enforces one active pending/confirmed team per
Discord account. The application transaction enforces five member rows before a
team can be confirmed.

### `confirmations`

One row per team member and team. It records `pending`, `approved`, or `declined`,
the response timestamp, a private reason (if supplied), and DM delivery status.
The unique `(team_id, user_id)` key makes button presses idempotent.

### `substitutes`

One current row per active substitute registration, keyed by `(guild_id, user_id)`.
Leaving or automatic removal sets `active=false` and records the reason. A partial
unique index makes join idempotent and prevents duplicate current pool entries.

### `audit_events`

Append-only event history for team submitted, member added/approved/declined,
confirmation failed/expired/confirmed, substitute joined/left/auto-removed,
membership deactivated, team withdrawn, and panel operations. Payloads contain
structured non-secret metadata; DM contents and private decline reasons are never
stored in public-facing payloads.

### `settings`

Small durable key/value store for the panel message ID and setup identifiers. It
is namespaced by guild and key, allowing test guilds to share the same database
without collision.

## Important invariants

1. There are at most eight active (`pending_confirmation` or `confirmed`) teams.
2. A submitted team has exactly five distinct member IDs.
3. An active member ID belongs to at most one active team.
4. A confirmed team member is not active in `substitutes`.
5. All deadline comparisons use an aware timestamp converted to the configured
   `America/Los_Angeles` zone; PostgreSQL stores `timestamptz`.
6. Team and membership state changes are accompanied by an audit row in the same
   transaction.

## Deadlines

The default team deadline is `2026-11-30 23:59:59 America/Los_Angeles`; the
substitute deadline is `2026-12-11 23:59:59 America/Los_Angeles`. The code treats
the deadline as inclusive through that second and rejects an operation when
`now > deadline`. Environment overrides are supported for test installations.

Pending confirmation expires after 48 hours or at the team-registration deadline,
whichever comes first. That policy is shown to selected members in their DMs and
is stored as `confirmation_deadline` on the team.
