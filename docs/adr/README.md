# Architecture Decision Records

ADRs capture decisions that constrain multiple packages, persistence, public
contracts, security, or operations.

## Index

- [0001: Separate contracts, providers, and persistence](0001-package-boundaries.md)
- [0002: Use an async provider protocol](0002-async-provider-protocol.md)
- [0003: Persist lifecycle records separately](0003-persistence-model.md)
- [0004: Minimize and redact content by default](0004-privacy-by-default.md)
- [0005: Enforce cross-record persistence integrity](0005-cross-record-integrity.md)
- [0006: Start generation records atomically](0006-atomic-generation-ledger.md)

## Format

New ADRs use the next four-digit number and this structure:

```markdown
# NNNN: Decision title

- Status: Proposed | Accepted | Superseded | Deprecated
- Date: YYYY-MM-DD
- Owners: role or team

## Context

What forces and constraints require a decision?

## Decision

What is the decision?

## Consequences

What becomes easier, harder, required, or prohibited?

## Alternatives considered

What credible alternatives were rejected and why?
```

Accepted ADRs are immutable except for typo or link fixes. A changed decision
gets a new ADR that marks the old record superseded.
