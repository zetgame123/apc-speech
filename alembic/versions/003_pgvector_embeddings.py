"""Add pgvector embedding column, metadata columns, and HNSW cosine index

Revision ID: 003
Revises: 002
Create Date: 2026-11-21 12:20:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _existing_columns(conn, table_name: str) -> set[str]:
    inspector = sa.inspect(conn)
    if table_name not in inspector.get_table_names():
        return set()
    return {col["name"] for col in inspector.get_columns(table_name)}


def upgrade() -> None:
    conn = op.get_bind()
    cols = _existing_columns(conn, "embeddings")

    if conn.dialect.name == "postgresql":
        op.execute(sa.text("CREATE EXTENSION IF NOT EXISTS vector;"))
        op.execute(sa.text("ALTER TABLE embeddings ALTER COLUMN vector_json DROP NOT NULL;"))
        if "embedding" not in cols:
            op.execute(sa.text("ALTER TABLE embeddings ADD COLUMN embedding vector(384);"))
        if "model_name" not in cols:
            op.add_column("embeddings", sa.Column("model_name", sa.String(100), nullable=True))
        if "updated_at" not in cols:
            op.add_column("embeddings", sa.Column("updated_at", sa.DateTime(), nullable=True))
        op.execute(
            sa.text(
                "CREATE INDEX IF NOT EXISTS idx_embeddings_hnsw_cosine "
                "ON embeddings USING hnsw (embedding vector_cosine_ops);"
            )
        )
    else:
        if "embedding" not in cols:
            op.add_column("embeddings", sa.Column("embedding", sa.JSON(), nullable=True))
        if "model_name" not in cols:
            op.add_column("embeddings", sa.Column("model_name", sa.String(100), nullable=True))
        if "updated_at" not in cols:
            op.add_column("embeddings", sa.Column("updated_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    conn = op.get_bind()
    cols = _existing_columns(conn, "embeddings")

    if conn.dialect.name == "postgresql":
        op.execute(sa.text("DROP INDEX IF EXISTS idx_embeddings_hnsw_cosine;"))
        for col in ("updated_at", "model_name", "embedding"):
            if col in cols:
                op.drop_column("embeddings", col)
    else:
        with op.batch_alter_table("embeddings") as batch_op:
            for col in ("updated_at", "model_name", "embedding"):
                if col in cols:
                    batch_op.drop_column(col)
