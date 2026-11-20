"""Add composite query indexes and PostgreSQL pg_trgm GIN index for transcript search

Revision ID: 002
Revises: 001
Create Date: 2026-11-20 14:48:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _existing_indexes(conn, table_name: str) -> set[str]:
    inspector = sa.inspect(conn)
    if table_name not in inspector.get_table_names():
        return set()
    return {idx["name"] for idx in inspector.get_indexes(table_name) if idx.get("name")}


def upgrade() -> None:
    conn = op.get_bind()

    seg_idx = _existing_indexes(conn, "transcript_segments")
    if "idx_segments_ns_call" not in seg_idx:
        op.create_index(
            "idx_segments_ns_call",
            "transcript_segments",
            ["namespace", "call_id"],
        )

    an_idx = _existing_indexes(conn, "analyses")
    if "idx_analyses_ns_inquiry" not in an_idx:
        op.create_index(
            "idx_analyses_ns_inquiry",
            "analyses",
            ["namespace", "inquiry_type"],
        )

    job_idx = _existing_indexes(conn, "jobs")
    if "idx_jobs_status_step_created" not in job_idx:
        op.create_index(
            "idx_jobs_status_step_created",
            "jobs",
            ["status", "step", "created_at"],
        )

    # PostgreSQL-only trigram GIN index accelerates ILIKE '%q%' search in GET /v1/calls
    if conn.dialect.name == "postgresql":
        op.execute(sa.text("CREATE EXTENSION IF NOT EXISTS pg_trgm;"))
        op.execute(
            sa.text(
                "CREATE INDEX IF NOT EXISTS idx_segments_text_masked_trgm "
                "ON transcript_segments USING gin (text_masked gin_trgm_ops);"
            )
        )


def downgrade() -> None:
    conn = op.get_bind()
    if conn.dialect.name == "postgresql":
        op.execute(sa.text("DROP INDEX IF EXISTS idx_segments_text_masked_trgm;"))

    for table, name in [
        ("jobs", "idx_jobs_status_step_created"),
        ("analyses", "idx_analyses_ns_inquiry"),
        ("transcript_segments", "idx_segments_ns_call"),
    ]:
        if name in _existing_indexes(conn, table):
            op.drop_index(name, table_name=table)
