"""Initial schema

Revision ID: 001
Revises: None
Create Date: 2026-09-29
"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "calls",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("namespace", sa.String(100), nullable=False, index=True),
        sa.Column("external_id", sa.String(100), nullable=True, index=True),
        sa.Column("direction", sa.String(20), nullable=True, server_default="inbound"),
        sa.Column("call_created_at", sa.DateTime, nullable=True),
        sa.Column("manager_id", sa.String(100), nullable=True, index=True),
        sa.Column("client_id", sa.String(100), nullable=True, index=True),
        sa.Column("duration_sec", sa.Float, nullable=True),
        sa.Column("is_answering_machine", sa.Boolean, server_default="false"),
        sa.Column("created_at", sa.DateTime, index=True),
        sa.Column("expires_at", sa.DateTime, nullable=True, index=True),
    )
    op.create_index("idx_calls_ns_created", "calls", ["namespace", "created_at"])
    op.create_index("idx_calls_ns_manager", "calls", ["namespace", "manager_id"])

    op.create_table(
        "audio_objects",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("call_id", sa.String(36), sa.ForeignKey("calls.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("namespace", sa.String(100), nullable=False, index=True),
        sa.Column("s3_bucket", sa.String(100), nullable=False),
        sa.Column("s3_key", sa.String(255), nullable=False),
        sa.Column("mime_type", sa.String(50), nullable=True),
        sa.Column("file_size_bytes", sa.Integer, nullable=True),
        sa.Column("channels_layout", sa.String(20), server_default="mono"),
        sa.Column("expires_at", sa.DateTime, nullable=True, index=True),
        sa.Column("created_at", sa.DateTime),
    )

    op.create_table(
        "transcript_segments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("call_id", sa.String(36), sa.ForeignKey("calls.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("namespace", sa.String(100), nullable=False, index=True),
        sa.Column("segment_order", sa.Integer, nullable=False),
        sa.Column("start_time", sa.Float, nullable=False),
        sa.Column("end_time", sa.Float, nullable=False),
        sa.Column("speaker_label", sa.String(50), nullable=False),
        sa.Column("speaker_role", sa.String(50), server_default="participant"),
        sa.Column("text_original", sa.Text, nullable=False),
        sa.Column("text_masked", sa.Text, nullable=False),
        sa.Column("confidence", sa.Float, nullable=True),
        sa.Column("words_json", postgresql.JSONB, nullable=True),
    )
    op.create_index("idx_segments_call_order", "transcript_segments", ["call_id", "segment_order"])

    op.create_table(
        "analyses",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("call_id", sa.String(36), sa.ForeignKey("calls.id", ondelete="CASCADE"), nullable=False, unique=True, index=True),
        sa.Column("namespace", sa.String(100), nullable=False, index=True),
        sa.Column("schema_version", sa.String(20), nullable=False, server_default="1.0.0"),
        sa.Column("prompt_version", sa.String(50), nullable=False, server_default="v1.2-ru-outdoor"),
        sa.Column("summary", sa.Text, nullable=True),
        sa.Column("call_result", sa.String(50), nullable=True, index=True),
        sa.Column("inquiry_type", sa.String(50), nullable=True, index=True),
        sa.Column("entities", postgresql.JSONB, nullable=True),
        sa.Column("agreements", postgresql.JSONB, nullable=True),
        sa.Column("sentiment", postgresql.JSONB, nullable=True),
        sa.Column("manager_checklist", postgresql.JSONB, nullable=True),
        sa.Column("speech_metrics", postgresql.JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime),
    )

    op.create_table(
        "jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("namespace", sa.String(100), nullable=False, index=True),
        sa.Column("call_id", sa.String(36), sa.ForeignKey("calls.id", ondelete="SET NULL"), nullable=True),
        sa.Column("status", sa.String(20), server_default="queued", index=True),
        sa.Column("progress", sa.Integer, server_default="0"),
        sa.Column("step", sa.String(50), server_default="init"),
        sa.Column("idempotency_key", sa.String(128), nullable=True),
        sa.Column("file_hash_sha256", sa.String(64), nullable=True, index=True),
        sa.Column("callback_url", sa.String(512), nullable=True),
        sa.Column("error_details", postgresql.JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime, index=True),
        sa.Column("updated_at", sa.DateTime),
    )
    op.create_index("idx_jobs_ns_idempotency", "jobs", ["namespace", "idempotency_key"], unique=True)

    op.create_table(
        "callback_logs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("job_id", sa.String(36), sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("url", sa.String(512), nullable=False),
        sa.Column("attempt", sa.Integer, nullable=False),
        sa.Column("http_status", sa.Integer, nullable=True),
        sa.Column("response_body", sa.Text, nullable=True),
        sa.Column("sent_at", sa.DateTime),
    )

    op.create_table(
        "embeddings",
        sa.Column("call_id", sa.String(36), sa.ForeignKey("calls.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("namespace", sa.String(100), nullable=False, index=True),
        sa.Column("vector_json", postgresql.JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime),
    )


def downgrade() -> None:
    op.drop_table("embeddings")
    op.drop_table("callback_logs")
    op.drop_table("jobs")
    op.drop_table("analyses")
    op.drop_table("transcript_segments")
    op.drop_table("audio_objects")
    op.drop_table("calls")
