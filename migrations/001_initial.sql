CREATE SCHEMA IF NOT EXISTS vgna_tourney;

CREATE TABLE IF NOT EXISTS vgna_tourney.teams (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    guild_id TEXT NOT NULL,
    team_name TEXT NOT NULL,
    team_name_normalized TEXT NOT NULL,
    registrar_user_id TEXT NOT NULL,
    registrar_display_name TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN (
        'pending_confirmation', 'confirmed', 'confirmation_failed', 'expired', 'withdrawn'
    )),
    idempotency_key TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    confirmation_deadline TIMESTAMPTZ NOT NULL,
    confirmed_at TIMESTAMPTZ,
    withdrawn_at TIMESTAMPTZ,
    withdrawal_actor_user_id TEXT,
    private_failure_code TEXT,
    UNIQUE (guild_id, idempotency_key)
);

CREATE UNIQUE INDEX IF NOT EXISTS tourney_active_team_name_idx
    ON vgna_tourney.teams (guild_id, team_name_normalized)
    WHERE status IN ('pending_confirmation', 'confirmed');

CREATE INDEX IF NOT EXISTS tourney_teams_status_idx
    ON vgna_tourney.teams (guild_id, status, created_at);

CREATE TABLE IF NOT EXISTS vgna_tourney.team_members (
    team_id BIGINT NOT NULL REFERENCES vgna_tourney.teams(id),
    user_id TEXT NOT NULL,
    display_name TEXT NOT NULL,
    membership_state TEXT NOT NULL CHECK (membership_state IN (
        'active', 'approved', 'declined', 'inactive'
    )),
    joined_at TIMESTAMPTZ NOT NULL,
    left_at TIMESTAMPTZ,
    PRIMARY KEY (team_id, user_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS tourney_active_member_idx
    ON vgna_tourney.team_members (user_id)
    WHERE membership_state IN ('active', 'approved');

CREATE INDEX IF NOT EXISTS tourney_team_members_team_idx
    ON vgna_tourney.team_members (team_id, membership_state);

CREATE TABLE IF NOT EXISTS vgna_tourney.confirmations (
    team_id BIGINT NOT NULL REFERENCES vgna_tourney.teams(id),
    user_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'approved', 'declined')),
    responded_at TIMESTAMPTZ,
    private_reason TEXT,
    dm_status TEXT NOT NULL DEFAULT 'not_attempted' CHECK (dm_status IN (
        'not_attempted', 'sent', 'failed'
    )),
    dm_failure_code TEXT,
    PRIMARY KEY (team_id, user_id)
);

CREATE TABLE IF NOT EXISTS vgna_tourney.substitutes (
    guild_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    display_name TEXT NOT NULL,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    joined_at TIMESTAMPTZ NOT NULL,
    left_at TIMESTAMPTZ,
    change_reason TEXT,
    PRIMARY KEY (guild_id, user_id)
);

CREATE INDEX IF NOT EXISTS tourney_active_substitutes_idx
    ON vgna_tourney.substitutes (guild_id, active, joined_at);

CREATE TABLE IF NOT EXISTS vgna_tourney.audit_events (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    guild_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    actor_user_id TEXT,
    team_id BIGINT,
    subject_user_id TEXT,
    idempotency_key TEXT,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS tourney_audit_team_idx
    ON vgna_tourney.audit_events (guild_id, team_id, created_at);
CREATE INDEX IF NOT EXISTS tourney_audit_subject_idx
    ON vgna_tourney.audit_events (guild_id, subject_user_id, created_at);

CREATE TABLE IF NOT EXISTS vgna_tourney.settings (
    guild_id TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (guild_id, key)
);
