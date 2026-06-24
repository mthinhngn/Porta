import { useState } from "react";

import "./dashboardPanels.css";

import { routeClass } from "./dashboardTokens";
import type { ModelPlaygroundPanelProps, ModelRunResult } from "./types";

const playgroundNavItems = [
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

function CodeBlock({ result }: Readonly<{ result: ModelRunResult }>) {
  return (
    <div className="playground-code-card">
      <div className="playground-code-header">
        <span>{result.language}</span>
        <button aria-label="Copy code" type="button">
          <Icon name="content_copy" />
        </button>
      </div>
      <pre>
        <code>
          {result.code.map((line, index) => (
            <span style={{ animationDelay: `${index * 60}ms` }} key={`${result.id}-line-${index}`}>
              {line || " "}
            </span>
          ))}
        </code>
      </pre>
    </div>
  );
}

function ModelColumn({ result }: Readonly<{ result: ModelRunResult }>) {
  return (
    <section className={`playground-model-column ${routeClass(result.route)}`}>
      <header className="playground-model-header">
        <div>
          <div className="playground-model-mark">{result.model.slice(0, 1)}</div>
          <div>
            <h3>{result.model}</h3>
            <span>{result.provider}</span>
          </div>
        </div>
        <span className="playground-temp">Temp: {result.temperature}</span>
      </header>

      <div className="playground-output terminal-scroll">
        <p>{result.response}</p>
        <CodeBlock result={result} />
        <p>{result.note}</p>
      </div>

      <footer className="playground-metrics-footer">
        <div>
          <span>Latency</span>
          <p className={result.latencyMs > 1000 ? "is-error" : ""}>
            {result.latencyMs}ms
            {result.latencyWinner ? <b>Winner</b> : null}
          </p>
        </div>
        <div>
          <span>Tokens (In/Out)</span>
          <p>
            {result.inputTokens} / {result.outputTokens}
          </p>
        </div>
        <div>
          <span>Est. Cost</span>
          <p>
            {result.estimatedCost}
            {result.costWinner ? <b>Winner</b> : null}
          </p>
        </div>
      </footer>
    </section>
  );
}

export function ModelPlaygroundPanel({
  data,
  className = "",
  errorMessage = null,
  isRunning = false,
  onRunPrompt,
}: Readonly<ModelPlaygroundPanelProps>) {
  const [prompt, setPrompt] = useState("");
  const tokenEstimate = prompt.trim() ? Math.max(1, Math.ceil(prompt.trim().length / 4)) : data.tokenEstimate;
  const canRun = prompt.trim().length > 0 && !isRunning;

  function handleRun() {
    if (!canRun) {
      return;
    }
    onRunPrompt?.(prompt.trim());
  }

  return (
    <section className={`playground-screen ${className}`} aria-label="Model Playground">
      <nav className="playground-sidebar" aria-label="Model Playground navigation">
        <div className="playground-brand">
          <h1>Model Playground</h1>
          <p>v1.0.4-stable</p>
        </div>

        <div className="playground-nav-list">
          {playgroundNavItems.map(([icon, label]) => (
            <a className={label === "Playground" ? "is-active" : ""} href="#" key={label}>
              <Icon name={icon} />
              <span>{label}</span>
            </a>
          ))}
        </div>

        <div className="playground-sidebar-footer">
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

      <header className="playground-topbar">
        <div className="playground-topbar-left">
          <div className="playground-title-lockup">
            <Icon name="hub" />
            <h1>Model Playground</h1>
          </div>
          <label className="playground-search">
            <Icon name="search" />
            <input placeholder="Search models, prompt history..." type="text" />
          </label>
        </div>
        <div className="playground-topbar-actions">
          <button aria-label="Help" type="button">
            <Icon name="help" />
          </button>
          <button aria-label="Toggle theme" type="button">
            <Icon name="light_mode" />
          </button>
          <button aria-label="Notifications" className="has-alert" type="button">
            <Icon name="notifications" />
          </button>
          <button aria-label="Account" type="button">
            <Icon name="account_circle" />
          </button>
          <i />
          <button className="playground-deploy" type="button">
            <Icon name="rocket_launch" />
            <span>Deploy</span>
          </button>
        </div>
      </header>

      <main className="playground-canvas">
        <div className="playground-comparison">
          {data.models.map((result) => (
            <ModelColumn result={result} key={result.id} />
          ))}
        </div>

        <div className="playground-prompt-shell">
          <div className="playground-prompt-box">
            <textarea
              aria-label="Prompt"
              onChange={(event) => setPrompt(event.target.value)}
              placeholder="Write a prompt to test both models..."
              value={prompt}
            />
            {errorMessage ? <p className="playground-error">{errorMessage}</p> : null}
            <div className="playground-prompt-actions">
              <div>
                <button title="System Prompt" type="button">
                  <Icon name="tune" />
                </button>
                <button title="Attach Data" type="button">
                  <Icon name="attach_file" />
                </button>
                <i />
                <span>Tokens: ~{tokenEstimate}</span>
              </div>
              <button
                className="playground-run"
                disabled={!canRun}
                onClick={handleRun}
                type="button"
              >
                <span>{isRunning ? "Running" : "Run"}</span>
                <Icon name="play_arrow" />
              </button>
            </div>
          </div>
        </div>
      </main>
    </section>
  );
}
