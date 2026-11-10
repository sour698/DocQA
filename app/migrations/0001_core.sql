-- Core tables: accounts, usage limits, analytics, audit trail.
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE users (
    id            text PRIMARY KEY,
    email         text NOT NULL UNIQUE,
    salt          bytea NOT NULL,
    password_hash bytea NOT NULL,
    token_ver     integer NOT NULL DEFAULT 0,
    is_active     boolean NOT NULL DEFAULT true,
    created_at    timestamptz NOT NULL DEFAULT now(),
    last_login_at timestamptz
);

-- questions asked per user (or per demo visitor) per UTC day; '*' is the whole-app counter
CREATE TABLE budget (
    user_key text NOT NULL,
    day      date NOT NULL,
    used     integer NOT NULL DEFAULT 0,
    PRIMARY KEY (user_key, day)
);

-- sliding-window rate limiting (shared by every app process)
CREATE TABLE rate_events (
    id  bigserial PRIMARY KEY,
    key text NOT NULL,
    ts  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX rate_events_key_ts ON rate_events (key, ts);

CREATE TABLE query_log (
    id             bigserial PRIMARY KEY,
    ts             timestamptz NOT NULL DEFAULT now(),
    user_id        text NOT NULL,
    question       text NOT NULL,
    route          text,
    refused        boolean,
    from_documents boolean,
    top_score      double precision,
    latency_ms     integer
);
CREATE INDEX query_log_user_ts ON query_log (user_id, ts);

CREATE TABLE feedback (
    id       bigserial PRIMARY KEY,
    ts       timestamptz NOT NULL DEFAULT now(),
    user_id  text NOT NULL,
    question text NOT NULL,
    answer   text,
    rating   smallint NOT NULL CHECK (rating IN (-1, 1)),
    comment  text
);
CREATE INDEX feedback_user_ts ON feedback (user_id, ts);

-- who did what: sign-ins, failures, password changes, uploads, deletions
CREATE TABLE audit_log (
    id      bigserial PRIMARY KEY,
    ts      timestamptz NOT NULL DEFAULT now(),
    user_id text,
    event   text NOT NULL,
    ip      text,
    detail  text
);
CREATE INDEX audit_log_ts ON audit_log (ts);
CREATE INDEX audit_log_user_ts ON audit_log (user_id, ts);

-- install-time facts the app verifies on every start (embedding size / model, search language)
CREATE TABLE settings (
    key   text PRIMARY KEY,
    value text NOT NULL
);
INSERT INTO settings (key, value) VALUES ('embed_dim', '{EMBED_DIM}'), ('fts_language', '{FTS_LANGUAGE}');
