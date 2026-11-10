-- Documents (the original files) and their searchable chunks live in the same database, so an
-- upload or a delete is ONE transaction: a document can never exist without its chunks or the
-- other way round, and a single pg_dump backs up everything.
CREATE TABLE documents (
    user_id    text NOT NULL,
    name       text NOT NULL,
    content    bytea NOT NULL,
    size_bytes integer NOT NULL,
    sha256     text NOT NULL,
    pages      integer NOT NULL DEFAULT 0,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, name)
);

CREATE TABLE chunks (
    user_id   text NOT NULL,
    id        text NOT NULL,
    source    text NOT NULL,
    page      integer NOT NULL,
    idx       integer NOT NULL,
    heading   text NOT NULL DEFAULT '',
    text      text NOT NULL,
    embedding vector({EMBED_DIM}) NOT NULL,
    tsv       tsvector GENERATED ALWAYS AS (
                  to_tsvector('{FTS_LANGUAGE}',
                      regexp_replace(source, '[^A-Za-z0-9]+', ' ', 'g') || ' ' || heading || ' ' || text)
              ) STORED,
    PRIMARY KEY (user_id, id),
    FOREIGN KEY (user_id, source) REFERENCES documents (user_id, name) ON DELETE CASCADE
);
CREATE INDEX chunks_source_order ON chunks (user_id, source, page, idx);
CREATE INDEX chunks_tsv ON chunks USING gin (tsv);
CREATE INDEX chunks_embedding ON chunks USING hnsw (embedding vector_cosine_ops);
