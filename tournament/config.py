"""Environment-backed configuration for the tournament bot."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


DEFAULT_TIMEZONE = "America/Los_Angeles"
DEFAULT_TEAM_DEADLINE = "2026-11-30T23:59:59"
DEFAULT_SUBSTITUTE_DEADLINE = "2026-12-11T23:59:59"
DEFAULT_CATEGORY_NAME = "Tourney Controls"


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"{name} is required")
    return value


def _optional_int(name: str) -> int | None:
    value = os.getenv(name, "").strip()
    if not value:
        return None
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a Discord integer ID") from exc


def parse_local_deadline(value: str, timezone: ZoneInfo) -> datetime:
    """Parse an ISO timestamp, assigning the configured zone when no offset exists."""
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"invalid deadline: {value!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone)
    return parsed.astimezone(timezone)


@dataclass(frozen=True)
class Settings:
    token: str
    database_url: str
    guild_id: int
    timezone_name: str = DEFAULT_TIMEZONE
    team_deadline: datetime = field(default_factory=lambda: datetime.now().astimezone())
    substitute_deadline: datetime = field(default_factory=lambda: datetime.now().astimezone())
    lynx_user_id: int | None = None
    organizer_user_ids: frozenset[int] = frozenset()
    max_teams: int = 8
    confirmation_ttl_hours: int = 48
    category_name: str = DEFAULT_CATEGORY_NAME

    @property
    def timezone(self) -> ZoneInfo:
        return ZoneInfo(self.timezone_name)

    @classmethod
    def from_env(cls) -> "Settings":
        timezone_name = os.getenv("TOURNAMENT_TIMEZONE", DEFAULT_TIMEZONE).strip() or DEFAULT_TIMEZONE
        try:
            timezone = ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"TOURNAMENT_TIMEZONE is not a valid IANA timezone: {timezone_name}") from exc
        try:
            guild_id = int(_required("TOURNAMENT_GUILD_ID"))
        except ValueError as exc:
            raise ValueError("TOURNAMENT_GUILD_ID must be a Discord integer ID") from exc
        lynx = _optional_int("LYNX_USER_ID")
        extra = set()
        organizer_values = ",".join(
            value for value in (
                os.getenv("TOURNAMENT_ORGANIZER_USER_ID", ""),
                os.getenv("TOURNAMENT_ORGANIZER_USER_IDS", ""),
            ) if value.strip()
        )
        for raw in organizer_values.split(","):
            if raw.strip():
                try:
                    extra.add(int(raw.strip()))
                except ValueError as exc:
                    raise ValueError("TOURNAMENT_ORGANIZER_USER_IDS must contain Discord integer IDs") from exc
        if lynx is not None:
            extra.add(lynx)
        database_url = _required("DATABASE_URL")
        # Be forgiving when someone pastes the full ``DATABASE_URL=...`` line
        # into the value field of the local .env template.
        if database_url.startswith("DATABASE_URL="):
            database_url = database_url.split("=", 1)[1].strip()
        if not database_url.startswith(("postgresql://", "postgres://")):
            raise ValueError("DATABASE_URL must start with postgresql:// or postgres://")
        return cls(
            token=_required("DISCORD_TOKEN"),
            database_url=database_url,
            guild_id=guild_id,
            timezone_name=timezone_name,
            team_deadline=parse_local_deadline(
                os.getenv("TEAM_REGISTRATION_DEADLINE", DEFAULT_TEAM_DEADLINE), timezone
            ),
            substitute_deadline=parse_local_deadline(
                os.getenv("SUBSTITUTE_REGISTRATION_DEADLINE", DEFAULT_SUBSTITUTE_DEADLINE), timezone
            ),
            lynx_user_id=lynx,
            organizer_user_ids=frozenset(extra),
            category_name=os.getenv("TOURNAMENT_CATEGORY_NAME", DEFAULT_CATEGORY_NAME).strip()
            or DEFAULT_CATEGORY_NAME,
        )

