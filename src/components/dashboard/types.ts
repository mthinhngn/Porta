export type GatewayRoute = "primary" | "fallback" | "neutral" | "error";

export interface GatewayMetricSnapshot {
  globalTps: number;
  globalTpsDelta: string;
  avgLatencyMs: number;
  errorRate: number;
  activeNodes: number;
  totalNodes: number;
}

export interface GatewayRouteEvent {
  id: string;
  status: string;
  model: string;
  provider: string;
  route: GatewayRoute;
  latencyMs: number;
  tokensPerSecond: number;
  volume: number;
}

export interface GatewayProviderLoad {
  provider: string;
  route: GatewayRoute;
  load: number;
}

export interface GatewayPulsePanelProps {
  data: Readonly<{
    metrics: GatewayMetricSnapshot;
    routes: GatewayRouteEvent[];
    providerLoad: GatewayProviderLoad[];
  }>;
  className?: string;
  variant?: "dark" | "light";
}

export interface ModelRunResult {
  id: string;
  model: string;
  provider: string;
  route: GatewayRoute;
  latencyMs: number;
  inputTokens: number;
  outputTokens: number;
  estimatedCost: string;
  latencyWinner?: boolean;
  costWinner?: boolean;
  temperature: number;
  language: string;
  response: string;
  note: string;
  code: string[];
}

export interface ModelPlaygroundPanelProps {
  data: Readonly<{
    prompt: string;
    tokenEstimate: number;
    models: ModelRunResult[];
  }>;
  className?: string;
  errorMessage?: string | null;
  isRunning?: boolean;
  onRunPrompt?: (prompt: string) => void;
}

export type CircuitBreakerState = "nominal" | "armed" | "tripped";

export interface CostSuggestion {
  id: string;
  recommendation: string;
  detail: string;
  impact: "High" | "Medium" | "Low";
  estimatedSavings: string;
  action: string;
  icon: string;
}

export interface CostGuardPanelProps {
  data: Readonly<{
    budgetLimit: number;
    projectedSpend: number;
    budgetWindow: string;
    circuitBreaker: CircuitBreakerState;
    trajectory: number[];
    suggestions: CostSuggestion[];
  }>;
  className?: string;
}
