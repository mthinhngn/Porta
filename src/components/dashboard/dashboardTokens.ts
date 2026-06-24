export const dashboardTokens = {
  background: "#fbf8ff",
  surface: "#fbf8ff",
  surfaceRaised: "#f4f2fd",
  surfaceHigh: "#eeedf7",
  outline: "#6c7a71",
  outlineStrong: "#bbcabf",
  text: "#1a1b22",
  textMuted: "#3c4a42",
  primary: "#006c49",
  primaryContainer: "#10b981",
  secondary: "#565e74",
  tertiary: "#494bd6",
  fallback: "#f59e0b",
  error: "#ba1a1a",
  danger: "#ba1a1a",
  radius: "4px",
  radiusLarge: "8px",
  fontBody: "Inter, system-ui, sans-serif",
  fontMono: '"JetBrains Mono", monospace',
} as const;

export function routeClass(route: string): string {
  if (route === "primary") return "llmg-route-primary";
  if (route === "fallback") return "llmg-route-fallback";
  if (route === "error") return "llmg-route-error";
  return "llmg-route-neutral";
}

export function formatCompactCurrency(value: number): string {
  if (value >= 1000) return `$${(value / 1000).toFixed(1)}k`;
  return `$${value.toLocaleString()}`;
}
