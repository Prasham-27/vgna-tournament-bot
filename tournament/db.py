"""PostgreSQL access for the namespaced tournament registration schema."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

try:
    import asyncpg
except ImportError:  # Keeps pure-rule tests importable without production dependencies.
    asyncpg = None  # type: ignore[assignment]

from .domain import capacity_is_open, confirmation_deadline, deadline_is_open

log = logging.getLogger(__name__)
MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"


def _as_dict(row: Any) -> dict[str, Any] | None:
    return None if row is None else dict(row)


class TournamentDatabase:
    def __init__(self, database_url: str):
        self.database_url = database_url
        self.pool = None

    async def connect(self) -> None:
        if asyncpg is None:
            raise RuntimeError("asyncpg is required to run the tournament bot")
        self.pool = await asyncpg.create_pool(self.database_url, min_size=1, max_size=5, command_timeout=30)
        await self.run_migrations()

    async def close(self) -> None:
        if self.pool is not None:
            await self.pool.close()
            self.pool = None

    def _require_pool(self):
        if self.pool is None:
            raise RuntimeError("tournament database is not connected")
        return self.pool

    async def run_migrations(self) -> None:
        pool = self._require_pool()
        async with pool.acquire() as con:
            await con.execute("CREATE SCHEMA IF NOT EXISTS vgna_tourney")
            await con.execute(
                "CREATE TABLE IF NOT EXISTS vgna_tourney.schema_migrations "
                "(version TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL)"
            )
            applied = {
                row["version"]
                for row in await con.fetch("SELECT version FROM vgna_tourney.schema_migrations")
            }
            for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
                version = path.name
                if version in applied:
                    continue
                sql = path.read_text(encoding="utf-8")
                async with con.transaction():
                    await con.execute(sql)
                    await con.execute(
                        "INSERT INTO vgna_tourney.schema_migrations(version, applied_at) VALUES($1,$2)",
                        version,
                        datetime.now(timezone.utc),
                    )
                log.info("migration_applied", extra={"migration": version})

    @staticmethod
    async def _audit(
        con,
        guild_id: int | str,
        event_type: str,
        *,
        actor_user_id: int | str | None = None,
        team_id: int | None = None,
        subject_user_id: int | str | None = None,
        idempotency_key: str | None = None,
        payload: dict[str, Any] | None = None,
        now: datetime,
    ) -> None:
        await con.execute(
            """INSERT INTO vgna_tourney.audit_events
               (guild_id,event_type,actor_user_id,team_id,subject_user_id,idempotency_key,payload,created_at)
               VALUES($1,$2,$3,$4,$5,$6,$7::jsonb,$8)""",
            str(guild_id),
            event_type,
            None if actor_user_id is None else str(actor_user_id),
            team_id,
            None if subject_user_id is None else str(subject_user_id),
            idempotency_key,
            json.dumps(payload or {}),
            now,
        )

    async def get_setting(self, guild_id: int, key: str) -> str | None:
        pool = self._require_pool()
        async with pool.acquire() as con:
            return await con.fetchval(
                "SELECT value FROM vgna_tourney.settings WHERE guild_id=$1 AND key=$2",
                str(guild_id), key
            )

    async def set_setting(self, guild_id: int, key: str, value: str, now: datetime | None = None) -> None:
        pool = self._require_pool()
        async with pool.acquire() as con:
            await con.execute(
                """INSERT INTO vgna_tourney.settings(guild_id,key,value,updated_at)
                   VALUES($1,$2,$3,$4)
                   ON CONFLICT(guild_id,key) DO UPDATE SET value=EXCLUDED.value,updated_at=EXCLUDED.updated_at""",
                str(guild_id), key, value, now or datetime.now(timezone.utc)
            )

    async def create_team(
        self,
        *,
        guild_id: int,
        team_name: str,
        registrar_user_id: int,
        registrar_display_name: str,
        members: Iterable[tuple[int, str]],
        now: datetime,
        team_deadline: datetime,
        max_teams: int = 8,
        confirmation_ttl_hours: int = 48,
        idempotency_key: str,
    ) -> dict[str, Any]:
        member_rows = [(str(uid), str(name)) for uid, name in members]
        if len(member_rows) != 5 or len({uid for uid, _ in member_rows}) != 5:
            raise ValueError("team creation requires five distinct members")
        normalized = " ".join(team_name.strip().split()).casefold()
        if not normalized:
            raise ValueError("team name is required")
        pool = self._require_pool()
        async with pool.acquire() as con:
            async with con.transaction():
                await con.execute("SELECT pg_advisory_xact_lock(hashtext($1))", "vgna_tourney:team_capacity")
                existing = await con.fetchrow(
                    "SELECT id FROM vgna_tourney.teams WHERE guild_id=$1 AND idempotency_key=$2",
                    str(guild_id), idempotency_key
                )
                if existing:
                    return {"id": int(existing["id"]), "idempotent": True}
                if not deadline_is_open(now, team_deadline):
                    raise ValueError("team registration is closed")
                count = await con.fetchval(
                    """SELECT COUNT(*) FROM vgna_tourney.teams
                       WHERE guild_id=$1 AND status IN ('pending_confirmation','confirmed')""",
                    str(guild_id)
                )
                if not capacity_is_open(int(count), max_teams):
                    raise ValueError("the maximum of eight active teams has been reached")
                occupied = await con.fetchval(
                    """SELECT user_id FROM vgna_tourney.team_members
                       WHERE user_id = ANY($1::text[]) AND membership_state IN ('active','approved') LIMIT 1""",
                    [uid for uid, _ in member_rows]
                )
                if occupied:
                    raise ValueError(f"player {occupied} is already pending or confirmed on another team")
                try:
                    team_id = await con.fetchval(
                        """INSERT INTO vgna_tourney.teams
                           (guild_id,team_name,team_name_normalized,registrar_user_id,registrar_display_name,
                            status,idempotency_key,created_at,confirmation_deadline)
                           VALUES($1,$2,$3,$4,$5,'pending_confirmation',$6,$7,$8) RETURNING id""",
                        str(guild_id), team_name.strip(), normalized, str(registrar_user_id), registrar_display_name,
                        idempotency_key, now, confirmation_deadline(now, team_deadline, confirmation_ttl_hours)
                    )
                except Exception as exc:
                    if asyncpg is not None and isinstance(exc, asyncpg.UniqueViolationError):
                        raise ValueError("that team name is already active") from exc
                    raise
                for uid, display_name in member_rows:
                    await con.execute(
                        """INSERT INTO vgna_tourney.team_members
                           (team_id,user_id,display_name,membership_state,joined_at)
                           VALUES($1,$2,$3,'active',$4)""",
                        team_id, uid, display_name, now
                    )
                    await con.execute(
                        "INSERT INTO vgna_tourney.confirmations(team_id,user_id) VALUES($1,$2)", team_id, uid
                    )
                    await self._audit(
                        con, guild_id, "team_member_added", actor_user_id=registrar_user_id,
                        team_id=team_id, subject_user_id=uid, idempotency_key=idempotency_key,
                        payload={"display_name": display_name}, now=now
                    )
                await self._audit(
                    con, guild_id, "team_submitted", actor_user_id=registrar_user_id,
                    team_id=team_id, idempotency_key=idempotency_key,
                    payload={"member_count": 5}, now=now
                )
            return {"id": int(team_id), "idempotent": False}

    async def get_team(self, team_id: int) -> dict[str, Any] | None:
        pool = self._require_pool()
        async with pool.acquire() as con:
            return await self._get_team(con, team_id)

    async def _get_team(self, con, team_id: int) -> dict[str, Any] | None:
        row = await con.fetchrow("SELECT * FROM vgna_tourney.teams WHERE id=$1", team_id)
        if row is None:
            return None
        result = dict(row)
        result["members"] = [
            dict(item) for item in await con.fetch(
                "SELECT * FROM vgna_tourney.team_members WHERE team_id=$1 ORDER BY user_id", team_id
            )
        ]
        result["confirmations"] = [
            dict(item) for item in await con.fetch(
                "SELECT * FROM vgna_tourney.confirmations WHERE team_id=$1 ORDER BY user_id", team_id
            )
        ]
        return result

    async def pending_teams(self, guild_id: int) -> list[dict[str, Any]]:
        pool = self._require_pool()
        async with pool.acquire() as con:
            rows = await con.fetch(
                "SELECT id FROM vgna_tourney.teams WHERE guild_id=$1 AND status='pending_confirmation'",
                str(guild_id)
            )
            teams = []
            for row in rows:
                team = await self._get_team(con, int(row["id"]))
                if team:
                    teams.append(team)
            return teams

    async def record_dm_delivery(self, team_id: int, user_id: int, *, ok: bool, failure_code: str | None,
                                 now: datetime) -> None:
        pool = self._require_pool()
        async with pool.acquire() as con:
            status = "sent" if ok else "failed"
            await con.execute(
                """UPDATE vgna_tourney.confirmations
                   SET dm_status=$3,dm_failure_code=$4 WHERE team_id=$1 AND user_id=$2""",
                team_id, str(user_id), status, failure_code
            )
            await self._audit(
                con, (await con.fetchval("SELECT guild_id FROM vgna_tourney.teams WHERE id=$1", team_id)),
                "confirmation_dm_sent" if ok else "confirmation_dm_failed", team_id=team_id,
                subject_user_id=user_id, payload={"failure_code": failure_code} if failure_code else {}, now=now
            )

    async def respond_confirmation(
        self, *, team_id: int, user_id: int, decision: str, private_reason: str | None, now: datetime
    ) -> dict[str, Any]:
        if decision not in {"approve", "decline"}:
            raise ValueError("invalid confirmation decision")
        pool = self._require_pool()
        async with pool.acquire() as con:
            async with con.transaction():
                await con.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"vgna_tourney:team:{team_id}")
                team = await con.fetchrow("SELECT * FROM vgna_tourney.teams WHERE id=$1 FOR UPDATE", team_id)
                confirmation = await con.fetchrow(
                    "SELECT * FROM vgna_tourney.confirmations WHERE team_id=$1 AND user_id=$2 FOR UPDATE",
                    team_id, str(user_id)
                )
                if team is None or confirmation is None:
                    return {"outcome": "not_found", "team": None}
                if confirmation["status"] != "pending" or team["status"] != "pending_confirmation":
                    return {"outcome": "idempotent", "team": await self._get_team(con, team_id)}
                guild_id = team["guild_id"]
                if decision == "decline":
                    await con.execute(
                        """UPDATE vgna_tourney.confirmations
                           SET status='declined',responded_at=$3,private_reason=$4
                           WHERE team_id=$1 AND user_id=$2""",
                        team_id, str(user_id), now, private_reason
                    )
                    await con.execute(
                        """UPDATE vgna_tourney.team_members SET membership_state='declined',left_at=$3
                           WHERE team_id=$1 AND user_id=$2""", team_id, str(user_id), now
                    )
                    await con.execute(
                        """UPDATE vgna_tourney.team_members SET membership_state='inactive',left_at=$2
                           WHERE team_id=$1 AND user_id<>$3 AND membership_state IN ('active','approved')""",
                        team_id, now, str(user_id)
                    )
                    for member_row in await con.fetch(
                        "SELECT user_id FROM vgna_tourney.team_members WHERE team_id=$1 AND user_id<>$2",
                        team_id, str(user_id)
                    ):
                        await self._audit(
                            con, guild_id, "team_membership_deactivated", team_id=team_id,
                            subject_user_id=member_row["user_id"], payload={"reason": "member_declined"}, now=now
                        )
                    await con.execute(
                        "UPDATE vgna_tourney.teams SET status='confirmation_failed',private_failure_code='member_declined' WHERE id=$1",
                        team_id
                    )
                    await self._audit(con, guild_id, "member_declined", team_id=team_id, subject_user_id=user_id,
                                      payload={"has_private_reason": bool(private_reason)}, now=now)
                    await self._audit(con, guild_id, "team_confirmation_failed", team_id=team_id,
                                      payload={"failure_code": "member_declined"}, now=now)
                    return {"outcome": "declined", "team": await self._get_team(con, team_id)}
                await con.execute(
                    "UPDATE vgna_tourney.confirmations SET status='approved',responded_at=$3 WHERE team_id=$1 AND user_id=$2",
                    team_id, str(user_id), now
                )
                await con.execute(
                    "UPDATE vgna_tourney.team_members SET membership_state='approved' WHERE team_id=$1 AND user_id=$2",
                    team_id, str(user_id)
                )
                await self._audit(con, guild_id, "member_approved", team_id=team_id, subject_user_id=user_id, now=now)
                statuses = await con.fetch(
                    "SELECT status FROM vgna_tourney.confirmations WHERE team_id=$1", team_id
                )
                if all(row["status"] == "approved" for row in statuses) and len(statuses) == 5:
                    await con.execute(
                        "UPDATE vgna_tourney.teams SET status='confirmed',confirmed_at=$2 WHERE id=$1", team_id, now
                    )
                    await self._audit(con, guild_id, "team_confirmed", team_id=team_id, now=now)
                    member_ids = [row["user_id"] for row in await con.fetch(
                        "SELECT user_id FROM vgna_tourney.team_members WHERE team_id=$1", team_id
                    )]
                    for member_id in member_ids:
                        changed = await con.execute(
                            """UPDATE vgna_tourney.substitutes
                               SET active=FALSE,left_at=$3,change_reason='confirmed_team'
                               WHERE guild_id=$1 AND user_id=$2 AND active""",
                            guild_id, member_id, now
                        )
                        if changed.endswith("1"):
                            await self._audit(con, guild_id, "substitute_auto_removed", team_id=team_id,
                                              subject_user_id=member_id, payload={"reason": "confirmed_team"}, now=now)
                    return {"outcome": "confirmed", "team": await self._get_team(con, team_id)}
                return {"outcome": "approved", "team": await self._get_team(con, team_id)}

    async def expire_pending(self, *, now: datetime) -> list[dict[str, Any]]:
        pool = self._require_pool()
        expired: list[dict[str, Any]] = []
        async with pool.acquire() as con:
            rows = await con.fetch(
                "SELECT id FROM vgna_tourney.teams WHERE status='pending_confirmation' AND confirmation_deadline < $1",
                now
            )
            for row in rows:
                async with con.transaction():
                    team = await con.fetchrow("SELECT * FROM vgna_tourney.teams WHERE id=$1 FOR UPDATE", row["id"])
                    if team is None or team["status"] != "pending_confirmation" or team["confirmation_deadline"] >= now:
                        continue
                    await con.execute("UPDATE vgna_tourney.teams SET status='expired',private_failure_code='expired' WHERE id=$1", row["id"])
                    await con.execute(
                        """UPDATE vgna_tourney.team_members SET membership_state='inactive',left_at=$2
                           WHERE team_id=$1 AND membership_state IN ('active','approved')""", row["id"], now
                    )
                    await self._audit(con, team["guild_id"], "team_confirmation_expired", team_id=row["id"], now=now)
                    for member_row in await con.fetch(
                        "SELECT user_id FROM vgna_tourney.team_members WHERE team_id=$1", row["id"]
                    ):
                        await self._audit(
                            con, team["guild_id"], "team_membership_deactivated", team_id=row["id"],
                            subject_user_id=member_row["user_id"], payload={"reason": "expired"}, now=now
                        )
                snapshot = await self._get_team(con, int(row["id"]))
                if snapshot:
                    expired.append(snapshot)
        return expired

    async def join_substitute(self, *, guild_id: int, user_id: int, display_name: str, now: datetime,
                              substitute_deadline: datetime, actor_user_id: int | None = None) -> dict[str, Any]:
        pool = self._require_pool()
        async with pool.acquire() as con:
            async with con.transaction():
                await con.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"vgna_tourney:substitute:{user_id}")
                if not deadline_is_open(now, substitute_deadline):
                    raise ValueError("substitute-pool registration is closed")
                if await con.fetchval(
                    """SELECT 1 FROM vgna_tourney.team_members tm JOIN vgna_tourney.teams t ON t.id=tm.team_id
                       WHERE t.guild_id=$1 AND tm.user_id=$2 AND t.status='confirmed'
                         AND tm.membership_state IN ('active','approved') LIMIT 1""",
                    str(guild_id), str(user_id)
                ):
                    raise ValueError("confirmed team members cannot join the substitute pool")
                existing = await con.fetchrow(
                    "SELECT active FROM vgna_tourney.substitutes WHERE guild_id=$1 AND user_id=$2 FOR UPDATE",
                    str(guild_id), str(user_id)
                )
                if existing and existing["active"]:
                    return {"outcome": "already_active"}
                await con.execute(
                    """INSERT INTO vgna_tourney.substitutes(guild_id,user_id,display_name,active,joined_at,left_at,change_reason)
                       VALUES($1,$2,$3,TRUE,$4,NULL,NULL)
                       ON CONFLICT(guild_id,user_id) DO UPDATE SET display_name=EXCLUDED.display_name,
                         active=TRUE,joined_at=EXCLUDED.joined_at,left_at=NULL,change_reason=NULL""",
                    str(guild_id), str(user_id), display_name, now
                )
                await self._audit(con, guild_id, "substitute_joined", actor_user_id=actor_user_id or user_id,
                                  subject_user_id=user_id, now=now)
                return {"outcome": "joined"}

    async def leave_substitute(self, *, guild_id: int, user_id: int, now: datetime,
                               reason: str = "member_left") -> dict[str, Any]:
        pool = self._require_pool()
        async with pool.acquire() as con:
            async with con.transaction():
                row = await con.fetchrow(
                    "SELECT active FROM vgna_tourney.substitutes WHERE guild_id=$1 AND user_id=$2 FOR UPDATE",
                    str(guild_id), str(user_id)
                )
                if not row or not row["active"]:
                    return {"outcome": "already_left"}
                await con.execute(
                    "UPDATE vgna_tourney.substitutes SET active=FALSE,left_at=$3,change_reason=$4 WHERE guild_id=$1 AND user_id=$2",
                    str(guild_id), str(user_id), now, reason
                )
                await self._audit(con, guild_id, "substitute_left", actor_user_id=user_id, subject_user_id=user_id,
                                  payload={"reason": reason}, now=now)
                return {"outcome": "left"}

    async def withdraw_team(self, *, guild_id: int, team_id: int, actor_user_id: int, now: datetime) -> dict[str, Any]:
        pool = self._require_pool()
        async with pool.acquire() as con:
            async with con.transaction():
                await con.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"vgna_tourney:team:{team_id}")
                team = await con.fetchrow("SELECT * FROM vgna_tourney.teams WHERE id=$1 AND guild_id=$2 FOR UPDATE", team_id, str(guild_id))
                if team is None:
                    return {"outcome": "not_found"}
                member_ids = [row["user_id"] for row in await con.fetch(
                    "SELECT user_id FROM vgna_tourney.team_members WHERE team_id=$1", team_id
                )]
                if team["status"] == "withdrawn":
                    return {"outcome": "already_withdrawn", "team": await self._get_team(con, team_id), "member_ids": member_ids}
                await con.execute(
                    """UPDATE vgna_tourney.teams SET status='withdrawn',withdrawn_at=$2,withdrawal_actor_user_id=$3
                       WHERE id=$1""", team_id, now, str(actor_user_id)
                )
                await con.execute(
                    """UPDATE vgna_tourney.team_members SET membership_state='inactive',left_at=$2
                       WHERE team_id=$1 AND membership_state IN ('active','approved')""", team_id, now
                )
                await self._audit(con, guild_id, "team_withdrawn", actor_user_id=actor_user_id, team_id=team_id,
                                  payload={"previous_status": team["status"]}, now=now)
                for member_id in member_ids:
                    await self._audit(con, guild_id, "team_membership_deactivated", actor_user_id=actor_user_id,
                                      team_id=team_id, subject_user_id=member_id, payload={"reason": "withdrawn"}, now=now)
                return {"outcome": "withdrawn", "team": await self._get_team(con, team_id), "member_ids": member_ids}

    async def panel_snapshot(self, guild_id: int) -> dict[str, Any]:
        pool = self._require_pool()
        async with pool.acquire() as con:
            confirmed_count = await con.fetchval(
                "SELECT COUNT(*) FROM vgna_tourney.teams WHERE guild_id=$1 AND status='confirmed'", str(guild_id)
            )
            pending_count = await con.fetchval(
                "SELECT COUNT(*) FROM vgna_tourney.teams WHERE guild_id=$1 AND status='pending_confirmation'", str(guild_id)
            )
            substitute_count = await con.fetchval(
                "SELECT COUNT(*) FROM vgna_tourney.substitutes WHERE guild_id=$1 AND active", str(guild_id)
            )
            teams = []
            rows = await con.fetch(
                "SELECT * FROM vgna_tourney.teams WHERE guild_id=$1 AND status='confirmed' ORDER BY confirmed_at,id",
                str(guild_id)
            )
            for row in rows:
                item = dict(row)
                item["members"] = [dict(member) for member in await con.fetch(
                    "SELECT user_id,display_name FROM vgna_tourney.team_members WHERE team_id=$1 ORDER BY user_id",
                    row["id"]
                )]
                teams.append(item)
            return {
                "confirmed_count": int(confirmed_count or 0),
                "pending_count": int(pending_count or 0),
                "substitute_count": int(substitute_count or 0),
                "teams": teams,
            }

    async def list_teams(self, guild_id: int) -> list[dict[str, Any]]:
        pool = self._require_pool()
        async with pool.acquire() as con:
            return [dict(row) for row in await con.fetch(
                "SELECT id,team_name,status,registrar_display_name,created_at FROM vgna_tourney.teams WHERE guild_id=$1 ORDER BY id",
                str(guild_id)
            )]

