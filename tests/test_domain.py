from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from tournament.config import parse_local_deadline
from tournament.domain import (
    all_approved,
    capacity_is_open,
    confirmation_deadline,
    confirmation_transition,
    deadline_is_open,
    validate_team_players,
)


def test_pacific_deadline_uses_iana_offsets_and_is_inclusive():
    zone = ZoneInfo("America/Los_Angeles")
    november = parse_local_deadline("2026-11-30T23:59:59", zone)
    july = parse_local_deadline("2026-07-01T23:59:59", zone)
    assert november.utcoffset() == timedelta(hours=-8)
    assert july.utcoffset() == timedelta(hours=-7)
    assert deadline_is_open(november, november)
    assert not deadline_is_open(november + timedelta(seconds=1), november)


def test_five_player_validation_rejects_wrong_count_duplicates_and_bots():
    members = {
        str(i): SimpleNamespace(bot=(i == 5)) for i in range(1, 6)
    }
    assert validate_team_players(["1", "2", "3", "4"], members)
    errors = validate_team_players(["1", "2", "2", "4", "5"], members)
    assert any("more than once" in error for error in errors)
    assert any("Bots" in error for error in errors)


def test_capacity_is_eight_team_maximum():
    assert capacity_is_open(7, 8)
    assert not capacity_is_open(8, 8)


def test_confirmation_deadline_is_capped_at_team_deadline():
    registered = datetime(2026, 11, 29, 23, 0, tzinfo=ZoneInfo("America/Los_Angeles"))
    team_deadline = datetime(2026, 11, 30, 23, 59, 59, tzinfo=ZoneInfo("America/Los_Angeles"))
    assert confirmation_deadline(registered, team_deadline) == registered + timedelta(hours=24, minutes=59, seconds=59)


def test_pending_confirmation_and_all_approval_transition():
    statuses = ["approved", "approved", "approved", "approved", "pending"]
    assert not all_approved(statuses)
    assert confirmation_transition("pending_confirmation", "pending", "approve", statuses) == "pending_confirmation"
    statuses[-1] = "approved"
    assert all_approved(statuses)
    assert confirmation_transition("pending_confirmation", "pending", "approve", statuses) == "confirmed"


def test_decline_is_terminal_and_repeated_action_is_idempotent():
    assert confirmation_transition(
        "pending_confirmation", "pending", "decline", ["pending"] * 5
    ) == "confirmation_failed"
    assert confirmation_transition(
        "confirmation_failed", "declined", "decline", ["declined"] + ["inactive"] * 4
    ) == "confirmation_failed"

