-- Rate limiting as ONE atomic statement per attempt (a fixed window per key) instead of a table of
-- events that needed a lock, a delete, a count and an insert (about 6 network round trips).
CREATE TABLE rate_limits (
    key          text PRIMARY KEY,
    window_start timestamptz NOT NULL,
    hits         integer NOT NULL
);
DROP TABLE IF EXISTS rate_events;
