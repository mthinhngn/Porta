import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { useEffect, useState } from "react";

import { CostGuardPanel, GatewayPulsePanel, ModelPlaygroundPanel } from "./components/dashboard";
import type { ModelRunResult, GatewayRouteEvent } from "./components/dashboard/types";
import {
  costGuardMockData,
  gatewayPulseMockData,
  modelPlaygroundMockData,
} from "./data/dashboardMockData";
import { generateText } from "./api/gateway";
import "./styles/dashboardApp.css";

type PanelKey = "gateway" | "playground" | "cost";

function getPanelFromHash(): PanelKey {
  const panel = window.location.hash.replace("#", "");
  if (panel === "playground" || panel === "cost") {
    return panel;
  }
  return "gateway";
}

function isLightGateway(): boolean {
  return window.location.hash === "#gateway-light";
}

interface ParsedMetrics {
  totalRequests: number;
  durationSum: number;
  durationCount: number;
  totalGenerations: number;
  failedGenerations: number;
}

function parsePrometheusMetrics(text: string): ParsedMetrics {
  const lines = text.split("\n");
  let totalRequests = 0;
  let durationSum = 0;
  let durationCount = 0;
  let totalGenerations = 0;
  let failedGenerations = 0;

  for (const line of lines) {
    if (line.startsWith("#") || !line.trim()) continue;

    const match = line.match(/^([a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{([^}]*)\})?\s+([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)$/);
    if (!match) continue;

    const name = match[1];
    const labelsStr = match[2];
    const valStr = match[3];
    const value = parseFloat(valStr);

    if (name === "llm_gateway_http_requests_total") {
      totalRequests += value;
    } else if (name === "llm_gateway_generation_duration_seconds_sum") {
      durationSum = value;
    } else if (name === "llm_gateway_generation_duration_seconds_count") {
      durationCount = value;
    } else if (name === "llm_gateway_generate_events_total") {
      totalGenerations += value;
      if (labelsStr && labelsStr.includes('result="failure"')) {
        failedGenerations += value;
      }
    }
  }

  return {
    totalRequests,
    durationSum,
    durationCount,
    totalGenerations,
    failedGenerations,
  };
}

function DashboardApp() {
  const [activePanel, setActivePanel] = useState<PanelKey>(getPanelFromHash);
  const [gatewayLight, setGatewayLight] = useState<boolean>(isLightGateway);

  // Live Gateway Pulse metrics state
  const [pulseData, setPulseData] = useState(gatewayPulseMockData);
  const [liveRoutes, setLiveRoutes] = useState<GatewayRouteEvent[]>(gatewayPulseMockData.routes);
  const [prevRequestCount, setPrevRequestCount] = useState<number | null>(null);

  // Live Playground state
  const [playgroundData, setPlaygroundData] = useState(modelPlaygroundMockData);
  const [isRunning, setIsRunning] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  useEffect(() => {
    const onHashChange = () => {
      setActivePanel(getPanelFromHash());
      setGatewayLight(isLightGateway());
    };
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);

  // Poll metrics and health every 5 seconds
  useEffect(() => {
    let intervalId: any;

    async function pollMetrics() {
      try {
        const healthRes = await fetch("/health/ready");
        const isReady = healthRes.ok;

        const metricsRes = await fetch("/metrics");
        if (!metricsRes.ok) throw new Error("Failed to fetch metrics");
        const text = await metricsRes.text();

        const parsed = parsePrometheusMetrics(text);

        let tps = 0;
        if (prevRequestCount !== null) {
          tps = Math.max(0, (parsed.totalRequests - prevRequestCount) / 5);
        }
        setPrevRequestCount(parsed.totalRequests);

        setPulseData(prev => ({
          ...prev,
          metrics: {
            globalTps: parseFloat(tps.toFixed(1)),
            globalTpsDelta: tps > 0 ? `+${Math.round(tps * 10)}%` : "0%",
            avgLatencyMs: parsed.durationCount > 0
              ? Math.round((parsed.durationSum / parsed.durationCount) * 1000)
              : 0,
            errorRate: parsed.totalGenerations > 0
              ? parsed.failedGenerations / parsed.totalGenerations
              : 0,
            activeNodes: isReady ? 12 : 0,
            totalNodes: 12,
          },
          routes: liveRoutes,
          providerLoad: [
            { provider: "OpenAI", route: "primary", load: parsed.totalGenerations > 0 ? Math.round(((parsed.totalGenerations - parsed.failedGenerations) / parsed.totalGenerations) * 100) : 100 },
            { provider: "Local (Ollama)", route: "neutral", load: parsed.failedGenerations > 0 ? 50 : 0 },
            { provider: "Redis Cache", route: "neutral", load: parsed.totalGenerations > 0 ? 30 : 0 },
          ]
        }));
      } catch (e) {
        // Fallback silently if backend is not responding
      }
    }

    pollMetrics();
    intervalId = setInterval(pollMetrics, 5000);
    return () => clearInterval(intervalId);
  }, [prevRequestCount, liveRoutes]);

  async function handleRunPrompt(prompt: string) {
    setIsRunning(true);
    setErrorMessage(null);

    try {
      const startTime = performance.now();
      const res = await generateText({
        model: "gateway-default",
        input: prompt,
      });
      const durationMs = Math.round(performance.now() - startTime);

      const winningModel: ModelRunResult = {
        id: "active-winner",
        model: res.model,
        provider: res.provider,
        route: res.served_from_cache ? "neutral" : "primary",
        latencyMs: res.latency_ms || durationMs,
        inputTokens: res.tokens.input_tokens,
        outputTokens: res.tokens.output_tokens,
        estimatedCost: `$${parseFloat(res.cost.amount).toFixed(4)}`,
        latencyWinner: true,
        temperature: 0.7,
        language: "text",
        response: res.output,
        note: res.served_from_cache
          ? `Served from Redis cache (status: ${res.cache_status}).`
          : `Routed to ${res.provider} (attempts: ${res.attempt_count}, routing: ${res.routing_reason}).`,
        code: res.output.split("\n"),
      };

      setPlaygroundData(prev => ({
        prompt,
        tokenEstimate: Math.max(1, Math.ceil(prompt.length / 4)),
        models: [
          winningModel,
          prev.models[1] || modelPlaygroundMockData.models[1],
        ],
      }));

      // Add to live routes feed for Pulse screen
      setLiveRoutes(prev => [
        {
          id: res.request_id || `req-${Math.floor(Math.random() * 10000)}`,
          status: "200 OK",
          model: res.model,
          provider: res.provider,
          route: res.served_from_cache ? "fallback" : "primary",
          latencyMs: res.latency_ms || durationMs,
          tokensPerSecond: res.tokens.output_tokens > 0 
            ? Math.round(res.tokens.output_tokens / ((res.latency_ms || durationMs) / 1000))
            : 0,
          volume: res.tokens.total_tokens,
        },
        ...prev,
      ]);
    } catch (err: any) {
      setErrorMessage(err.message || "An error occurred while generating response.");
    } finally {
      setIsRunning(false);
    }
  }

  if (activePanel === "playground") {
    return (
      <ModelPlaygroundPanel
        data={playgroundData}
        errorMessage={errorMessage}
        isRunning={isRunning}
        onRunPrompt={handleRunPrompt}
      />
    );
  }

  if (activePanel === "cost") {
    return <CostGuardPanel data={costGuardMockData} />;
  }

  return <GatewayPulsePanel data={pulseData} variant={gatewayLight ? "light" : "dark"} />;
}

createRoot(document.getElementById("root") as HTMLElement).render(
  <StrictMode>
    <DashboardApp />
  </StrictMode>,
);
