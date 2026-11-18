"""Preview or move tournament records between guilds while the bot is stopped."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

import asyncpg
from dotenv import load_dotenv


TABLES = ("teams", "substitutes", "audit_events", "settings")


async def migrate(args: argparse.Namespace) -> None:
    if args.from_guild <= 0 or args.to_guild <= 0 or args.from_guild == args.to_guild:
        raise ValueError("Source and destination must be different positive server IDs")
    load_dotenv(args.env_file)
    url = os.environ.get("DATABASE_URL", "").removeprefix("DATABASE_URL=").strip()
    if not url.startswith(("postgresql://", "postgres://")):
        raise ValueError("Configure the existing PostgreSQL DATABASE_URL in the env file")
    con = await asyncpg.connect(url)
    try:
        async with con.transaction():
            # Block concurrent writes for the count checks and the entire move.
            await con.execute(
                "LOCK TABLE " + ", ".join("vgna_tourney." + table for table in TABLES)
                + " IN EXCLUSIVE MODE"
            )
            counts = {}
            for table in TABLES:
                counts[table] = {
                    "source": await con.fetchval(
                        f"SELECT COUNT(*) FROM vgna_tourney.{table} WHERE guild_id=$1",
                        str(args.from_guild),
                    ),
                    "destination": await con.fetchval(
                        f"SELECT COUNT(*) FROM vgna_tourney.{table} WHERE guild_id=$1",
                        str(args.to_guild),
                    ),
                }
            print(json.dumps(counts, indent=2))
            if any(item["destination"] for item in counts.values()):
                raise ValueError("Destination already has records; refusing to merge or overwrite them")
            if not any(item["source"] for item in counts.values()):
                raise ValueError("No source records found; verify the old server ID and database")
            if not args.apply:
                print("Preview only. Stop the bot and rerun with --apply to move these records.")
                return
            for table in TABLES:
                if table == "settings":
                    # Discord messages cannot move between servers. Leave the old
                    # panel ID behind so startup creates a panel in the new channel.
                    await con.execute(
                        "UPDATE vgna_tourney.settings SET guild_id=$2 "
                        "WHERE guild_id=$1 AND key <> 'registration_panel_message_id'",
                        str(args.from_guild), str(args.to_guild),
                    )
                else:
                    await con.execute(
                        f"UPDATE vgna_tourney.{table} SET guild_id=$2 WHERE guild_id=$1",
                        str(args.from_guild), str(args.to_guild),
                    )
            # Team IDs remain unchanged, preserving members, approvals and DM buttons.
            await con.execute(
                "INSERT INTO vgna_tourney.audit_events (guild_id,event_type,payload,created_at) "
                "VALUES ($1,'guild_migrated',$2::jsonb,now())",
                str(args.to_guild), json.dumps({"from_guild_id": str(args.from_guild)}),
            )
        print("Migration committed. Start the bot with the new server and channel settings.")
    finally:
        await con.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from-guild", type=int, required=True)
    parser.add_argument("--to-guild", type=int, required=True)
    parser.add_argument("--env-file", type=Path, default=Path(__file__).resolve().parents[1] / ".env")
    parser.add_argument("--apply", action="store_true")
    asyncio.run(migrate(parser.parse_args()))
