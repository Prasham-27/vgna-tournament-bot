# VGNA Tournament Bot

Standalone Discord registration bot for the VGNA tournament. This first phase
supports only five-player team registration, private five-member confirmation,
the substitute pool, the public `#registrations` panel, and organizer team
withdrawals. It does not implement matchmaking, brackets, tournament games,
replays, or VGNA Client telemetry.

## Run locally

1. Create a separate Discord application/bot and invite it only to the tournament
   guild with the `bot` and `applications.commands` scopes.
2. Grant it View Channel, Send Messages, Embed Links, Read Message History, and
   Manage Channels. Enable the Server Members intent in the Discord developer
   portal; it is required for validated member selection.
3. Copy `.env.example` to `.env` and fill in `DISCORD_TOKEN`, `DATABASE_URL`, and
   `TOURNAMENT_GUILD_ID`. Optionally set `LYNX_USER_ID` or organizer IDs.
4. Install dependencies with `python -m pip install -r requirements-dev.txt`.
5. Start with `python bot.py`.

The bot runs idempotent migrations under the dedicated `vgna_tourney` schema,
adopts or creates `Tourney Controls` / `registrations`, and edits one durable
registration panel across restarts. It never writes to the existing VGNA tables.

Deadlines default to November 30, 2026 at 23:59:59 Pacific Time for teams and
December 11, 2026 at 23:59:59 Pacific Time for substitutes. `TOURNAMENT_TIMEZONE`
defaults to `America/Los_Angeles`; deadline comparisons use aware timestamps so
PST/PDT transitions are handled by the IANA timezone database.

See [the architecture](docs/ARCHITECTURE.md), [data model](docs/DATA_MODEL.md),
and [registration flow](docs/REGISTRATION_FLOW.md) before operating the bot.

## Tests

Run `python -m pytest`. The unit suite covers deadline/timezone behavior,
capacity, member validation, confirmation transitions, idempotency, substitute
removal, withdrawals, DM delivery failures, restart recovery, and concurrency
contracts without requiring a live Discord token. A PostgreSQL-backed smoke test
can be added in deployment CI with a disposable database.

