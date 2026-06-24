"""Small browser UI for manually testing the LLM gateway."""

from __future__ import annotations

import argparse
import re
from typing import Any

import httpx
import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

DEFAULT_GATEWAY_URL = "http://127.0.0.1:8000"

app = FastAPI(title="LLM Gateway Test Console", version="0.1.0")


class GatewayRequest(BaseModel):
    base_url: str = Field(default=DEFAULT_GATEWAY_URL, min_length=1, max_length=2048)


class GenerateUiRequest(GatewayRequest):
    user_key: str = Field(min_length=1, max_length=512)
    model: str = Field(default="gateway-default", min_length=1, max_length=255)
    input: str = Field(min_length=1, max_length=32768)
    max_output_tokens: int | None = Field(default=64, ge=16)
    temperature: float | None = Field(default=None, ge=0, le=2)
    top_p: float | None = Field(default=None, ge=0, le=1)


class AnalyticsUiRequest(GatewayRequest):
    admin_key: str = Field(min_length=1, max_length=512)
    provider: str | None = Field(default=None, min_length=1, max_length=128)
    model: str | None = Field(default=None, min_length=1, max_length=255)
    status: str | None = Field(default=None, min_length=1, max_length=32)


def _gateway_url(base_url: str, path: str) -> str:
    return f"{base_url.rstrip('/')}{path}"


def _error_payload(error: Exception) -> dict[str, Any]:
    return {"ok": False, "error": type(error).__name__, "message": str(error)}


def _parse_labels(label_text: str) -> dict[str, str]:
    labels: dict[str, str] = {}
    for match in re.finditer(r'([a-zA-Z_][a-zA-Z0-9_]*)="([^"]*)"', label_text):
        labels[match.group(1)] = match.group(2)
    return labels


def _parse_prometheus_samples(text: str) -> list[dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    sample_pattern = re.compile(
        r"^(?P<name>[a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{(?P<labels>[^}]*)\})?\s+"
        r"(?P<value>[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)$"
    )
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        match = sample_pattern.match(line)
        if not match:
            continue
        samples.append(
            {
                "name": match.group("name"),
                "labels": _parse_labels(match.group("labels") or ""),
                "value": float(match.group("value")),
            }
        )
    return samples


def _add_chart_value(
    charts: dict[str, dict[str, float]],
    chart_name: str,
    label: str,
    value: float,
) -> None:
    charts.setdefault(chart_name, {})
    charts[chart_name][label] = charts[chart_name].get(label, 0.0) + value


def _build_metric_charts(text: str) -> dict[str, dict[str, float]]:
    charts: dict[str, dict[str, float]] = {
        "http_by_status": {},
        "generate_by_result": {},
        "provider_by_result": {},
        "cache_by_status": {},
        "generation_latency_buckets": {},
    }
    for sample in _parse_prometheus_samples(text):
        name = sample["name"]
        labels = sample["labels"]
        value = sample["value"]
        if name == "llm_gateway_http_requests_total":
            status = labels.get("status_family") or labels.get("status_code") or "unknown"
            _add_chart_value(charts, "http_by_status", status, value)
        elif name == "llm_gateway_generate_events_total":
            result = labels.get("result", "unknown")
            _add_chart_value(charts, "generate_by_result", result, value)
        elif name == "llm_gateway_provider_attempts_total":
            result = labels.get("result", "unknown")
            provider = labels.get("provider", "provider")
            _add_chart_value(charts, "provider_by_result", f"{provider}:{result}", value)
        elif name == "llm_gateway_cache_events_total":
            status = labels.get("cache_status") or labels.get("result") or "unknown"
            _add_chart_value(charts, "cache_by_status", status, value)
        elif name == "llm_gateway_generation_duration_seconds_bucket":
            bucket = labels.get("le", "unknown")
            _add_chart_value(charts, "generation_latency_buckets", bucket, value)
    return charts


async def _json_or_text(response: httpx.Response) -> dict[str, Any]:
    content_type = response.headers.get("content-type", "")
    if "application/json" in content_type:
        return {"status_code": response.status_code, "body": response.json()}
    return {"status_code": response.status_code, "body": response.text}


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    return HTML


@app.post("/api/health")
async def check_health(payload: GatewayRequest) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            live = await client.get(_gateway_url(payload.base_url, "/health/live"))
            ready = await client.get(_gateway_url(payload.base_url, "/health/ready"))
        return {
            "ok": live.status_code == 200,
            "live": await _json_or_text(live),
            "ready": await _json_or_text(ready),
        }
    except Exception as exc:
        return _error_payload(exc)


@app.post("/api/metrics")
async def check_metrics(payload: GatewayRequest) -> dict[str, Any]:
    metric_names = [
        "llm_gateway_http_requests_total",
        "llm_gateway_generate_events_total",
        "llm_gateway_provider_attempts_total",
        "llm_gateway_cache_events_total",
        "llm_gateway_ledger_operation_duration_seconds",
    ]
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            response = await client.get(_gateway_url(payload.base_url, "/metrics"))
        text = response.text
        return {
            "ok": response.status_code == 200,
            "status_code": response.status_code,
            "found": {name: name in text for name in metric_names},
            "charts": _build_metric_charts(text),
            "sample": "\n".join(text.splitlines()[:24]),
        }
    except Exception as exc:
        return _error_payload(exc)


@app.post("/api/generate")
async def generate(payload: GenerateUiRequest) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": payload.model,
        "input": payload.input,
    }
    if payload.max_output_tokens is not None:
        body["max_output_tokens"] = payload.max_output_tokens
    if payload.temperature is not None:
        body["temperature"] = payload.temperature
    if payload.top_p is not None:
        body["top_p"] = payload.top_p

    try:
        async with httpx.AsyncClient(timeout=45.0) as client:
            response = await client.post(
                _gateway_url(payload.base_url, "/v1/generate"),
                headers={"Authorization": f"Bearer {payload.user_key}"},
                json=body,
            )
        return {"ok": response.status_code < 400, **await _json_or_text(response)}
    except Exception as exc:
        return _error_payload(exc)


