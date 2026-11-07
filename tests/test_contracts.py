from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_schema_is_namespaced_and_does_not_touch_existing_vgna_tables():
    sql = (ROOT / "migrations/001_initial.sql").read_text()
    assert "CREATE SCHEMA IF NOT EXISTS vgna_tourney" in sql
    assert "CREATE TABLE IF NOT EXISTS stats" not in sql
    assert "CREATE TABLE IF NOT EXISTS players" not in sql
    assert "audit_events" in sql


def test_restart_recovery_uses_durable_panel_and_pending_teams():
    source = (ROOT / "bot.py").read_text()
    assert "registration_panel_message_id" in source
    assert "pending_teams" in source
    assert "ConfirmationView" in source


def test_registration_surface_contains_required_public_rules():
    source = (ROOT / "tournament/panel.py").read_text()
    assert "Confirmed" in source
    assert "Pending confirmation" in source
    assert "Substitute pool" in source
    assert "withdraw" in source


def test_registration_database_contract_covers_retry_safe_and_private_paths():
    source = (ROOT / "tournament/db.py").read_text()
    assert "pg_advisory_xact_lock" in source
    assert "confirmation_dm_failed" in source
    assert "substitute_auto_removed" in source
    assert "already_withdrawn" in source
    assert "ON CONFLICT" in source

