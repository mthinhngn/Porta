# 0005: Enforce cross-record persistence integrity

- Status: Accepted
- Date: 2026-06-10
- Owners: Architecture

## Context

Provider attempts identify both a provider and a model, while usage may
identify both a gateway request and one of its attempts. Independent foreign
keys prove that each referenced row exists but do not prove that the paired
identities belong together. Application validation alone can be bypassed by
maintenance scripts, concurrent writes, or future code paths.

## Decision

Use composite relational constraints to enforce paired identities:

- `(model_id, provider_id)` on an attempt references the corresponding pair on
  `models`.
- `(provider_attempt_id, gateway_request_id)` on usage references the
  corresponding pair on `provider_attempts`.
- usage `total_tokens` must equal `prompt_tokens + completion_tokens`.

Retain ordinary request foreign keys for request-level usage and lifecycle
ownership. Keep the schema portable to PostgreSQL and SQLite so Phase 0 tests
can execute the constraints locally. Add descriptive comments to
privacy-sensitive columns as schema-level handling guidance.

## Consequences

The database rejects provider/model mismatches, cross-request usage links, and
inconsistent token totals regardless of the writer. Composite candidate keys
and relationship join annotations add schema and ORM complexity. Existing
data must satisfy these rules before persistence. The initial Phase 0 Alembic
revision creates the constraints so a clean `upgrade head` produces the same
relational guarantees as the SQLAlchemy metadata.

## Alternatives considered

Application-only validation was rejected because it does not protect writes
outside the primary service path. Triggers were rejected because declarative
foreign keys and checks are simpler, portable, and visible to SQLAlchemy
metadata tooling.
