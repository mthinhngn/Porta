# Privacy and Data Handling

## Principles

The gateway minimizes retained content, separates operational metadata from
prompt data, and treats all request and response content as confidential.
Collection must have a stated operational purpose and a bounded retention
period.

## Data classification

| Class | Examples | Default handling |
| --- | --- | --- |
| Secret | API keys, authorization headers, provider credentials | Never log or persist; load by secret reference |
| Content | prompts, tool results, assistant output, stop strings | Do not persist by default; redact from logs |
| Identifier | end-user value, IP address, provider request ID | Omit or keyed-hash when needed; restrict access |
| Operational | model alias, latency, status, token totals | Persist with retention controls |
| Audit | actor hash, client application, policy tags | Persist separately with least-privilege access |

## Redaction policy

- Allowlist structured log fields instead of recursively logging request
  objects.
- Redact authorization, cookie, API-key, and secret-like headers regardless of
  case.
- Do not log message content, response content, stop sequences, or the request
  `user` value.
- Normalize provider errors before logging. Store a stable error code and a
  sanitized summary, not raw response bodies or exception dumps.
- Hash identifiers only with an application-held keyed hash. Plain hashes of
  emails, IP addresses, or user IDs are vulnerable to enumeration.
- Treat redacted payload persistence as opt-in. The
  `request_payload_redacted` column may remain null.

Redaction is not anonymization. Access controls and retention limits still
apply to redacted records.

## Correlation IDs

Correlation IDs are operational identifiers only. Accept an inbound value only
after validating length and character policy; otherwise generate a random
value. Never derive one from prompt content, an email address, IP address, API
key, or the client-provided `user` field.

Correlation IDs may appear in client responses, logs, traces, and database
records. Internal request and provider-attempt UUIDs should remain internal
unless support tooling requires them.

## Persistence and retention

- Retain lifecycle, latency, model mapping, and token totals only as long as
  needed for reliability, billing, abuse response, or legal obligations.
- Give content retention a separate explicit configuration and legal review.
- Delete or aggregate request-linked usage and audit records according to the
  applicable retention class.
- Backups inherit the same classification and require expiration controls.
- Restrict audit metadata and provider request IDs to operational roles.

## Provider disclosure

Before enabling a provider/model pair, document the provider's data use,
retention, training, residency, and subprocessors. Routing must honor any
tenant or deployment restrictions. Do not silently fail over to a provider
with weaker privacy terms.

## Configuration requirements

Privacy-affecting settings must be explicit and reviewable:

- content persistence enabled/disabled, default disabled
- retention class or duration
- keyed-hash secret reference
- permitted providers and models
- provider timeout and fallback policy
- log level without payload logging

Secrets must come from environment injection or a secret manager. Database
records may store only a lookup reference such as `secret_ref`.

## Incident response

Operational tooling should support lookup by correlation ID without requiring
prompt content. Suspected disclosure of secrets or content requires immediate
credential rotation where applicable, preservation of minimal audit evidence,
and deletion or quarantine under the incident process.