@app.post("/api/analytics")
async def analytics(payload: AnalyticsUiRequest) -> dict[str, Any]:
    params = {
        key: value
        for key, value in {
            "provider": payload.provider,
            "model": payload.model,
            "status": payload.status,
        }.items()
        if value
    }
    try:
        async with httpx.AsyncClient(timeout=12.0) as client:
            response = await client.get(
                _gateway_url(payload.base_url, "/v1/analytics/usage/summary"),
                headers={"Authorization": f"Bearer {payload.admin_key}"},
                params=params,
            )
        return {"ok": response.status_code < 400, **await _json_or_text(response)}
    except Exception as exc:
        return _error_payload(exc)


HTML = r"""
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>LLM Gateway Test Console</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f3f6f1;
      --panel: #ffffff;
      --panel-soft: #f6f8f2;
      --panel-strong: #171f1a;
      --text: #18201c;
      --muted: #667268;
      --quiet: #8a968d;
      --line: #dce4d9;
      --line-strong: #becbbc;
      --accent: #116149;
      --accent-strong: #0b4d3a;
      --accent-soft: #dff1e8;
      --accent-2: #2f5fb3;
      --warm: #ad5f37;
      --bad: #b42318;
      --warn: #a15c07;
      --warn-soft: #fff5db;
      --bad-soft: #fee4e2;
      --ok-soft: #def7ec;
      --shadow: 0 18px 38px rgba(30, 45, 35, 0.08);
      --radius: 8px;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background:
        linear-gradient(180deg, #e7f0e8 0, var(--bg) 300px),
        var(--bg);
      color: var(--text);
      font: 14px/1.45 ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont,
        "Segoe UI", sans-serif;
    }
    header {
      border-bottom: 1px solid var(--line);
      background: rgba(255, 255, 255, 0.92);
      backdrop-filter: blur(12px);
      position: sticky;
      top: 0;
      z-index: 5;
    }
    .bar {
      max-width: 1320px;
      margin: 0 auto;
      padding: 16px 24px;
      display: flex;
      gap: 18px;
      align-items: center;
      justify-content: space-between;
    }
    h1 {
      margin: 0;
      font-size: 28px;
      line-height: 1.08;
      letter-spacing: 0;
    }
    .subtitle { color: var(--muted); margin-top: 8px; max-width: 72ch; }
    .status-strip {
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      justify-content: flex-end;
    }
    .pill {
      border: 1px solid var(--line);
      background: var(--panel-soft);
      border-radius: 999px;
      color: var(--muted);
      padding: 6px 10px;
      font-size: 12px;
      white-space: nowrap;
    }
    .pill.strong {
      background: var(--accent-soft);
      border-color: #b8e7e1;
      color: var(--accent-strong);
      font-weight: 750;
    }
    main {
      max-width: 1320px;
      margin: 0 auto;
      padding: 24px;
      display: grid;
      grid-template-columns: 320px minmax(0, 1fr);
      gap: 20px;
      align-items: start;
    }
    section, aside {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: var(--radius);
      box-shadow: var(--shadow);
    }
    aside {
      padding: 18px;
      position: sticky;
      top: 86px;
      background: #17211b;
      color: #ecf4ee;
      border-color: rgba(255, 255, 255, 0.1);
    }
    aside h2, aside h3 { color: #ffffff; }
    aside .hint, aside label, aside .step { color: #aab8ae; }
    aside input {
      background: rgba(255, 255, 255, 0.96);
      border-color: rgba(255, 255, 255, 0.18);
    }
    .stack { display: grid; gap: 16px; }
    .panel { padding: 18px; }
    .panel-head {
      display: flex;
      gap: 14px;
      align-items: flex-start;
      justify-content: space-between;
      margin-bottom: 14px;
    }
    .panel-kicker {
      color: var(--quiet);
      font-size: 12px;
      font-weight: 750;
    }
    h2 { margin: 0; font-size: 16px; letter-spacing: 0; }
    h3 { margin: 0 0 10px; font-size: 14px; letter-spacing: 0; }
    .hint { color: var(--muted); margin: 4px 0 0; font-size: 13px; }
    label {
      display: grid;
      gap: 6px;
      color: var(--muted);
      font-size: 12px;
      font-weight: 650;
    }
    input, textarea, select {
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #fff;
      color: var(--text);
      padding: 10px 12px;
      font: inherit;
      outline: none;
    }
    textarea { min-height: 126px; resize: vertical; }
    input:focus, textarea:focus, select:focus {
      border-color: var(--accent);
      box-shadow: 0 0 0 3px rgba(15, 118, 110, 0.14);
    }
    .grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
    .grid-3 { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; }
    button {
      border: 0;
      border-radius: 6px;
      background: var(--accent);
      color: white;
      font-weight: 750;
      padding: 10px 12px;
      cursor: pointer;
      min-height: 40px;
      transition: transform 120ms ease, filter 120ms ease, box-shadow 120ms ease;
    }
    button.secondary { background: #344054; }
    aside button.secondary { background: #496257; }
    button.ghost {
      color: var(--text);
      background: var(--panel-soft);
      border: 1px solid var(--line);
    }
    button:hover { filter: brightness(0.96); box-shadow: 0 8px 18px rgba(15, 23, 42, 0.12); }
    button:active { transform: translateY(1px); }
    .actions { display: flex; flex-wrap: wrap; gap: 10px; }
    .result {
      margin-top: 12px;
      border: 1px solid #253244;
      border-radius: 6px;
      background: var(--panel-strong);
      color: #d7e2ee;
      padding: 12px;
      min-height: 92px;
      max-height: 360px;
      overflow: auto;
      white-space: pre-wrap;
      word-break: break-word;
      font: 12px/1.5 ui-monospace, "Cascadia Code", Consolas, monospace;
    }
    .summary {
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 12px;
      margin: 0 0 12px;
    }
    .metric {
      border: 1px solid var(--line);
      border-radius: var(--radius);
      padding: 12px;
      background: var(--panel-soft);
    }
    .metric b { display: block; font-size: 20px; }
    .metric span { color: var(--muted); font-size: 12px; }
    .ok { color: var(--accent-2); }
    .bad { color: var(--bad); }
    .warn { color: var(--warn); }
    code {
      background: var(--panel-soft);
      border: 1px solid var(--line);
      border-radius: 5px;
      padding: 1px 5px;
      overflow-wrap: anywhere;
    }
    aside code {
      color: #dff1e8;
      background: rgba(255, 255, 255, 0.1);
      border-color: rgba(255, 255, 255, 0.16);
    }
    ol { margin: 0; padding-left: 20px; color: var(--muted); }
    li { margin: 8px 0; }
    .charts {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 12px;
    }
    .chart {
      border: 1px solid var(--line);
      border-radius: var(--radius);
      background:
        linear-gradient(180deg, #ffffff 0, var(--panel-soft) 100%);
      padding: 14px;
      min-height: 178px;
    }
    .chart:last-child { grid-column: 1 / -1; }
    .chart-title {
      color: var(--text);
      font-size: 12px;
      font-weight: 750;
      margin-bottom: 12px;
    }
    .bar-row {
      display: grid;
      grid-template-columns: minmax(96px, 138px) 1fr minmax(44px, auto);
      gap: 10px;
      align-items: center;
      min-height: 26px;
      margin: 8px 0;
    }
    .bar-label {
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      color: var(--text);
      font-size: 12px;
    }
    .bar-track {
      height: 11px;
      border-radius: 999px;
      background: #e7edf4;
      overflow: hidden;
    }
    .bar-fill {
      min-width: 2px;
      height: 100%;
      border-radius: inherit;
      background: linear-gradient(90deg, var(--accent), #14b8a6);
    }
    .bar-value {
      color: var(--muted);
      font: 12px ui-monospace, "Cascadia Code", Consolas, monospace;
      text-align: right;
    }
    .empty-chart {
      color: var(--muted);
      display: grid;
      min-height: 114px;
      place-items: center;
      text-align: center;
      border: 1px dashed var(--line-strong);
      border-radius: 6px;
      background: rgba(255, 255, 255, 0.62);
      padding: 14px;
    }
    .quick-grid {
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 12px;
    }
    .quick-card {
      border: 1px solid var(--line);
      border-radius: var(--radius);
      background: var(--panel-soft);
      padding: 12px;
    }
    .quick-card strong {
      display: block;
      font-size: 13px;
      margin-bottom: 4px;
    }
    .quick-card span {
      color: var(--muted);
      font-size: 12px;
    }
    .system-strip {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 12px;
    }
    .token-row, .component-preview {
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
      align-items: center;
      margin-top: 10px;
    }
    .swatch {
      width: 42px;
      height: 28px;
      border: 1px solid rgba(0, 0, 0, 0.08);
      border-radius: 6px;
    }
    .swatch.accent { background: var(--accent); }
    .swatch.blue { background: var(--accent-2); }
    .swatch.warm { background: var(--warm); }
    .swatch.ink { background: var(--panel-strong); }
    .mini-input {
      min-width: 160px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #fff;
      color: var(--muted);
      padding: 9px 10px;
    }
    .signal-map {
      display: grid;
      gap: 10px;
      margin-top: 12px;
    }
    .signal-row {
      display: grid;
      grid-template-columns: 130px 1fr auto;
      gap: 10px;
      align-items: center;
      color: var(--muted);
      font-size: 12px;
    }
    .signal-track {
      height: 8px;
      border-radius: 999px;
      background: #e5ece3;
      overflow: hidden;
    }
    .signal-fill {
      height: 100%;
      border-radius: inherit;
      background: linear-gradient(90deg, var(--accent), var(--accent-2));
    }
    .timeline {
      display: grid;
      gap: 8px;
      margin-top: 12px;
    }
    .step {
      display: grid;
      grid-template-columns: 28px 1fr;
      gap: 10px;
      align-items: start;
      color: var(--muted);
      font-size: 13px;
    }
    .step-mark {
      width: 24px;
      height: 24px;
      display: grid;
      place-items: center;
      border-radius: 999px;
      background: var(--accent-soft);
      color: var(--accent-strong);
      font-size: 12px;
      font-weight: 800;
    }
    .split-actions {
      display: flex;
      gap: 10px;
      justify-content: flex-end;
      flex-wrap: wrap;
    }
    @media (max-width: 900px) {
      body { overflow-x: hidden; }
      .bar, main, section, aside, .panel { min-width: 0; max-width: 100%; }
      main { grid-template-columns: 1fr; padding: 16px; }
      aside { position: static; }
      .summary, .grid-3, .quick-grid { grid-template-columns: 1fr; }
      .system-strip { grid-template-columns: 1fr; }
      .grid-2 { grid-template-columns: 1fr; }
      .charts { grid-template-columns: 1fr; }
      .chart:last-child { grid-column: auto; }
      .panel-head { align-items: stretch; flex-direction: column; }
      .split-actions { justify-content: flex-start; }
      .signal-row { grid-template-columns: 1fr; }
      .bar { align-items: flex-start; flex-direction: column; padding: 18px 16px; }
      .status-strip { justify-content: flex-start; max-width: 100%; }
      .pill { white-space: normal; }
    }
    @media (max-width: 520px) {
      .bar, main { max-width: 390px; margin-left: 0; margin-right: 0; }
      h1 { font-size: 26px; max-width: 12.5em; }
      .subtitle { max-width: 31ch; }
      .status-strip { max-width: 358px; }
    }
  </style>
</head>
<body>
  <header>
    <div class="bar">
      <div>
        <h1>Modern LLM Gateway Interface</h1>
        <div class="subtitle">
          Local observability, controlled generation smoke tests, and ledger analytics
          in one operator-focused surface.
        </div>
      </div>
      <div class="status-strip">
        <span class="pill strong">Design System</span>
        <span class="pill" id="gateway-pill">Gateway not checked</span>
        <span class="pill" id="metrics-pill">Metrics not checked</span>
      </div>
    </div>
  </header>

  <main>
    <aside>
      <div class="stack">
        <div>
          <h2>Connection</h2>
          <p class="hint">Keys are used for requests only and are not written by this UI.</p>
        </div>
        <label>Gateway URL
          <input id="baseUrl" value="http://127.0.0.1:8000" />
        </label>
        <label>User key
          <input id="userKey" type="password" value="user-test-key" autocomplete="off" />
        </label>
        <label>Admin key
          <input id="adminKey" type="password" value="admin-test-key" autocomplete="off" />
        </label>
        <div class="actions">
          <button onclick="checkHealth()">Check health</button>
          <button class="secondary" onclick="checkMetrics()">Check metrics</button>
        </div>
        <div>
          <h3>Setup path</h3>
          <div class="timeline">
            <div class="step">
              <div class="step-mark">1</div>
              <div>Start gateway with <code>uv run llm-gateway</code>.</div>
            </div>
            <div class="step">
              <div class="step-mark">2</div>
              <div>Start this UI with <code>uv run python app.py</code>.</div>
            </div>
            <div class="step">
              <div class="step-mark">3</div>
              <div>Keep the OpenAI provider key in <code>.env.local</code>.</div>
            </div>
            <div class="step">
              <div class="step-mark">4</div>
              <div>Check health, metrics, generate, then analytics.</div>
            </div>
          </div>
        </div>
      </div>
    </aside>

    <div class="stack">
      <section class="panel">
        <div class="panel-head">
          <div>
            <div class="panel-kicker">Control plane</div>
            <h2>Gateway Status</h2>
            <p class="hint">Fast checks for live, ready, scrape health, and the last action.</p>
          </div>
          <div class="split-actions">
            <button class="ghost" onclick="checkHealth()">Health</button>
            <button onclick="checkMetrics()">Metrics</button>
          </div>
        </div>
        <div class="summary">
          <div class="metric"><b id="liveValue">-</b><span>Live</span></div>
          <div class="metric"><b id="readyValue">-</b><span>Ready</span></div>
          <div class="metric"><b id="metricValue">-</b><span>Metric families</span></div>
          <div class="metric"><b id="lastStatus">Idle</b><span>Last action</span></div>
        </div>
        <div class="result" id="statusResult">No checks yet.</div>
      </section>

      <section class="panel">
        <div class="panel-head">
          <div>
            <div class="panel-kicker">Routing posture</div>
            <h2>Operational Signals</h2>
            <p class="hint">A compact view of the system this console is testing.</p>
          </div>
        </div>
        <div class="signal-map">
          <div class="signal-row">
            <span>Privacy guard</span>
            <div class="signal-track"><div class="signal-fill" style="width:92%"></div></div>
            <b>strict</b>
          </div>
          <div class="signal-row">
            <span>Cost ledger</span>
            <div class="signal-track"><div class="signal-fill" style="width:84%"></div></div>
            <b>tracked</b>
          </div>
          <div class="signal-row">
            <span>Cache path</span>
            <div class="signal-track"><div class="signal-fill" style="width:68%"></div></div>
            <b>phase 2</b>
          </div>
          <div class="signal-row">
            <span>Observability</span>
            <div class="signal-track"><div class="signal-fill" style="width:78%"></div></div>
            <b>active</b>
          </div>
        </div>
      </section>

      <section class="panel">
        <div class="panel-head">
          <div>
            <div class="panel-kicker">Prometheus scrape</div>
            <h2>Visual Observability</h2>
            <p class="hint">
              Charts are rendered from the same bounded labels exposed at /metrics.
            </p>
          </div>
          <button class="secondary" onclick="checkMetrics()">Refresh charts</button>
        </div>
        <div class="charts">
          <div class="chart">
            <div class="chart-title">HTTP requests by status</div>
            <div id="httpChart" class="empty-chart">Click Check metrics.</div>
          </div>
          <div class="chart">
            <div class="chart-title">Generate events by result</div>
            <div id="generateChart" class="empty-chart">No generation samples yet.</div>
          </div>
          <div class="chart">
            <div class="chart-title">Provider attempts</div>
            <div id="providerChart" class="empty-chart">No provider samples yet.</div>
          </div>
          <div class="chart">
            <div class="chart-title">Cache events</div>
            <div id="cacheChart" class="empty-chart">No cache samples yet.</div>
          </div>
          <div class="chart">
            <div class="chart-title">Generation latency buckets</div>
            <div id="latencyChart" class="empty-chart">No latency samples yet.</div>
          </div>
        </div>
      </section>

      <section class="panel">
        <div class="panel-head">
          <div>
            <div class="panel-kicker">Smoke test</div>
            <h2>Generate</h2>
            <p class="hint">Send a controlled prompt and refresh observability after the call.</p>
          </div>
        </div>
        <div class="grid-3">
          <label>Model
            <input id="model" value="gateway-default" />
          </label>
          <label>Max output tokens
            <input id="maxOutputTokens" type="number" min="16" value="64" />
          </label>
          <label>Temperature
            <input id="temperature" type="number" min="0" max="2" step="0.1"
              placeholder="optional" />
          </label>
        </div>
        <label style="margin-top:12px;">Prompt
          <textarea id="prompt">Say exactly: hello from the gateway</textarea>
        </label>
        <div class="actions" style="margin-top:12px;">
          <button onclick="runGenerate()">Run generate</button>
          <button class="ghost" onclick="setBlockedPrompt()">Try guardrail block</button>
        </div>
        <div class="result" id="generateResult">No generation yet.</div>
      </section>

      <section class="panel">
        <div class="panel-head">
          <div>
            <div class="panel-kicker">Ledger aggregates</div>
            <h2>Usage Analytics</h2>
            <p class="hint">
              Admin-only summaries with aggregate request, token, and cost evidence.
            </p>
          </div>
          <button onclick="loadAnalytics()">Load analytics</button>
        </div>
        <div class="grid-3">
          <label>Provider filter
            <select id="providerFilter">
              <option value="">All providers</option>
              <option value="openai">openai</option>
              <option value="llama">llama</option>
              <option value="qwen">qwen</option>
            </select>
          </label>
          <label>Status filter
            <select id="statusFilter">
              <option value="">All statuses</option>
              <option value="succeeded">succeeded</option>
              <option value="failed">failed</option>
              <option value="in_progress">in_progress</option>
            </select>
          </label>
          <label>Model filter
            <input id="analyticsModel" value="gateway-default" />
          </label>
        </div>
        <div class="actions" style="margin-top:12px;">
          <button class="ghost" onclick="clearAnalyticsFilters()">Clear filters</button>
        </div>
        <div class="quick-grid" id="analyticsCards" style="margin-top:12px;">
          <div class="quick-card"><strong>-</strong><span>Total requests</span></div>
          <div class="quick-card"><strong>-</strong><span>Total tokens</span></div>
          <div class="quick-card"><strong>-</strong><span>Reconciliation anomalies</span></div>
        </div>
        <div class="result" id="analyticsResult">No analytics loaded.</div>
      </section>

      <section class="panel">
        <div class="panel-head">
          <div>
            <div class="panel-kicker">Stitch screen translation</div>
            <h2>Design System</h2>
            <p class="hint">Local tokens and component states used by the gateway console.</p>
          </div>
        </div>
        <div class="system-strip">
          <div class="quick-card">
            <strong>Color Tokens</strong>
            <div class="token-row">
              <div class="swatch accent" title="Gateway green"></div>
              <div class="swatch blue" title="Provider blue"></div>
              <div class="swatch warm" title="Warning clay"></div>
              <div class="swatch ink" title="Console ink"></div>
            </div>
          </div>
          <div class="quick-card">
            <strong>Components</strong>
            <div class="component-preview">
              <button>Primary</button>
              <button class="ghost">Secondary</button>
              <div class="mini-input">Input field</div>
            </div>
          </div>
        </div>
      </section>
    </div>
  </main>

  <script>
    const $ = (id) => document.getElementById(id);

    function connection() {
      return {
        base_url: $("baseUrl").value.trim()
      };
    }

    function show(id, value) {
      $(id).textContent = typeof value === "string" ? value : JSON.stringify(value, null, 2);
    }

    function setLast(text, kind = "") {
      const el = $("lastStatus");
      el.textContent = text;
      el.className = kind;
    }

    function formatNumber(value) {
      if (!Number.isFinite(value)) return "0";
      if (value >= 1000) return value.toLocaleString();
      if (Math.abs(value - Math.round(value)) < 0.001) return String(Math.round(value));
      return value.toFixed(2);
    }

    function escapeHtml(value) {
      return String(value)
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");
    }

    function renderBarChart(id, values) {
      const el = $(id);
      const entries = Object.entries(values || {})
        .filter((entry) => Number(entry[1]) > 0)
        .sort((a, b) => Number(b[1]) - Number(a[1]));
      if (!entries.length) {
        el.className = "empty-chart";
        el.textContent = "No samples yet.";
        return;
      }
      const max = Math.max(...entries.map((entry) => Number(entry[1])), 1);
      el.className = "";
      el.innerHTML = entries.map(([label, rawValue]) => {
        const value = Number(rawValue);
        const width = Math.max(2, (value / max) * 100);
        const safeLabel = escapeHtml(label);
        return `
          <div class="bar-row" title="${safeLabel}: ${formatNumber(value)}">
            <div class="bar-label">${safeLabel}</div>
            <div class="bar-track"><div class="bar-fill" style="width:${width}%"></div></div>
            <div class="bar-value">${formatNumber(value)}</div>
          </div>
        `;
      }).join("");
    }

    function renderCharts(charts) {
      renderBarChart("httpChart", charts && charts.http_by_status);
      renderBarChart("generateChart", charts && charts.generate_by_result);
      renderBarChart("providerChart", charts && charts.provider_by_result);
      renderBarChart("cacheChart", charts && charts.cache_by_status);
      renderBarChart("latencyChart", charts && charts.generation_latency_buckets);
    }

    function analyticsTotalRequests(body) {
      return (body.request_statuses || [])
        .reduce((total, item) => total + Number(item.count || 0), 0);
    }

    function analyticsAnomalyCount(body) {
      const reconciliation = body.reconciliation || {};
      return Number(reconciliation.succeeded_requests_without_usage || 0)
        + Number(reconciliation.usage_rows_without_succeeded_attempt || 0)
        + Number(reconciliation.duplicate_charge_violations || 0);
    }

    function renderAnalyticsCards(data) {
      const body = data && data.body ? data.body : {};
      const totalRequests = analyticsTotalRequests(body);
      const totalTokens = body.usage ? Number(body.usage.total_tokens || 0) : 0;
      const anomalies = analyticsAnomalyCount(body);
      $("analyticsCards").innerHTML = `
        <div class="quick-card">
          <strong>${formatNumber(totalRequests)}</strong><span>Total requests</span>
        </div>
        <div class="quick-card">
          <strong>${formatNumber(totalTokens)}</strong><span>Total tokens</span>
        </div>
        <div class="quick-card">
          <strong>${formatNumber(anomalies)}</strong><span>Reconciliation anomalies</span>
        </div>
      `;
    }

    async function postJson(path, payload) {
      const response = await fetch(path, {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(payload)
      });
      return await response.json();
    }

    async function checkHealth() {
      setLast("Checking", "warn");
      const data = await postJson("/api/health", connection());
      show("statusResult", data);
      const liveOk = data.live && data.live.status_code === 200;
      const readyOk = data.ready && data.ready.status_code === 200;
      $("liveValue").textContent = liveOk ? "OK" : "Fail";
      $("liveValue").className = liveOk ? "ok" : "bad";
      $("readyValue").textContent = readyOk ? "OK" : "Check";
      $("readyValue").className = readyOk ? "ok" : "warn";
      $("gateway-pill").textContent = liveOk ? "Gateway live" : "Gateway check failed";
      setLast(liveOk ? "Health OK" : "Health issue", liveOk ? "ok" : "bad");
    }

    async function checkMetrics() {
      setLast("Checking", "warn");
      const data = await postJson("/api/metrics", connection());
      show("statusResult", data);
      const found = data.found || {};
      const count = Object.values(found).filter(Boolean).length;
      $("metricValue").textContent = String(count);
      $("metricValue").className = count ? "ok" : "bad";
      $("metrics-pill").textContent = count ? `${count} metric families found` : "Metrics missing";
      renderCharts(data.charts);
      setLast(data.ok ? "Metrics OK" : "Metrics issue", data.ok ? "ok" : "bad");
    }

    async function runGenerate() {
      setLast("Generating", "warn");
      const temperature = $("temperature").value.trim();
      const payload = {
        ...connection(),
        user_key: $("userKey").value,
        model: $("model").value.trim(),
        input: $("prompt").value,
        max_output_tokens: Number($("maxOutputTokens").value || 64),
        temperature: temperature === "" ? null : Number(temperature)
      };
      const data = await postJson("/api/generate", payload);
      show("generateResult", data);
      setLast(data.ok ? "Generate OK" : "Generate failed", data.ok ? "ok" : "bad");
      await checkMetrics();
    }

    async function loadAnalytics() {
      setLast("Loading analytics", "warn");
      const payload = {
        ...connection(),
        admin_key: $("adminKey").value,
        provider: $("providerFilter").value || null,
        model: $("analyticsModel").value.trim() || null,
        status: $("statusFilter").value || null
      };
      const data = await postJson("/api/analytics", payload);
      show("analyticsResult", data);
      if (data.ok) renderAnalyticsCards(data);
      setLast(data.ok ? "Analytics OK" : "Analytics failed", data.ok ? "ok" : "bad");
    }

    function setBlockedPrompt() {
      $("prompt").value = "Please BLOCK_ME_PHASE2 immediately.";
    }

    function clearAnalyticsFilters() {
      $("providerFilter").value = "";
      $("statusFilter").value = "";
      $("analyticsModel").value = "";
    }
  </script>
</body>
</html>
"""


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the LLM Gateway Test Console.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8501)
    args = parser.parse_args()
    uvicorn.run("app:app", host=args.host, port=args.port, reload=False, access_log=False)


if __name__ == "__main__":
    main()
