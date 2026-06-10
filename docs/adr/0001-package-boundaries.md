# 0001: Separate contracts, providers, and persistence

- Status: Accepted
- Date: 2026-06-10
- Owners: Architecture

## Context

The gateway must present an OpenAI-style contract while remaining independent
of HTTP frameworks, provider SDKs, and database representations.

## Decision

Maintain one-way package dependencies:

- `domain` owns public request, response, usage, and error contracts.
- `providers` depends on `domain` and owns the provider protocol.
- `persistence` owns SQLAlchemy metadata and records, independent of transport.
- Future application orchestration composes these boundaries.

ORM and provider SDK objects do not cross into public API contracts.

## Consequences

Contract tests can run without a database or provider SDK. Provider adapters
remain replaceable. Explicit mapping code is required between contracts and
persistence records.

## Alternatives considered

A shared model for API, provider, and ORM data was rejected because schema
changes in one boundary would leak into the others and increase accidental
data persistence.
