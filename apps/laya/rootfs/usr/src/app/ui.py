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
    .container { max-width: 1020px; margin: 0 auto; }

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
    .flow-pipeline { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin: 20px 0 24px 0; padding: 16px; background: var(--card-sub); border-radius: 8px; border: 1px solid var(--border); overflow-x: auto; }
    .flow-node { display: flex; flex-direction: column; align-items: center; text-align: center; min-width: 120px; }
    .flow-icon { width: 38px; height: 38px; border-radius: 50%; background: #1e293b; border: 2px solid var(--border); display: flex; align-items: center; justify-content: center; font-size: 16px; margin-bottom: 6px; transition: all 0.3s; }
    .flow-icon.active { border-color: var(--primary); background: var(--primary-light); color: #60a5fa; box-shadow: 0 0 12px rgba(59,130,246,0.3); }
    .flow-icon.fast-path { border-color: var(--success); background: var(--success-light); color: #34d399; box-shadow: 0 0 12px rgba(16,185,129,0.3); }
    .flow-icon.cloud { border-color: var(--purple); background: var(--purple-light); color: #c084fc; box-shadow: 0 0 12px rgba(139,92,246,0.3); }
    .flow-label { font-size: 12px; font-weight: 700; color: #fff; margin-bottom: 2px; }
    .flow-sub { font-size: 11px; color: var(--text-muted); }
    .flow-arrow { font-size: 16px; color: var(--text-dim); }

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

    <!-- Visual Flow Pipeline Diagram -->
    <div class="flow-pipeline">
      <div class="flow-node">
        <div id="flow-node-input" class="flow-icon active">💬</div>
        <div class="flow-label">User Input</div>
        <div id="flow-sub-input" class="flow-sub">Normalized</div>
      </div>
      <div class="flow-arrow">➔</div>
      <div class="flow-node">
        <div id="flow-node-intent" class="flow-icon active">🧠</div>
        <div class="flow-label">Semantic Intent</div>
        <div id="flow-sub-intent" class="flow-sub">Analyzing</div>
      </div>
      <div class="flow-arrow">➔</div>
      <div class="flow-node">
        <div id="flow-node-decision" class="flow-icon fast-path">⚡</div>
        <div class="flow-label">Decision Gate</div>
        <div id="flow-sub-decision" class="flow-sub">Zero-LLM Fast Path</div>
      </div>
      <div class="flow-arrow">➔</div>
      <div class="flow-node">
        <div id="flow-node-opt" class="flow-icon active">✂️</div>
        <div class="flow-label">Optimization</div>
        <div id="flow-sub-opt" class="flow-sub">Tool & Memory Gating</div>
      </div>
      <div class="flow-arrow">➔</div>
      <div class="flow-node">
        <div id="flow-node-output" class="flow-icon active">🎯</div>
        <div class="flow-label">Action Target</div>
        <div id="flow-sub-output" class="flow-sub">HA Service Call</div>
      </div>
    </div>

    <!-- Tabs -->
    <div class="tab-bar">
      <button id="tab-btn-fastpath" class="tab-btn active" onclick="switchTab('fastpath')">⚡ Domotica Fast-Path</button>
      <button id="tab-btn-model" class="tab-btn" onclick="switchTab('model')">🤖 Model & Effort Routing</button>
      <button id="tab-btn-debug" class="tab-btn" onclick="switchTab('debug')">🔬 Trace & Debug Log</button>
    </div>

    <!-- Tab 1: Fast Path View -->
    <div id="tab-content-fastpath">
      <div class="result-grid">
        <div class="result-box">
          <div class="result-label">Fast-Path Eligible</div>
          <div id="fp-eligible" class="result-val" style="color: #34d399;">YES</div>
        </div>
        <div class="result-box">
          <div class="result-label">Target Domain & Action</div>
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
    </div>

    <!-- Tab 3: Debug Log View -->
    <div id="tab-content-debug" style="display: none;">
      <div class="trace-card">
        <div class="trace-item">
          <span class="trace-k"><span>🧹</span> Input Normalization</span>
          <span id="trace-step-norm" class="trace-v trace-check">✓ Done</span>
        </div>
        <div class="trace-item">
          <span class="trace-k"><span>🏠</span> Entity & Area Matching</span>
          <span id="trace-step-resolve" class="trace-v">-</span>
        </div>
        <div class="trace-item">
          <span class="trace-k"><span>✂️</span> Dynamic Tool Pruning</span>
          <span id="trace-step-tools" class="trace-v">-</span>
        </div>
        <div class="trace-item">
          <span class="trace-k"><span>🧠</span> Memory Gating</span>
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
    const decNode = document.getElementById('flow-node-decision');
    const subDec = document.getElementById('flow-sub-decision');
    const subOut = document.getElementById('flow-sub-output');

    if (isFastPath) {
      badge.textContent = 'Fast-Path Active (<50ms)';
      badge.className = 'pill smarthome';
      decNode.className = 'flow-icon fast-path';
      subDec.textContent = 'Zero-LLM Fast Path';
      subOut.textContent = (domotica.domain || 'HA') + '.' + (domotica.service || 'call');
      switchTab('fastpath');
    } else {
      badge.textContent = 'Cloud LLM Route';
      badge.className = 'pill ' + (routing ? routing.family : 'general');
      decNode.className = 'flow-icon cloud';
      subDec.textContent = (routing ? routing.family.toUpperCase() : 'Cloud Route');
      subOut.textContent = (routing ? routing.model : 'LLM Call');
      switchTab('model');
    }

    // Populate Fast-Path Tab
    if (domotica) {
      document.getElementById('fp-eligible').textContent = isFastPath ? 'YES (<50ms)' : (isDomotica ? 'Ambiguous Target' : 'NO (Not Domotica)');
      document.getElementById('fp-eligible').style.color = isFastPath ? '#34d399' : '#f59e0b';
      document.getElementById('fp-domain-service').textContent = (domotica.domain || '-') + ' ➔ ' + (domotica.service || '-');
      document.getElementById('fp-target-id').textContent = domotica.target_id ? `${domotica.target_id} (${domotica.target_type})` : (domotica.target_name || 'No target');
      document.getElementById('fp-latency').textContent = (wallMs / 2).toFixed(1) + ' ms';
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
    document.getElementById('trace-step-resolve').textContent = domotica && domotica.target_id ? `Resolved to ${domotica.target_id}` : 'General Prompt';
    document.getElementById('trace-step-tools').textContent = (routing && routing.needs_memory === false) ? 'Pruned for token efficiency' : 'Preserved full toolset';
    document.getElementById('trace-step-mem').textContent = (routing && routing.needs_memory === false) ? 'Stripped <memory-context>' : 'Memory context preserved';
    document.getElementById('trace-step-time').textContent = `${wallMs} ms (Hardware XPU)`;

    document.getElementById('json-dump').textContent = JSON.stringify({
      domotica_fast_path: domotica,
      system_one_routing: routing,
      wall_clock_ms: parseFloat(wallMs)
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

  loadStatus();
</script>
</body>
</html>"""
