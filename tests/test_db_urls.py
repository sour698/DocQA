import pytest

from app import db


@pytest.mark.parametrize(
    "url,pooled",
    [
        (
            "postgresql://u:p@ep-cool-123456.us-east-2.aws.neon.tech/db?sslmode=require",
            False,
        ),  # Neon direct
        (
            "postgresql://u:p@ep-cool-123456-pooler.us-east-2.aws.neon.tech/db?sslmode=require",
            True,
        ),  # Neon pooled
        ("postgresql://postgres:p@db.abcdefgh.supabase.co:5432/postgres", False),  # Supabase direct
        (
            "postgres://postgres.abcdefgh:p@aws-0-eu-west-1.pooler.supabase.com:5432/postgres",
            False,
        ),  # session
        (
            "postgres://postgres.abcdefgh:p@aws-0-eu-west-1.pooler.supabase.com:6543/postgres",
            True,
        ),  # transaction
        ("postgresql://docqa:docqa@localhost:5432/docqa", False),
    ],
)
def test_transaction_poolers_are_detected(url, pooled):
    assert db.is_transaction_pooler(url) is pooled


def test_statement_timeout_is_only_sent_on_direct_or_session_connections():
    direct = db.pool_kwargs("postgresql://u:p@host.example.com:5432/db")
    pooled = db.pool_kwargs("postgresql://u:p@ep-x-pooler.eu.aws.neon.tech/db")
    assert "statement_timeout" in direct["options"]
    assert "options" not in pooled
    assert direct["prepare_threshold"] is None and pooled["prepare_threshold"] is None
