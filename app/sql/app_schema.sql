CREATE SCHEMA IF NOT EXISTS app;

CREATE TABLE IF NOT EXISTS app.users (
    id                   serial PRIMARY KEY,
    username             text NOT NULL,
    password_hash        text NOT NULL,
    role                 text NOT NULL CHECK (role IN ('admin','reporting')),
    is_active            boolean NOT NULL DEFAULT true,
    must_change_password boolean NOT NULL DEFAULT true,
    failed_count         int NOT NULL DEFAULT 0,
    locked_until         timestamptz,
    created_at           timestamptz NOT NULL DEFAULT now(),
    last_login           timestamptz
);
CREATE UNIQUE INDEX IF NOT EXISTS users_username_ci ON app.users (lower(username));

CREATE TABLE IF NOT EXISTS app.settings (
    key        text PRIMARY KEY,
    value      text,
    is_secret  boolean NOT NULL DEFAULT false,
    updated_at timestamptz NOT NULL DEFAULT now(),
    updated_by text
);

CREATE TABLE IF NOT EXISTS app.audit_log (
    id       bigserial PRIMARY KEY,
    ts       timestamptz NOT NULL DEFAULT now(),
    username text,
    action   text NOT NULL,
    detail   text
);

CREATE TABLE IF NOT EXISTS app.jobs (
    id           bigserial PRIMARY KEY,
    kind         text NOT NULL DEFAULT 'etl',
    params       jsonb NOT NULL DEFAULT '{}',
    status       text NOT NULL DEFAULT 'queued',  -- queued | running | done | failed
    message      text,
    requested_by text,
    requested_at timestamptz NOT NULL DEFAULT now(),
    started_at   timestamptz,
    finished_at  timestamptz
);
