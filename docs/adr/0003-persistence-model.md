# 0003: Persist lifecycle records separately

- Status: Accepted
- Date: 2026-06-10
- Owners: Architecture

## Context

A gateway request may make multiple provider attempts, while usage, audit
context, and provider/model configuration have different access and retention
requirements.

## Decision

Use separate relational entities for gateway requests, provider attempts,
providers, models, usage records, and audit metadata. Use UUID primary keys,
timezone-aware timestamps, application-managed lifecycle strings, and
JSONB-compatible metadata columns.

## Consequences

Retries and fallback remain observable without duplicating request records.
Audit access and retention can differ from usage. Queries require joins, and
application code must enforce valid lifecycle transitions.

## Alternatives considered

A single request event blob was rejected because it weakens constraints,
queryability, and retention separation. Database-native enums were deferred to
avoid high-friction changes during early lifecycle design.
