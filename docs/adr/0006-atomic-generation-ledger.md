# 0006: Start generation records atomically

- Status: Accepted
- Date: 2026-06-12
- Owners: Gateway

## Context

The Phase 1 generation path must create a gateway request and its first provider
attempt before provider I/O starts. Committing those records in separate
transactions can leave incomplete lifecycle state when a later write fails.
Usage also needs an immutable pricing reference and must not be finalized more
than once for the same attempt.

## Decision

Create the request and first provider attempt, including their `in_progress`
state and start timestamp, in one database transaction. Finalize success in a
separate transaction that writes exactly one usage record, links the pricing
snapshot used for the calculation, and marks both lifecycle records succeeded.

Reject completion when the request and attempt do not belong together, are not
in progress, or already have usage. Persist cached input tokens separately and
price uncached input, cached input, and output with `Decimal` rates from the
selected pricing snapshot.

## Consequences

Provider I/O begins only after a complete durable lifecycle pair exists.
Duplicate completion cannot double charge. Failure handling must preserve
terminal states rather than rewriting a completed request. Pricing migrations
must remain additive because earlier Phase 1 revisions are already published.

## Alternatives considered

Three independent start transactions were rejected because partial state could
survive a failure. Application-only duplicate checks were rejected because
concurrent or external writers could bypass them.
