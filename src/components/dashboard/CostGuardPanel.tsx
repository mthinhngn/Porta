import "./dashboardPanels.css";

import { formatCompactCurrency } from "./dashboardTokens";
import type { CostGuardPanelProps, CostSuggestion } from "./types";

const costNavItems = [
  ["dashboard", "Dashboard"],
  ["extension", "Models"],
  ["security", "Guardrails"],
  ["list_alt", "Logs"],
  ["insights", "Pulse"],
  ["terminal", "Playground"],
  ["analytics", "Analytics"],
] as const;

function Icon({ name }: Readonly<{ name: string }>) {
  return <span className="gateway-icon material-symbols-outlined" aria-hidden="true" data-icon={name} />;
}

function trajectoryPoints(values: readonly number[], width = 760, height = 250): string {
  const max = Math.max(...values);
  const min = Math.min(...values);
  return values
    .map((value, index) => {
      const x = (index / Math.max(values.length - 1, 1)) * width;
      const y = height - ((value - min) / Math.max(max - min, 1)) * 196 - 28;
      return `${x},${y}`;
    })
    .join(" ");
}

function impactClass(suggestion: CostSuggestion): string {
  return `llmg-impact-${suggestion.impact.toLowerCase()}`;
}

function dollars(value: number): string {
  return `$${value.toLocaleString()}`;
}

export function CostGuardPanel({ data, className = "" }: Readonly<CostGuardPanelProps>) {
  const budgetRatio = Math.round((data.projectedSpend / data.budgetLimit) * 100);
  const overage = data.projectedSpend - data.budgetLimit;
  const circumference = 2 * Math.PI * 45;
  const gaugeOffset = circumference - Math.min(1.15, data.projectedSpend / data.budgetLimit) * circumference;

  return (
    <section className={`cost-screen ${className}`} aria-label="Cost Guard">
      <nav className="cost-sidebar" aria-label="LLM Gateway">
        <div className="cost-brand">
          <div>
            <Icon name="security" />
            <h1>LLM Gateway</h1>
          </div>
          <p>v1.0.4-stable</p>
        </div>

        <div className="cost-nav-list">
          {costNavItems.map(([icon, label]) => (
            <a className={label === "Guardrails" ? "is-active" : ""} href="#" key={label}>
              <Icon name={icon} />
              <span>{label}</span>
            </a>
          ))}
        </div>

        <div className="cost-sidebar-footer">
          <a href="#">
            <Icon name="settings" />
            <span>Settings</span>
          </a>
          <a href="#">
            <Icon name="help" />
            <span>Support</span>
          </a>
        </div>
      </nav>

      <header className="cost-topbar">
        <div className="cost-topbar-left">
          <button aria-label="Menu" type="button">
            <Icon name="menu" />
          </button>
          <span>Cost Guard</span>
          <label className="cost-search">
            <Icon name="search" />
            <input placeholder="Search costs..." type="text" />
          </label>
        </div>
        <div className="cost-topbar-actions">
          <button aria-label="Toggle theme" type="button">
            <Icon name="light_mode" />
          </button>
          <button aria-label="Notifications" type="button">
            <Icon name="notifications" />
          </button>
          <button aria-label="Account" type="button">
            <Icon name="account_circle" />
          </button>
          <button className="cost-deploy" type="button">
            Deploy
          </button>
        </div>
      </header>

      <main className="cost-canvas">
        <div className="cost-content">
          <div className="cost-hero-grid">
            <section className="cost-projected-card">
              <div className="cost-card-glow" />
              <header>
                <h2>
                  <Icon name="warning" />
                  Projected Month End Spend
                </h2>
                <span>{data.budgetWindow}</span>
              </header>

              <div className="cost-gauge-wrap">
                <svg className="cost-gauge" viewBox="0 0 100 100" role="img" aria-label="Projected spend gauge">
                  <circle cx="50" cy="50" fill="none" r="45" />
                  <circle
                    className="cost-gauge-arc"
                    cx="50"
                    cy="50"
                    fill="none"
                    r="45"
                    strokeDasharray={circumference}
                    strokeDashoffset={gaugeOffset}
                  />
                </svg>
                <div>
                  <strong>{formatCompactCurrency(data.projectedSpend)}</strong>
                  <span>{budgetRatio}% of Budget</span>
                </div>
              </div>

              <footer>
                <div>
                  <p>Hard Budget</p>
                  <span>{dollars(data.budgetLimit)}</span>
                </div>
                <div>
                  <p>Est. Overage</p>
                  <span>+{dollars(overage)}</span>
                </div>
              </footer>
            </section>

            <section className="cost-trajectory-card">
              <header>
                <h2>
                  <Icon name="show_chart" />
                  Spend Trajectory
                </h2>
                <div>
                  <button type="button">7D</button>
                  <button className="is-active" type="button">
                    30D
                  </button>
                </div>
              </header>
              <div className="cost-chart">
                <svg viewBox="0 0 760 300" role="img" aria-label="Spend Trajectory">
                  {[50, 100, 150, 200, 250].map((y) => (
                    <line className="cost-grid-line" x1="0" x2="760" y1={y} y2={y} key={y} />
                  ))}
                  <line className="cost-budget-line" x1="0" x2="760" y1="168" y2="168" />
                  <polyline className="cost-trajectory-fill" points={`0,300 ${trajectoryPoints(data.trajectory)} 760,300`} />
                  <polyline className="cost-trajectory-line" points={trajectoryPoints(data.trajectory)} />
                </svg>
              </div>
            </section>
          </div>

          <section className="cost-suggestions-card">
            <header>
              <h2>
                <Icon name="lightbulb" />
                Optimization Suggestions
              </h2>
              <span>Potential Savings: ~$850/mo</span>
            </header>
            <div className="cost-table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Recommendation</th>
                    <th>Impact</th>
                    <th>Est. Savings</th>
                    <th>Action</th>
                  </tr>
                </thead>
                <tbody>
                  {data.suggestions.map((suggestion) => (
                    <tr key={suggestion.id}>
                      <td>
                        <div className="cost-recommendation">
                          <Icon name={suggestion.icon} />
                          <div>
                            <p>{suggestion.recommendation}</p>
                            <span>{suggestion.detail}</span>
                          </div>
                        </div>
                      </td>
                      <td>
                        <span className={impactClass(suggestion)}>{suggestion.impact} Impact</span>
                      </td>
                      <td>{suggestion.estimatedSavings}</td>
                      <td>
                        <button type="button">{suggestion.action}</button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        </div>
      </main>
    </section>
  );
}
