# VGNA Tournament Bot

Standalone Discord registration bot for the VGNA tournament. This first phase
supports only five-player team registration, private five-member confirmation,
the substitute pool, the public `#registrations` panel, and organizer team
withdrawals. It does not implement matchmaking, brackets, tournament games,
replays, or VGNA Client telemetry.

## Run locally

1. Create a separate Discord application/bot and invite it only to the tournament
   guild with the `bot` and `applications.commands` scopes.
2. Grant it View Channel, Send Messages, Embed Links, and Read Message History. Enable the Server Members intent in the Discord developer
   portal; it is required for validated member selection.
3. Copy `.env.example` to `.env` and fill in `DISCORD_TOKEN`, `DATABASE_URL`,
   `TOURNAMENT_GUILD_ID`, and `TOURNAMENT_REGISTRATION_CHANNEL_ID`. Optionally set `LYNX_USER_ID` or organizer IDs.
4. Install dependencies with `python -m pip install -r requirements-dev.txt`.
5. Start with `python bot.py`.

The bot runs idempotent migrations under the dedicated `vgna_tourney` schema,
uses the configured existing registration channel and edits one durable
registration panel across restarts. It never writes to the existing VGNA tables.

Deadlines default to November 30, 2026 at 23:59:59 Pacific Time for teams and
December 11, 2026 at 23:59:59 Pacific Time for substitutes. `TOURNAMENT_TIMEZONE`
defaults to `America/Los_Angeles`; deadline comparisons use aware timestamps so
PST/PDT transitions are handled by the IANA timezone database.

See [the architecture](docs/ARCHITECTURE.md), [data model](docs/DATA_MODEL.md),
and [registration flow](docs/REGISTRATION_FLOW.md) before operating the bot.

## Move to another Discord server

Invite the bot to the new server, then update `.env`:

- `TOURNAMENT_GUILD_ID`: the new server ID.
- `TOURNAMENT_REGISTRATION_CHANNEL_ID`: the existing text channel ID where the panel belongs.
- `LYNX_USER_ID`, `TOURNAMENT_ORGANIZER_USER_ID`, or `TOURNAMENT_ORGANIZER_USER_IDS`: update if organizers changed. User IDs remain the same across servers.
- Review `TOURNAMENT_TIMEZONE`, `TEAM_REGISTRATION_DEADLINE`, and `SUBSTITUTE_REGISTRATION_DEADLINE` if the schedule changed.

Keep `DISCORD_TOKEN` and `DATABASE_URL` when using the same bot and database.
Grant View Channel, Send Messages, Embed Links, and Read Message History in the
chosen channel. The bot never creates or moves channels or categories and does
not require Manage Channels.
An invalid configured channel stops setup instead of creating a different channel.
Restart the bot to apply changes and create or recover the panel in that channel.

Registrations and panel settings are scoped to the server ID. A new server starts
with an empty registration list; existing records remain under the old server ID.
Moving existing registrations requires a separate database migration.

To carry registrations over, stop the existing bot and take a database backup.
Use the same database connection for both the migration and the new deployment.
Preview the move first (requires the dependencies installed above):

```sh
python scripts/migrate_guild.py --from-guild OLD_SERVER_ID --to-guild NEW_SERVER_ID
```

Repeat with `--apply` to commit the move, then start the bot with the new settings.
The migration runs in a single transaction and refuses a destination with any
existing tournament records. It preserves team IDs, members, confirmations, and
substitutes, and moves audit history. It leaves the old panel message ID behind;
the bot creates a panel in the configured destination channel. The old Discord
panel itself remains in the old server. Pending confirmations keep their original
deadlines and DM buttons; members should join the new server before using them.

## Tests

Run `python -m pytest`. The unit suite covers deadline/timezone behavior,
capacity, member validation, confirmation transitions, idempotency, substitute
removal, withdrawals, DM delivery failures, restart recovery, and concurrency
contracts without requiring a live Discord token. A PostgreSQL-backed smoke test
can be added in deployment CI with a disposable database.

