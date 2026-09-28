import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from app.db.session import Base


def generate_uuid() -> str:
    return str(uuid.uuid4())


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Call(Base):
    __tablename__ = "calls"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    namespace = Column(String(100), nullable=False, index=True)
    external_id = Column(String(100), nullable=True, index=True)
    direction = Column(String(20), nullable=True, default="inbound")
    call_created_at = Column(DateTime, nullable=True)
    manager_id = Column(String(100), nullable=True, index=True)
    client_id = Column(String(100), nullable=True, index=True)
    duration_sec = Column(Float, nullable=True)
    is_answering_machine = Column(Boolean, default=False)
    created_at = Column(DateTime, default=utc_now, index=True)
    expires_at = Column(DateTime, nullable=True, index=True)

    # Relationships
    audio_objects = relationship("AudioObject", back_populates="call", cascade="all, delete-orphan")
    segments = relationship("TranscriptSegment", back_populates="call", cascade="all, delete-orphan", order_by="TranscriptSegment.segment_order")
    analysis = relationship("Analysis", back_populates="call", uselist=False, cascade="all, delete-orphan")
    jobs = relationship("Job", back_populates="call")

    __table_args__ = (
        Index("idx_calls_ns_created", "namespace", "created_at"),
        Index("idx_calls_ns_manager", "namespace", "manager_id"),
    )


class AudioObject(Base):
    __tablename__ = "audio_objects"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    call_id = Column(String(36), ForeignKey("calls.id", ondelete="CASCADE"), nullable=False, index=True)
    namespace = Column(String(100), nullable=False, index=True)
    s3_bucket = Column(String(100), nullable=False)
    s3_key = Column(String(255), nullable=False)
    mime_type = Column(String(50), nullable=True)
    file_size_bytes = Column(Integer, nullable=True)
    channels_layout = Column(String(20), default="mono")
    expires_at = Column(DateTime, nullable=True, index=True)
    created_at = Column(DateTime, default=utc_now)

    call = relationship("Call", back_populates="audio_objects")


class TranscriptSegment(Base):
    __tablename__ = "transcript_segments"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    call_id = Column(String(36), ForeignKey("calls.id", ondelete="CASCADE"), nullable=False, index=True)
    namespace = Column(String(100), nullable=False, index=True)
    segment_order = Column(Integer, nullable=False)
    start_time = Column(Float, nullable=False)
    end_time = Column(Float, nullable=False)
    speaker_label = Column(String(50), nullable=False)
    speaker_role = Column(String(50), default="participant")
    text_original = Column(Text, nullable=False)
    text_masked = Column(Text, nullable=False)
    confidence = Column(Float, nullable=True)
    words_json = Column(JSONB, nullable=True)

    call = relationship("Call", back_populates="segments")

    __table_args__ = (
        Index("idx_segments_call_order", "call_id", "segment_order"),
    )


class Analysis(Base):
    __tablename__ = "analyses"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    call_id = Column(String(36), ForeignKey("calls.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    namespace = Column(String(100), nullable=False, index=True)
    schema_version = Column(String(20), nullable=False, default="1.0.0")
    prompt_version = Column(String(50), nullable=False, default="v1.2-ru-outdoor")
    summary = Column(Text, nullable=True)
    call_result = Column(String(50), nullable=True, index=True)
    inquiry_type = Column(String(50), nullable=True, index=True)
    entities = Column(JSONB, nullable=True)
    agreements = Column(JSONB, nullable=True)
    sentiment = Column(JSONB, nullable=True)
    manager_checklist = Column(JSONB, nullable=True)
    speech_metrics = Column(JSONB, nullable=True)
    created_at = Column(DateTime, default=utc_now)

    call = relationship("Call", back_populates="analysis")


class Job(Base):
    __tablename__ = "jobs"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    namespace = Column(String(100), nullable=False, index=True)
    call_id = Column(String(36), ForeignKey("calls.id", ondelete="SET NULL"), nullable=True)
    status = Column(String(20), default="queued", index=True)  # queued, running, succeeded, failed
    progress = Column(Integer, default=0)
    step = Column(String(50), default="init")
    idempotency_key = Column(String(128), nullable=True)
    file_hash_sha256 = Column(String(64), nullable=True, index=True)
    callback_url = Column(String(512), nullable=True)
    error_details = Column(JSONB, nullable=True)
    created_at = Column(DateTime, default=utc_now, index=True)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)

    call = relationship("Call", back_populates="jobs")

    __table_args__ = (
        Index("idx_jobs_ns_idempotency", "namespace", "idempotency_key", unique=True),
    )


class CallbackLog(Base):
    __tablename__ = "callback_logs"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    job_id = Column(String(36), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True)
    url = Column(String(512), nullable=False)
    attempt = Column(Integer, nullable=False)
    http_status = Column(Integer, nullable=True)
    response_body = Column(Text, nullable=True)
    sent_at = Column(DateTime, default=utc_now)


class Embedding(Base):
    __tablename__ = "embeddings"

    call_id = Column(String(36), ForeignKey("calls.id", ondelete="CASCADE"), primary_key=True)
    namespace = Column(String(100), nullable=False, index=True)
    vector_json = Column(JSONB, nullable=False)  # Stores vector array (can also map to pgvector column)
    created_at = Column(DateTime, default=utc_now)
