# Read-only VGNA reference notes

Before implementation, the existing `/Users/prashamsheth/Documents/5v5draftbot`
repository was inspected without changing it. Relevant conventions were:

- Python 3.12-style `discord.py` 2.x bot lifecycle with `setup_hook`/`on_ready`
  and persistent `discord.ui.View` controls.
- PostgreSQL access through `asyncpg` and `DATABASE_URL`.
- Stable Discord account IDs are stored as text in VGNA tables; display names are
  refreshed snapshots, not permanent identities.
- Existing startup migrations use repeat-safe `CREATE TABLE IF NOT EXISTS` and a
  schema-state ledger.
- Existing organizer checks use Discord permissions and configured IDs.

This bot follows those conventions at its boundary but intentionally does not
import the VGNA bot, reuse its tables, copy its `.git` or runtime data, or depend
on its telemetry/replay code. Its migration ledger and all registration tables
are isolated in `vgna_tourney`.
