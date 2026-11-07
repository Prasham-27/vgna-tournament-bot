"""Pure registration rules. Kept independent of Discord and PostgreSQL for tests."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Iterable, Mapping


def normalize_team_name(value: str) -> str:
    return " ".join(value.strip().split())


def validate_team_players(player_ids: Iterable[str], members: Mapping[str, object]) -> list[str]:
    ids = [str(value) for value in player_ids]
    errors: list[str] = []
    if len(ids) != 5:
        errors.append("A team must have exactly five players.")
    if len(set(ids)) != len(ids):
        errors.append("A player cannot be selected more than once.")
    for player_id in ids:
        member = members.get(player_id)
        if member is None:
            errors.append(f"Player {player_id} is not a current server member.")
        elif bool(getattr(member, "bot", False)):
            errors.append("Bots cannot be registered as players.")
    return list(dict.fromkeys(errors))


def deadline_is_open(now: datetime, deadline: datetime) -> bool:
    """Deadlines are inclusive through the configured deadline second."""
    return now <= deadline


def capacity_is_open(active_team_count: int, max_teams: int = 8) -> bool:
    return active_team_count < max_teams


def confirmation_deadline(registered_at: datetime, team_deadline: datetime, ttl_hours: int = 48) -> datetime:
    return min(registered_at + timedelta(hours=ttl_hours), team_deadline)


def all_approved(statuses: Iterable[str]) -> bool:
    values = list(statuses)
    return len(values) == 5 and all(value == "approved" for value in values)


def confirmation_transition(team_status: str, current_status: str, decision: str, statuses_after: Iterable[str]) -> str:
    """Return the durable team status after an idempotent confirmation action."""
    if team_status != "pending_confirmation":
        return team_status
    if current_status != "pending":
        return "confirmed" if all_approved(statuses_after) else "pending_confirmation"
    if decision == "decline":
        return "confirmation_failed"
    if decision == "approve" and all_approved(statuses_after):
        return "confirmed"
    return "pending_confirmation"

