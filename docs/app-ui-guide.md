# LLM Gateway Test Console

`app.py` is a small browser UI for manually testing the local gateway.

## What It Does

- Checks `GET /health/live` and `GET /health/ready`.
- Checks `GET /metrics` and confirms key metric families are present.
- Draws local charts from the Prometheus scrape for HTTP status, generation
  results, provider attempts, cache events, and generation latency buckets.
- Sends `POST /v1/generate` with your user gateway key.
- Calls `GET /v1/analytics/usage/summary` with your admin gateway key.
- Keeps user/admin keys in the browser session only; it does not write them to
  disk.

## Start The Gateway

In one PowerShell window:

```powershell
cd C:\Users\thinh\llm-gateway
.\.venv\Scripts\Activate.ps1
uv run llm-gateway
```

The gateway should be available at:

```text
http://127.0.0.1:8000
```

For OpenAI generation, save your provider key once in ignored `.env.local`:

```powershell
Add-Content .env.local 'LLM_GATEWAY_OPENAI_API_KEY=your-real-openai-key'
```

Restart the gateway after changing `.env.local`.

## Start The UI

In another PowerShell window:

```powershell
cd C:\Users\thinh\llm-gateway
.\.venv\Scripts\Activate.ps1
uv run python app.py
```

Open:

```text
http://127.0.0.1:8501
```

To make the UI reachable from another device on your network, run:

```powershell
uv run python app.py --host 0.0.0.0 --port 8501
```

Use that only on a trusted network because the page accepts local test keys.

## Test Flow

1. Leave Gateway URL as `http://127.0.0.1:8000`.
2. Set User key to `user-test-key`.
3. Set Admin key to `admin-test-key`.
4. Click **Check health**.
5. Click **Check metrics**.
6. Review the **Visual Observability** charts.
7. Run a prompt in **Generate**.
8. Click **Check metrics** again to refresh the charts.
9. Click **Load analytics**.

## Expected Results

- Health shows `Live OK`.
- Metrics finds gateway metric families.
- Visual Observability shows bar charts once matching samples exist.
- Generate returns a provider, token counts, cost, cache status, and output.
- Analytics returns aggregate request, attempt, usage, cost, and reconciliation
  sections.

## Common Problems

- `404` at `http://127.0.0.1:8000/` is normal; use `/health/live` or this UI.
- `401 authentication_error` means the key in the UI does not match the gateway
  env.
- `403 analytics_access_denied` means the admin key does not have
  `"is_admin": true`.
- `503 service_unavailable` usually means Redis, Postgres, or the gateway
  generation service is not configured.
- OpenAI generation requires a real local `LLM_GATEWAY_OPENAI_API_KEY` in
  `.env.local` or the gateway process environment.
