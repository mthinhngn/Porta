import "./dashboardPanels.css";

import { routeClass } from "./dashboardTokens";
import type { GatewayPulsePanelProps } from "./types";

const navItems = [
  ["dashboard", "Dashboard"],
  ["extension", "Models"],
  ["security", "Guardrails"],
  ["list_alt", "Logs"],
  ["insights", "Pulse"],
  ["terminal", "Playground"],
  ["analytics", "Analytics"],
] as const;

const trafficBars = [40, 65, 30, 85, 50, 70, 90, 95, 60, 45, 35, 75, 55, 80, 40];

function Icon({ name }: Readonly<{ name: string }>) {
  return <span className="gateway-icon material-symbols-outlined" aria-hidden="true" data-icon={name} />;
}

export function GatewayPulsePanel({
  data,
  className = "",
  variant = "dark",
}: Readonly<GatewayPulsePanelProps>) {
  const { metrics, routes, providerLoad } = data;

  return (
    <section className={`gateway-screen gateway-screen-${variant} ${className}`} aria-label="Gateway Pulse">
      <div className="gateway-shader" aria-hidden="true" />
      <div className="gateway-particle-field" aria-hidden="true">
        {Array.from({ length: 24 }, (_, index) => (
          <span key={index} />
        ))}
      </div>

      <nav className="gateway-sidebar" aria-label="LLM Gateway">
        <div className="gateway-brand">
          <div>
            <Icon name="route" />
            <h1>LLM Gateway</h1>
          </div>
          <p>v1.0.4-stable</p>
        </div>

        <div className="gateway-nav-list">
          {navItems.map(([icon, label]) => (
            <a className={label === "Pulse" ? "is-active" : ""} href="#" key={label}>
              <Icon name={icon} />
              {label}
            </a>
          ))}
        </div>

        <div className="gateway-sidebar-footer">
          <a href="#">
            <Icon name="settings" />
            Settings
          </a>
          <a href="#">
            <Icon name="help" />
            Support
          </a>
          <div className="gateway-avatar" aria-label="User profile" />
        </div>
      </nav>

      <header className="gateway-topbar">
        <div className="gateway-topbar-left">
          <h2>Gateway Pulse</h2>
          <label className="gateway-search">
            <Icon name="search" />
            <input placeholder="Search logs, endpoints..." type="text" />
          </label>
        </div>
        <div className="gateway-topbar-actions">
          <button aria-label="Toggle theme" type="button">
            <Icon name="light_mode" />
          </button>
          <button aria-label="Notifications" type="button">
            <Icon name="notifications" />
          </button>
          <button aria-label="Account" type="button">
            <Icon name="account_circle" />
          </button>
          <button className="gateway-deploy" type="button">
            Deploy
          </button>
        </div>
      </header>

      <main className="gateway-canvas">
        <div className="gateway-metrics">
          <article className="gateway-metric-card">
            <div>
              <span>Global TPS</span>
              <Icon name="bolt" />
            </div>
            <p>
              {metrics.globalTps.toLocaleString()}
              <em>{metrics.globalTpsDelta}</em>
            </p>
          </article>
          <article className="gateway-metric-card">
            <div>
              <span>Avg Latency</span>
              <Icon name="speed" />
            </div>
            <p>
              {metrics.avgLatencyMs}
              <em>ms</em>
            </p>
          </article>
          <article className="gateway-metric-card">
            <div>
              <span>Error Rate</span>
              <Icon name="warning" />
            </div>
            <p>
              {metrics.errorRate.toFixed(2)}
              <em>%</em>
            </p>
          </article>
          <article className="gateway-metric-card">
            <div>
              <span>Active Nodes</span>
              <Icon name="dns" />
            </div>
            <p>
              {metrics.activeNodes}
              <em>/ {metrics.totalNodes}</em>
            </p>
          </article>
        </div>

        <div className="gateway-main-grid">
          <section className="gateway-live-feed">
            <header>
              <div>
                <span className="gateway-live-dot" />
                <span>Live Feed</span>
              </div>
              <b>STREAMING</b>
            </header>
            <div className="gateway-waterfall">
              <div className="gateway-fade-top" />
              <div className="gateway-fade-bottom" />
              <div className="gateway-waterfall-scroll">
                {[...routes, ...routes.slice(0, 2)].map((route, index) => (
                  <div className={`gateway-feed-row ${routeClass(route.route)}`} key={`${route.id}-${index}`}>
                    <div>
                      <span>{route.status}</span>
                      <span>{route.model}</span>
                      <span>{route.provider}</span>
                    </div>
                    <div>
                      <span>{route.latencyMs}ms</span>
                      <span>{route.tokensPerSecond ? `${route.tokensPerSecond} t/s` : "-- t/s"}</span>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </section>

          <aside className="gateway-side-panel">
            <section className="gateway-traffic-card">
              <header>Traffic Velocity (1m)</header>
              <div className="gateway-bars">
                {trafficBars.map((height, index) => (
                  <span
                    className={index === 7 ? "is-warning" : ""}
                    key={`${height}-${index}`}
                    style={{ height: `${height}%` }}
                  />
                ))}
                <i />
              </div>
            </section>

            <section className="gateway-provider-card">
              <header>Provider Load</header>
              <div className="gateway-provider-list">
                {providerLoad.map((provider) => (
                  <div className="gateway-provider-row" key={provider.provider}>
                    <div>
                      <span>{provider.provider}</span>
                      <span>{provider.load}%</span>
                    </div>
                    <div>
                      <span className={routeClass(provider.route)} style={{ width: `${provider.load}%` }} />
                    </div>
                  </div>
                ))}
              </div>
            </section>
          </aside>
        </div>
      </main>
    </section>
  );
}
