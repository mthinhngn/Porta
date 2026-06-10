# 0004: Minimize and redact content by default

- Status: Accepted
- Date: 2026-06-10
- Owners: Architecture

## Context

Prompts, responses, identifiers, and provider errors can contain personal,
regulated, proprietary, or secret data. Most reliability analysis does not
require raw content.

## Decision

Do not persist or log request/response content by default. Persist only
allowlisted operational fields. Store a redacted request payload only when an
explicit policy enables it. Store provider credentials outside the database
and refer to them by `secret_ref`.

## Consequences

The default design reduces disclosure impact and access scope. Content-level
debugging requires a separately approved workflow. Redaction and keyed hashing
must be implemented before enabling optional content or identifier retention.

## Alternatives considered

Persisting all payloads with access controls was rejected because access
control does not remove retention, breach, backup, and secondary-use risk.
