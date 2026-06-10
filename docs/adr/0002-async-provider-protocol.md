# 0002: Use an async provider protocol

- Status: Accepted
- Date: 2026-06-10
- Owners: Architecture

## Context

Provider calls are I/O-bound and concrete SDKs expose incompatible request,
response, and exception types. Phase 0 must define the boundary without making
network calls.

## Decision

Define a typed async structural protocol accepting a domain
`ChatCompletionRequest` and immutable `ProviderContext`, returning a domain
`ChatCompletionResponse`. Adapters raise the gateway provider error taxonomy.
A scripted in-memory implementation serves as the initial test double.

## Consequences

Orchestration can use providers without importing an SDK. Cancellation and
timeouts remain the caller's responsibility. Adapters must normalize provider
responses and sanitize errors.

## Alternatives considered

A synchronous interface was rejected because it would require thread
offloading for normal provider I/O. A base class was rejected because
structural typing keeps adapters lightweight and composable.
