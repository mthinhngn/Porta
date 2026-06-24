export interface GenerateRequestPayload {
  model: string;
  input: string;
  temperature?: number;
  max_output_tokens?: number;
}

export interface GenerateResponsePayload {
  request_id: string;
  output: string;
  provider: string;
  model: string;
  tokens: {
    input_tokens: number;
    output_tokens: number;
    total_tokens: number;
  };
  cost: {
    amount: string;
    currency: string;
  };
  routing_reason: string;
  cache_status: string;
  served_from_cache: boolean;
  attempt_count: number;
  latency_ms: number;
}

interface GatewayErrorPayload {
  error?: {
    message?: string;
    code?: string;
  };
}

const gatewayApiKey = import.meta.env.VITE_LLM_GATEWAY_API_KEY as string | undefined;

export async function generateText(
  payload: GenerateRequestPayload,
): Promise<GenerateResponsePayload> {
  const headers = new Headers({
    "Content-Type": "application/json",
  });

  if (gatewayApiKey) {
    headers.set("Authorization", `Bearer ${gatewayApiKey}`);
  }

  const response = await fetch("/v1/generate", {
    method: "POST",
    headers,
    body: JSON.stringify(payload),
  });

  if (!response.ok) {
    let errorMessage = `Gateway request failed with HTTP ${response.status}.`;
    try {
      const body = (await response.json()) as GatewayErrorPayload;
      if (body.error?.message) {
        errorMessage = body.error.message;
      }
    } catch {
      // Keep the status-based fallback when the backend response is not JSON.
    }
    throw new Error(errorMessage);
  }

  return (await response.json()) as GenerateResponsePayload;
}
