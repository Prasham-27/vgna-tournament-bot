# VGNA Tournament Bot — architecture

## Scope

This repository is a separate Discord bot process for tournament registration only.
The first phase owns team registration, five-member confirmation, the substitute
pool, organizer withdrawals, the registration panel, and their audit history. It
does not run matchmaking, brackets, tournament games, replay ingestion, or VGNA
Client telemetry.

The existing VGNA bot remains read-only reference material. This bot never imports
its Python modules or writes to its tables. The only shared operational input is
the PostgreSQL connection string supplied through `DATABASE_URL`.

## Components

- `bot.py` — Discord lifecycle, guild/channel setup, persistent views, modal and
  select handlers, DM delivery, and panel refresh.
- `tournament/config.py` — environment-backed configuration and Pacific-time
  deadline calculation.
- `tournament/domain.py` — dependency-free validation and confirmation state
  transitions; this is the main unit-test surface.
- `tournament/db.py` — `asyncpg` pool, idempotent migration runner, transactions,
  and registration operations. Every write is scoped to `vgna_tourney`.
- `tournament/panel.py` — public panel copy and rendering. It intentionally only
  includes public team/member information and aggregate counts.
- `migrations/001_initial.sql` — repeat-safe namespaced PostgreSQL schema.

## Startup and restart recovery

1. Load and validate environment variables, including the configured IANA timezone.
2. Connect to PostgreSQL and apply migrations in `vgna_tourney.schema_migrations`.
3. Validate the configured guild and required permissions.
4. Adopt or create the exact `Tourney Controls` category and `registrations`
   channel. Persist the panel message ID in `vgna_tourney.settings`; edit that
   message in place on restart and recreate it only when it was deleted.
5. Register the panel view and one confirmation view for every still-pending team.
   Custom IDs are deterministic, so a restart does not create duplicate actions.
6. Expire confirmation windows and retry panel rendering in background tasks.

The panel message ID and pending team rows are durable. A restart therefore loses
no registration or confirmation state and never needs to repost a second panel.

## Discord interaction design

Discord modals cannot reliably provide five server-member selectors in the
discord.py API. The team button therefore opens an ephemeral guided form: a team
name modal followed by five native `UserSelect` components. The server-side
handler re-fetches each selected ID from the configured guild and rejects users
who are absent, bots, duplicated, or already active elsewhere. Discord UI input
is treated as untrusted input.

Confirmation occurs only in private DMs. Each selected member receives the team
name, the other selected members, the registrar, the expiration timestamp, and
Approve/Decline buttons. A button press is authorized against the member ID in
the database, not against the displayed label. Approval is an idempotent update;
the fifth approval atomically transitions the team to `confirmed`.

If any DM cannot be opened, the team remains unconfirmed and is marked with a
private delivery issue for the registrar. A decline or expiration transitions the
team to `confirmation_failed`/`expired`, deactivates its pending memberships, and
keeps all history. Private reasons are never put in the public panel.

## Concurrency and idempotency

Team submissions run in a PostgreSQL transaction and acquire a transaction-scoped
advisory lock for the tournament registration capacity. The transaction checks
the active team count, validates active member uniqueness, inserts the team and
all five memberships, and writes audit rows before commit. This prevents two
simultaneous submissions from consuming the same eighth slot or player.

The database is the source of truth. Partial unique indexes prevent two active
teams from owning one Discord user or two active substitute rows for one user.
Repeated team submissions with the same idempotency key return the original team;
repeated approval, decline, join, leave, and withdrawal operations return the
already-applied result without adding a second state change. Audit events remain
append-only and include an idempotency key where one exists.

## Privacy and logging

The public panel shows confirmed teams, five members per confirmed team, pending
and substitute counts, deadlines, registration instructions, and the withdrawal
contact. It does not show private DM failures, decline reasons, or account data
beyond Discord display names for confirmed rosters. Structured logs contain event
names, IDs, statuses, and error classes; they never contain tokens, connection
strings, DM bodies, or private response text.

## Future VGNA integration boundary

Tournament tables store stable Discord user IDs as text and never use display
names as keys. A future `vgna_tourney.matches` table can reference
`vgna_tourney.teams.id` and `vgna_tourney.team_members.user_id`, then store a VGNA match
identifier once a game is created. Player statistics can join on the stable
Discord ID to the existing VGNA `players.user_id`/`stats.user_id` convention, or
through an explicit mapping layer if VGNA account merging is introduced. That
integration must be a later migration and must not make registration depend on
VGNA telemetry, replay rows, or display-name matching.
