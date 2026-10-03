"""WebUI for HA System 1 / Laya Decision Engine Home Assistant Ingress."""


def render_gui_html() -> str:
    """Generate self-contained, offline-first HTML/CSS/JS interface with flow visibility and debug tracing."""
    return """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>HA System 1 — Decision & Domotica Fast-Path Engine</title>
  <style>
    :root {
      --bg: #090d16;
      --card-bg: #131b2e;
      --card-sub: #0c1322;
      --border: #222f49;
      --border-focus: #3b82f6;
      --text: #f8fafc;
      --text-muted: #94a3b8;
      --text-dim: #64748b;
      --primary: #3b82f6;
      --primary-hover: #2563eb;
      --primary-light: rgba(59, 130, 246, 0.15);
      --success: #10b981;
      --success-light: rgba(16, 185, 129, 0.15);
      --warning: #f59e0b;
      --warning-light: rgba(245, 158, 11, 0.15);
      --purple: #8b5cf6;
      --purple-light: rgba(139, 92, 246, 0.15);
      --danger: #ef4444;
      --cyan: #06b6d4;
      --radius: 10px;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
    body { background-color: var(--bg); color: var(--text); padding: 24px 16px; min-height: 100vh; }
    .container { max-width: 1040px; margin: 0 auto; }

    /* Header */
    header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 24px; padding-bottom: 18px; border-bottom: 1px solid var(--border); }
    .logo-area h1 { font-size: 22px; font-weight: 700; color: #fff; display: flex; align-items: center; gap: 10px; }
    .logo-area p { font-size: 13px; color: var(--text-muted); margin-top: 4px; }
    .badge-bar { display: flex; gap: 8px; flex-wrap: wrap; }
    .badge { font-size: 12px; padding: 5px 12px; border-radius: 9999px; background: #1e293b; color: #e2e8f0; font-weight: 500; display: inline-flex; align-items: center; gap: 6px; }
    .badge.xpu { background: var(--primary-light); color: #60a5fa; border: 1px solid rgba(59, 130, 246, 0.3); }
    .badge.ready { background: var(--success-light); color: #34d399; border: 1px solid rgba(16, 185, 129, 0.3); }
    .badge.provider { background: var(--purple-light); color: #c084fc; border: 1px solid rgba(139, 92, 246, 0.3); }

    /* Cards */
    .card { background: var(--card-bg); border: 1px solid var(--border); border-radius: var(--radius); padding: 22px; margin-bottom: 20px; box-shadow: 0 4px 20px rgba(0,0,0,0.25); }
    .card-title { font-size: 14px; font-weight: 700; margin-bottom: 14px; color: #cbd5e1; text-transform: uppercase; letter-spacing: 0.6px; display: flex; justify-content: space-between; align-items: center; }

    /* Chips */
    .chips-title { font-size: 12px; color: var(--text-muted); margin-bottom: 8px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px; }
    .chips { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 18px; }
    .chip { font-size: 12px; background: var(--card-sub); border: 1px solid var(--border); color: #94a3b8; padding: 7px 14px; border-radius: 8px; cursor: pointer; transition: all 0.15s ease; user-select: none; }
    .chip:hover { border-color: var(--primary); color: #fff; background: var(--primary-light); }

    /* Inputs */
    textarea { width: 100%; background: var(--card-sub); border: 1px solid var(--border); border-radius: 8px; padding: 14px; color: #fff; font-size: 14px; outline: none; transition: border 0.15s; min-height: 85px; resize: vertical; margin-bottom: 14px; }
    textarea:focus { border-color: var(--border-focus); }

    .context-toggle { font-size: 12px; font-weight: 600; color: var(--primary); cursor: pointer; margin-bottom: 12px; display: inline-block; user-select: none; }
    .context-panel { display: none; margin-bottom: 14px; }
    .context-panel.open { display: block; }

    .action-row { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 12px; }
    .btn { background: var(--primary); color: #fff; border: none; padding: 10px 22px; border-radius: 8px; font-weight: 600; cursor: pointer; transition: all 0.15s; font-size: 14px; display: inline-flex; align-items: center; gap: 8px; }
    .btn:hover { background: var(--primary-hover); transform: translateY(-1px); }
    .btn:disabled { opacity: 0.5; cursor: not-allowed; transform: none; }
    .btn.execute { background: var(--success); }
    .btn.execute:hover { background: #059669; }

    /* Visual Flow Pipeline Diagram */
    .flow-pipeline { display: flex; align-items: center; justify-content: space-between; gap: 6px; margin: 20px 0 24px 0; padding: 20px 14px; background: var(--card-sub); border-radius: 8px; border: 1px solid var(--border); position: relative; }
    .flow-node { position: relative; display: flex; flex-direction: column; align-items: center; text-align: center; min-width: 145px; cursor: pointer; }
    .flow-icon { width: 42px; height: 42px; border-radius: 50%; background: #1e293b; border: 2px solid var(--border); display: flex; align-items: center; justify-content: center; font-size: 18px; margin-bottom: 6px; transition: all 0.25s ease; }
    .flow-icon.active { border-color: var(--primary); background: var(--primary-light); color: #60a5fa; box-shadow: 0 0 14px rgba(59,130,246,0.35); }
    .flow-icon.fast-path { border-color: var(--success); background: var(--success-light); color: #34d399; box-shadow: 0 0 14px rgba(16,185,129,0.35); }
    .flow-icon.cloud { border-color: var(--purple); background: var(--purple-light); color: #c084fc; box-shadow: 0 0 14px rgba(139,92,246,0.35); }
    .flow-icon.bypassed { border-color: #334155; background: #0f172a; color: #475569; opacity: 0.6; }
    .flow-label { font-size: 12px; font-weight: 700; color: #fff; margin-bottom: 2px; }
    .flow-sub { font-size: 11px; color: var(--text-muted); }
    .flow-arrow { font-size: 16px; color: var(--text-dim); }

    /* Hover Box Tooltip */
    .flow-tooltip {
      visibility: hidden;
      opacity: 0;
      position: absolute;
      top: 118%;
      left: 50%;
      transform: translateX(-50%);
      width: 250px;
      background: #0f172a;
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 12px;
      box-shadow: 0 10px 30px rgba(0,0,0,0.6);
      z-index: 100;
      transition: opacity 0.2s ease, visibility 0.2s ease;
      pointer-events: none;
      text-align: left;
    }
    .flow-node:hover .flow-tooltip {
      visibility: visible;
      opacity: 1;
    }
    .flow-tooltip-title {
      font-size: 12px;
      font-weight: 700;
      color: #60a5fa;
      margin-bottom: 4px;
      display: flex;
      justify-content: space-between;
      align-items: center;
    }
    .flow-tooltip-body {
      font-size: 11px;
      color: #cbd5e1;
      line-height: 1.45;
      margin-bottom: 6px;
    }
    .flow-tooltip-meta {
      font-size: 10px;
      color: var(--text-muted);
      border-top: 1px solid rgba(255,255,255,0.08);
      padding-top: 5px;
      font-family: monospace;
    }

    /* Results layout */
    #results-area { display: none; }
    .tab-bar { display: flex; gap: 8px; margin-bottom: 16px; border-bottom: 1px solid var(--border); padding-bottom: 10px; }
    .tab-btn { background: transparent; border: none; color: var(--text-muted); font-size: 13px; font-weight: 600; padding: 6px 14px; border-radius: 6px; cursor: pointer; transition: all 0.15s; }
    .tab-btn.active { background: var(--primary-light); color: #60a5fa; border: 1px solid rgba(59,130,246,0.3); }

    .result-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 14px; margin-bottom: 20px; }
    .result-box { background: var(--card-sub); border: 1px solid var(--border); border-radius: 8px; padding: 14px; }
    .result-label { font-size: 11px; text-transform: uppercase; color: var(--text-muted); font-weight: 600; margin-bottom: 6px; }
    .result-val { font-size: 17px; font-weight: 700; color: #fff; }

    .pill { display: inline-block; padding: 3px 10px; border-radius: 6px; font-size: 12px; font-weight: 700; text-transform: uppercase; }
    .pill.quick { background: var(--success-light); color: #34d399; }
    .pill.smarthome { background: rgba(6, 182, 212, 0.2); color: #22d3ee; }
    .pill.general { background: var(--primary-light); color: #60a5fa; }
    .pill.code { background: var(--purple-light); color: #c084fc; }
    .pill.deep { background: var(--warning-light); color: #fbbf24; }

    /* Trace List */
    .trace-card { background: var(--card-sub); border: 1px solid var(--border); border-radius: 8px; padding: 14px; margin-bottom: 14px; }
    .trace-item { display: flex; justify-content: space-between; align-items: center; padding: 8px 0; border-bottom: 1px solid rgba(255,255,255,0.05); font-size: 13px; }
    .trace-item:last-child { border-bottom: none; }
    .trace-k { color: var(--text-muted); display: flex; align-items: center; gap: 8px; }
    .trace-v { font-weight: 600; color: #e2e8f0; font-family: monospace; }
    .trace-check { color: var(--success); font-weight: 700; }

    pre { background: var(--card-sub); padding: 14px; border-radius: 8px; font-size: 12px; overflow-x: auto; color: #cbd5e1; border: 1px solid var(--border); margin-top: 10px; line-height: 1.5; }
    details { margin-top: 14px; cursor: pointer; font-size: 13px; color: var(--text-muted); }
  </style>
</head>
<body>
<div class="container">
  <header>
    <div class="logo-area">
      <h1>⚡ HA System 1</h1>
      <p>Local Sub-50ms System 1 Decision & Fast-Path Domotica Engine</p>
    </div>
    <div class="badge-bar">
      <div id="badge-device" class="badge xpu">Device: Loading...</div>
      <div id="badge-status" class="badge ready">Status: Loading...</div>
      <div id="badge-registry" class="badge provider">Registry: Loading...</div>
    </div>
  </header>

  <div class="card">
    <div class="card-title">Decision & Routing Playground</div>

    <div class="chips-title">Quick Presets:</div>
    <div class="chips">
      <div class="chip" onclick="loadPreset('lights_off')">💡 Doe de lampen in de serre uit</div>
      <div class="chip" onclick="loadPreset('lights_on')">💡 Zet de spots in de woonkamer aan</div>
      <div class="chip" onclick="loadPreset('climate')">🌡️ Zet de thermostaat op 20.5 graden</div>
      <div class="chip" onclick="loadPreset('quick')">❓ Wat is de hoofdstad van Australië?</div>
      <div class="chip" onclick="loadPreset('code')">🐍 Schrijf een Python script voor data analyse</div>
      <div class="chip" onclick="loadPreset('deep')">📊 Bereken de annuïtaire hypotheek voor 450k</div>
    </div>

    <label style="font-size: 12px; font-weight: 600; color: #94a3b8; display: block; margin-bottom: 6px;">Prompt / User Command:</label>
    <textarea id="prompt-input" placeholder="Type a smart home command or user message to evaluate routing and trace the execution flow..."></textarea>

    <div class="context-toggle" onclick="toggleContext()">+ Toggle Preceding Conversation History (Optional)</div>
    <div id="context-panel" class="context-panel">
      <label style="font-size: 12px; color: #94a3b8; display: block; margin-bottom: 4px;">Context (Preceding turns):</label>
      <textarea id="context-input" style="min-height: 60px;" placeholder="user: Wil je de verwarming aanzetten?\nassistant: Zeker, welke temperatuur wens je?"></textarea>
    </div>

    <div class="action-row">
      <span id="timing-hint" style="font-size: 12px; color: var(--text-muted);">Ready for live evaluation</span>
      <div style="display: flex; gap: 10px;">
        <button id="route-btn" class="btn" onclick="evaluateTurn()">
          <span>🚀 Evaluate & Trace Flow</span>
        </button>
      </div>
    </div>
  </div>

  <div id="results-area" class="card">
    <div class="card-title">
      <span>Evaluation & Decision Trace</span>
      <span id="flow-verdict-badge" class="pill smarthome">Fast-Path</span>
    </div>

    <!-- Visual Flow Pipeline Diagram with Hover Details -->
    <div class="flow-pipeline">
      <!-- Node 1: Input Normalization -->
      <div class="flow-node">
        <div id="flow-node-input" class="flow-icon active">💬</div>
        <div class="flow-label">1. User Input</div>
        <div id="flow-sub-input" class="flow-sub">Normalized</div>
        <div class="flow-tooltip">
          <div class="flow-tooltip-title"><span>Input Normalization</span><span>Step 1</span></div>
          <div class="flow-tooltip-body">Strips chat wrapper prefixes, telegram usernames, and folds diacritics. Checks deterministic follow-up markers (RT-04/05).</div>
          <div id="tt-meta-input" class="flow-tooltip-meta">Follow-up check: Standalone turn</div>
        </div>
      </div>

      <div class="flow-arrow">➔</div>

      <!-- Node 2: Fast-Path Domotica Gate -->
      <div class="flow-node">
        <div id="flow-node-fastpath" class="flow-icon fast-path">⚡</div>
        <div class="flow-label">2. Fast-Path Gate</div>
        <div id="flow-sub-fastpath" class="flow-sub">Zero-LLM (&lt;50ms)</div>
        <div class="flow-tooltip">
          <div class="flow-tooltip-title"><span>Fast-Path Engine</span><span>Step 2</span></div>
          <div class="flow-tooltip-body">Zero-LLM deterministic resolver. Matches smart home imperatives against 3,600+ Home Assistant entities without model latency or tokens.</div>
          <div id="tt-meta-fastpath" class="flow-tooltip-meta">Evaluating smart home intent...</div>
        </div>
      </div>

      <div class="flow-arrow">➔</div>

      <!-- Node 3: Laya System 1 Router (THIS IS LAYA!) -->
      <div class="flow-node">
        <div id="flow-node-laya" class="flow-icon cloud">🧠</div>
        <div class="flow-label">3. Laya System 1</div>
        <div id="flow-sub-laya" class="flow-sub">Intel Arc iGPU</div>
        <div class="flow-tooltip">
          <div class="flow-tooltip-title"><span>Laya Neural Router</span><span>Step 3 (Laya)</span></div>
          <div class="flow-tooltip-body"><strong>This is Laya!</strong> Local neural network running on Intel Arc GPU (XPU). Categorizes prompt into task families (Deep, Code, General, Quick) in ~80ms.</div>
          <div id="tt-meta-laya" class="flow-tooltip-meta">Hardware: Level-Zero XPU</div>
        </div>
      </div>

      <div class="flow-arrow">➔</div>

      <!-- Node 4: Optimization Gate -->
      <div class="flow-node">
        <div id="flow-node-opt" class="flow-icon active">✂️</div>
        <div class="flow-label">4. Optimization</div>
        <div id="flow-sub-opt" class="flow-sub">Tool &amp; Memory</div>
        <div class="flow-tooltip">
          <div class="flow-tooltip-title"><span>Optimization Gate</span><span>Step 4</span></div>
          <div class="flow-tooltip-body">Dynamic tool pruning removes developer tools. Smart memory gating strips personal memory context for impersonal queries to save tokens.</div>
          <div id="tt-meta-opt" class="flow-tooltip-meta">Evaluating gating policies...</div>
        </div>
      </div>

      <div class="flow-arrow">➔</div>

      <!-- Node 5: Action Target -->
      <div class="flow-node">
        <div id="flow-node-output" class="flow-icon active">🎯</div>
        <div class="flow-label">5. Action Target</div>
        <div id="flow-sub-output" class="flow-sub">Dispatched</div>
        <div class="flow-tooltip">
          <div class="flow-tooltip-title"><span>Execution Target</span><span>Step 5</span></div>
          <div class="flow-tooltip-body">Final execution destination: either direct Home Assistant REST service call, or upstream cloud model with calibrated thinking budget.</div>
          <div id="tt-meta-output" class="flow-tooltip-meta">Destination: Ready</div>
        </div>
      </div>
    </div>

    <!-- Tabs -->
    <div class="tab-bar">
      <button id="tab-btn-fastpath" class="tab-btn active" onclick="switchTab('fastpath')">⚡ Domotica Fast-Path</button>
      <button id="tab-btn-model" class="tab-btn" onclick="switchTab('model')">🤖 Model &amp; Effort Routing</button>
      <button id="tab-btn-debug" class="tab-btn" onclick="switchTab('debug')">🔬 Trace &amp; Debug Log</button>
    </div>

    <!-- Tab 1: Fast Path View -->
    <div id="tab-content-fastpath">
      <div class="result-grid">
        <div class="result-box">
          <div class="result-label">Fast-Path Eligible</div>
          <div id="fp-eligible" class="result-val" style="color: #34d399;">YES</div>
        </div>
        <div class="result-box">
          <div class="result-label">Target Domain &amp; Action</div>
          <div id="fp-domain-service" class="result-val">-</div>
        </div>
        <div class="result-box">
          <div class="result-label">Resolved Target Entity/Area</div>
          <div id="fp-target-id" class="result-val" style="font-size: 14px; word-break: break-all; color: #93c5fd;">-</div>
        </div>
        <div class="result-box">
          <div class="result-label">Decision Latency</div>
          <div id="fp-latency" class="result-val" style="color: #34d399;">-</div>
        </div>
      </div>

      <div class="trace-card">
        <div style="font-size: 12px; font-weight: 700; color: #cbd5e1; margin-bottom: 10px; text-transform: uppercase;">Generated Home Assistant Tool Call (OpenAI Wire Protocol):</div>
        <pre id="fp-tool-call" style="margin-top: 0;"></pre>
        <div style="margin-top: 14px; display: flex; justify-content: flex-end;">
          <button id="btn-execute-ha" class="btn execute" onclick="executeAgainstHA()">
            <span>⚡ Execute Live against Home Assistant</span>
          </button>
        </div>
        <div id="execution-feedback" style="margin-top: 10px; font-size: 13px; display: none;"></div>
      </div>
    </div>

    <!-- Tab 2: Model Routing View -->
    <div id="tab-content-model" style="display: none;">
      <div class="result-grid">
        <div class="result-box">
          <div class="result-label">Task Family</div>
          <div id="res-family" class="result-val"><span class="pill general">-</span></div>
        </div>
        <div class="result-box">
          <div class="result-label">Reasoning Effort</div>
          <div id="res-effort" class="result-val">-</div>
        </div>
        <div class="result-box">
          <div class="result-label">Assigned Model</div>
          <div id="res-model" class="result-val" style="font-size: 14px; word-break: break-all;">-</div>
        </div>
        <div class="result-box">
          <div class="result-label">Inference Latency</div>
          <div id="res-latency" class="result-val" style="color: #34d399;">-</div>
        </div>
        <div class="result-box">
          <div class="result-label">Thinking Budget</div>
          <div id="res-thinking" class="result-val" style="font-size: 15px; color: #fcd34d;">Unconstrained</div>
        </div>
        <div class="result-box">
          <div class="result-label">Allowed Tools</div>
          <div id="res-tools" class="result-val" style="font-size: 14px; color: #34d399;">-</div>
        </div>
        <div class="result-box">
          <div class="result-label">Memory Gating</div>
          <div id="res-memory" class="result-val" style="font-size: 14px; color: #34d399;">-</div>
        </div>
        <div class="result-box">
          <div class="result-label">Confidence</div>
          <div id="res-conf" class="result-val" style="font-size: 16px; color: #60a5fa;">-</div>
        </div>
      </div>

      <!-- Interactive Sticky Override Correction Bar (EV-03, OB-03) -->
      <div class="trace-card" style="margin-top: 14px; border-color: rgba(59, 130, 246, 0.4);">
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
          <span style="font-size: 13px; font-weight: 700; color: #93c5fd;">🎯 Correct this Routing (Exact Sticky Override)</span>
          <span id="override-status-badge" style="font-size: 11px; color: var(--text-muted);">Stored in corrections.yaml</span>
        </div>
        <p style="font-size: 12px; color: var(--text-muted); margin-bottom: 12px;">
          Was this prompt misrouted? Select the true family below to activate an exact normalized override (&lt;0.5ms) and queue it for review:
        </p>
        <div style="display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 10px;">
          <button class="chip" onclick="submitCorrection('quick')">⚡ Quick</button>
          <button class="chip" onclick="submitCorrection('smarthome')">🏠 Smarthome</button>
          <button class="chip" onclick="submitCorrection('general')">💬 General</button>
          <button class="chip" onclick="submitCorrection('code')">🐍 Code</button>
          <button class="chip" onclick="submitCorrection('deep')">🧠 Deep</button>
        </div>
        <div id="correction-feedback" style="display: none; font-size: 12px; padding: 8px 12px; border-radius: 6px; background: rgba(16, 185, 129, 0.15); color: #34d399; font-weight: 600;"></div>
      </div>
    </div>

    <!-- Tab 3: Debug Log View -->
    <div id="tab-content-debug" style="display: none;">
      <div class="trace-card">
        <div class="trace-item">
          <span class="trace-k"><span>🧹</span> Input Normalization</span>
          <span id="trace-step-norm" class="trace-v trace-check">✓ Done</span>
        </div>
        <div class="trace-item">
          <span class="trace-k"><span>🏠</span> Fast-Path Domotica Engine</span>
          <span id="trace-step-fp" class="trace-v">-</span>
        </div>
        <div class="trace-item">
          <span class="trace-k"><span>🧠</span> Laya System 1 Neural Router</span>
          <span id="trace-step-laya" class="trace-v">-</span>
        </div>
        <div class="trace-item">
          <span class="trace-k"><span>✂️</span> Dynamic Tool Pruning</span>
          <span id="trace-step-tools" class="trace-v">-</span>
        </div>
        <div class="trace-item">
          <span class="trace-k"><span>🧠</span> Smart Memory Gating</span>
          <span id="trace-step-mem" class="trace-v">-</span>
        </div>
        <div class="trace-item">
          <span class="trace-k"><span>⏱️</span> Hardware Execution Time</span>
          <span id="trace-step-time" class="trace-v">-</span>
        </div>
      </div>

      <details open>
        <summary>Raw JSON Diagnostics</summary>
        <pre id="json-dump"></pre>
      </details>
    </div>

  </div>
</div>

<script>
  let lastDomoticaAction = null;
  let lastModelDecision = null;

  async function loadStatus() {
    try {
      const res = await fetch('./health');
      if (res.ok) {
        const data = await res.json();
        document.getElementById('badge-device').textContent = 'Device: ' + data.device.toUpperCase() + (data.device === 'xpu' ? ' (Intel Arc iGPU)' : ' (CPU)');
        document.getElementById('badge-status').textContent = data.ready ? 'Status: Ready' : 'Status: Preloading...';
        document.getElementById('badge-status').className = 'badge ' + (data.ready ? 'ready' : 'xpu');
      }
      const modelRes = await fetch('./v1/models');
      if (modelRes.ok) {
        const modelData = await modelRes.json();
        document.getElementById('badge-registry').textContent = 'Provider: ' + modelData.provider.toUpperCase();
      }
    } catch (e) {
      console.error('Failed to load status:', e);
    }
  }

  function toggleContext() {
    const p = document.getElementById('context-panel');
    p.classList.toggle('open');
  }

  const presets = {
    lights_off: "doe de lampen in de serre uit",
    lights_on: "zet de spots in de woonkamer aan",
    climate: "zet de thermostaat op 20.5 graden",
    quick: "Wat is de hoofdstad van Australië?",
    code: "Schrijf een python script om een CSV bestand in te lezen en te plotten met matplotlib",
    deep: "Bereken de annuïtaire hypotheeklasten voor 450k tegen 4% rente over 30 jaar"
  };

  function loadPreset(key) {
    if (presets[key]) {
      document.getElementById('prompt-input').value = presets[key];
    }
  }

  function switchTab(name) {
    ['fastpath', 'model', 'debug'].forEach(t => {
      document.getElementById('tab-btn-' + t).classList.toggle('active', t === name);
      document.getElementById('tab-content-' + t).style.display = (t === name) ? 'block' : 'none';
    });
  }

  async function evaluateTurn() {
    const prompt = document.getElementById('prompt-input').value.trim();
    if (!prompt) return;

    const btn = document.getElementById('route-btn');
    const timing = document.getElementById('timing-hint');
    btn.disabled = true;
    timing.textContent = 'Tracing decision and evaluating on Intel Arc iGPU...';

    const t0 = performance.now();
    try {
      // 1. Evaluate Fast-Path Domotica Engine
      const domoticaPromise = fetch('./v1/domotica/route', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ prompt, execute: false })
      }).then(r => r.ok ? r.json() : null);

      // 2. Evaluate Model Router Engine
      const routePromise = fetch('./v1/route', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ prompt, recent_turns: [], question_set: 'hermes-v1' })
      }).then(r => r.ok ? r.json() : null);

      const [domoticaData, routeData] = await Promise.all([domoticaPromise, routePromise]);
      const wallMs = (performance.now() - t0).toFixed(1);

      lastDomoticaAction = domoticaData;
      lastModelDecision = routeData;

      renderPipelineResults(domoticaData, routeData, wallMs);
      timing.textContent = `Evaluation complete in ${wallMs} ms`;
    } catch (e) {
      alert('Error evaluating turn: ' + e);
      timing.textContent = 'Error occurred';
    } finally {
      btn.disabled = false;
    }
  }

  function renderPipelineResults(domotica, routing, wallMs) {
    document.getElementById('results-area').style.display = 'block';

    const isDomotica = domotica && domotica.is_domotica;
    const isFastPath = domotica && domotica.fast_path;

    // Visual Flow Pipeline updates
    const badge = document.getElementById('flow-verdict-badge');
    const fpNode = document.getElementById('flow-node-fastpath');
    const layaNode = document.getElementById('flow-node-laya');
    const optNode = document.getElementById('flow-node-opt');
    const outNode = document.getElementById('flow-node-output');

    const subFp = document.getElementById('flow-sub-fastpath');
    const subLaya = document.getElementById('flow-sub-laya');
    const subOpt = document.getElementById('flow-sub-opt');
    const subOut = document.getElementById('flow-sub-output');

    // Tooltip dynamic metadata
    const ttFp = document.getElementById('tt-meta-fastpath');
    const ttLaya = document.getElementById('tt-meta-laya');
    const ttOpt = document.getElementById('tt-meta-opt');
    const ttOut = document.getElementById('tt-meta-output');

    if (isFastPath) {
      badge.textContent = 'Fast-Path Active (<50ms)';
      badge.className = 'pill smarthome';
      fpNode.className = 'flow-icon fast-path';
      layaNode.className = 'flow-icon bypassed';
      optNode.className = 'flow-icon active';
      outNode.className = 'flow-icon fast-path';

      subFp.textContent = (domotica.domain || 'HA') + '.' + (domotica.service || 'call');
      subLaya.textContent = 'Bypassed (Zero-LLM)';
      subOpt.textContent = 'Zero-Token Fastpath';
      subOut.textContent = 'Direct HA Call';

      ttFp.innerHTML = `<span style="color:#34d399;">✓ Qualified! Action: ${domotica.domain}.${domotica.service} on ${domotica.target_id}</span>`;
      ttLaya.innerHTML = `<span style="color:#94a3b8;">Bypassed: Fast-path resolved deterministically in &lt;1ms.</span>`;
      ttOpt.innerHTML = `<span style="color:#34d399;">Zero cloud tokens consumed.</span>`;
      ttOut.innerHTML = `<span style="color:#34d399;">Target: Home Assistant Core API</span>`;

      switchTab('fastpath');
    } else {
      badge.textContent = 'Cloud LLM Route';
      badge.className = 'pill ' + (routing ? routing.family : 'general');
      fpNode.className = 'flow-icon bypassed';
      layaNode.className = 'flow-icon cloud';
      optNode.className = 'flow-icon active';
      outNode.className = 'flow-icon cloud';

      const rejectReason = domotica ? (domotica.rejected_reason || 'not_domotica') : 'none';
      subFp.textContent = `Bypassed (${rejectReason})`;
      subLaya.textContent = (routing ? routing.family.toUpperCase() : 'Cloud Route');
      subOpt.textContent = routing && routing.needs_memory === false ? 'Memory Stripped' : 'Full Context';
      subOut.textContent = (routing ? routing.model : 'LLM Call');

      ttFp.innerHTML = `<span style="color:#f59e0b;">Bypassed: ${rejectReason} (routed to Laya System 1).</span>`;
      ttLaya.innerHTML = `<span style="color:#c084fc;">Intel Arc XPU: classified as ${routing ? routing.family.toUpperCase() : 'general'} (${Math.round(((routing ? routing.confidence.family : 0) * 100))}% conf).</span>`;
      ttOpt.innerHTML = routing && routing.needs_memory === false ? `<span style="color:#34d399;">Tools pruned. Memory stripped.</span>` : `<span style="color:#60a5fa;">Full tools and memory retained.</span>`;
      ttOut.innerHTML = `<span style="color:#c084fc;">Model: ${routing ? routing.model : '-'} (${routing ? routing.provider : '-'})</span>`;

      switchTab('model');
    }

    // Populate Fast-Path Tab
    if (domotica) {
      document.getElementById('fp-eligible').textContent = isFastPath ? 'YES (<50ms)' : (isDomotica ? 'Ambiguous Target' : 'NO (Not Domotica)');
      document.getElementById('fp-eligible').style.color = isFastPath ? '#34d399' : '#f59e0b';
      document.getElementById('fp-domain-service').textContent = (domotica.domain || '-') + ' ➔ ' + (domotica.service || '-');
      document.getElementById('fp-target-id').textContent = domotica.target_id ? `${domotica.target_id} (${domotica.target_type})` : (domotica.target_name || 'No target');
      document.getElementById('fp-latency').textContent = '< 1.0 ms';
      document.getElementById('fp-tool-call').textContent = domotica.openai_tool_call ? JSON.stringify(domotica.openai_tool_call, null, 2) : 'No tool call generated';
      document.getElementById('btn-execute-ha').style.display = isFastPath ? 'inline-flex' : 'none';
      document.getElementById('execution-feedback').style.display = 'none';
    }

    // Populate Model Routing Tab
    if (routing) {
      const famEl = document.getElementById('res-family');
      famEl.innerHTML = '<span class="pill ' + routing.family + '">' + routing.family + '</span>';
      document.getElementById('res-effort').textContent = (routing.effort || '-').toUpperCase();
      document.getElementById('res-model').textContent = routing.model + ' (' + routing.provider + ')';
      document.getElementById('res-latency').textContent = (routing.latency_ms || 0).toFixed(1) + ' ms';
      document.getElementById('res-thinking').textContent = routing.thinking_budget !== undefined && routing.thinking_budget !== null ? routing.thinking_budget + ' tokens' : 'Unconstrained';

      const allowedTools = routing.allowed_tools;
      if (Array.isArray(allowedTools)) {
        document.getElementById('res-tools').textContent = allowedTools.length === 0 ? 'Pruned (0 tools)' : allowedTools.join(', ');
      } else {
        document.getElementById('res-tools').textContent = 'All tools allowed (*)';
      }

      document.getElementById('res-memory').textContent = routing.needs_memory === false ? 'Bypassed (Stripped)' : 'Enabled';
      const famConf = Math.round(((routing.confidence || {}).family || 0) * 100);
      document.getElementById('res-conf').textContent = famConf + '%';
    }

    // Populate Debug Tab
    document.getElementById('trace-step-fp').textContent = isFastPath ? `Resolved to ${domotica.target_id}` : `Bypassed (${domotica.rejected_reason || 'not domotica'})`;
    document.getElementById('trace-step-laya').textContent = routing ? `${routing.family.toUpperCase()} (${Math.round((routing.confidence.family || 0)*100)}% on Arc iGPU)` : '-';
    document.getElementById('trace-step-tools').textContent = (routing && routing.needs_memory === false) ? 'Pruned for token efficiency' : 'Preserved full toolset';
    document.getElementById('trace-step-mem').textContent = (routing && routing.needs_memory === false) ? 'Stripped <memory-context>' : 'Memory context preserved';
    document.getElementById('trace-step-time').textContent = `${wallMs} ms (Hardware XPU)`;

    document.getElementById('json-dump').textContent = JSON.stringify({
      step_1_input: { prompt, normalized: true },
      step_2_fast_path_domotica: domotica,
      step_3_laya_system_one_router: routing,
      step_4_optimization: {
        tools_allowed: routing ? routing.allowed_tools : null,
        memory_gated: routing ? routing.needs_memory === false : false
      },
      step_5_action_target: {
        destination: isFastPath ? "Home Assistant Core API" : (routing ? routing.model : "LLM"),
        wall_clock_ms: parseFloat(wallMs)
      }
    }, null, 2);
  }

  async function executeAgainstHA() {
    if (!lastDomoticaAction) return;
    const btn = document.getElementById('btn-execute-ha');
    const fb = document.getElementById('execution-feedback');
    btn.disabled = true;
    fb.style.display = 'block';
    fb.innerHTML = '<span style="color: #60a5fa;">Dispatching direct REST call to Home Assistant Core...</span>';

    try {
      const res = await fetch('./v1/domotica/route', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          prompt: document.getElementById('prompt-input').value.trim(),
          execute: true
        })
      });
      const data = await res.json();
      if (data.executed) {
        fb.innerHTML = '<span style="color: #34d399; font-weight: 700;">✓ Executed successfully in Home Assistant! (' + data.domain + '.' + data.service + ')</span>';
      } else {
        fb.innerHTML = '<span style="color: #ef4444; font-weight: 700;">✗ Execution failed or dry-run rejected.</span>';
      }
    } catch (e) {
      fb.innerHTML = '<span style="color: #ef4444;">Error calling HA: ' + e + '</span>';
    } finally {
      btn.disabled = false;
    }
  }

  async function submitCorrection(family) {
    const prompt = document.getElementById('prompt-input').value.trim();
    if (!prompt) return;
    const fb = document.getElementById('correction-feedback');
    fb.style.display = 'block';
    fb.style.color = '#60a5fa';
    fb.style.background = 'rgba(59, 130, 246, 0.15)';
    fb.textContent = 'Saving override for "' + prompt + '" to ' + family.toUpperCase() + '...';

    try {
      const res = await fetch('./v1/corrections', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          prompt: prompt,
          family: family,
          effort: (family === 'code' || family === 'deep') ? 'high' : 'normal',
          author: 'ingress_ui'
        })
      });
      const data = await res.json();
      if (data.success) {
        fb.style.color = '#34d399';
        fb.style.background = 'rgba(16, 185, 129, 0.15)';
        fb.textContent = '✓ Override active! Saved to corrections.yaml and queued in candidates.jsonl.';
        setTimeout(() => evaluateTurn(), 400);
      } else {
        fb.style.color = '#ef4444';
        fb.style.background = 'rgba(239, 68, 68, 0.15)';
        fb.textContent = 'Failed saving correction: ' + (data.detail || 'unknown error');
      }
    } catch (e) {
      fb.style.color = '#ef4444';
      fb.style.background = 'rgba(239, 68, 68, 0.15)';
      fb.textContent = 'Error: ' + e;
    }
  }

  loadStatus();
</script>
</body>
</html>"""
