# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Real evaluation benchmark using `faster-whisper`.
- Local mono audio diarization based on MFCC features and KMeans clustering.
- Real analytics calculation from the database.
- CSV export for analytics (`GET /v1/analytics/export_csv`).
- Long audio file chunking support for files up to 3 hours with overlap handling.
- `expires_at` column in DB to support text retention rules.
- Rate limiting for expensive API endpoints using Redis.
- Explicit `uv.lock` dependency locking for reproducible Docker builds.
- Cleanup script for expiring old audio and text data.

### Changed
- Refactored `evaluation/run.py` to remove hardcoded results.
- Fixed timezone issues in the database models by replacing `datetime.utcnow()` with timezone-aware `datetime.now(timezone.utc)`.
- Updated `README.md` and `openapi.yaml` to reflect the implemented changes.

### Security
- Fixed PII leakage for phone numbers and passports before sending transcripts to the LLM (SEC-001).
- Removed hardcoded default `dev-token` from production authentication flow (SEC-002).
