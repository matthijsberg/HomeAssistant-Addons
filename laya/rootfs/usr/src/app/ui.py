"""WebUI for Laya Decision Engine Home Assistant Ingress."""

def render_gui_html() -> str:
    """Generate self-contained, offline-first HTML/CSS/JS interface."""
    return """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Laya Router — HA Decision Engine</title>
  <style>
    :root {
      --bg: #0f172a;
      --card-bg: #1e293b;
      --border: #334155;
      --text: #f8fafc;
      --text-muted: #94a3b8;
      --primary: #3b82f6;
      --primary-hover: #2563eb;
      --success: #10b981;
      --warning: #f59e0b;
      --purple: #8b5cf6;
      --danger: #ef4444;
      --radius: 8px;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
    body { background-color: var(--bg); color: var(--text); padding: 24px; min-height: 100vh; }
    .container { max-width: 960px; margin: 0 auto; }
    header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 24px; padding-bottom: 16px; border-bottom: 1px solid var(--border); }
    .logo-area h1 { font-size: 22px; font-weight: 700; color: #fff; display: flex; align-items: center; gap: 10px; }
    .logo-area p { font-size: 13px; color: var(--text-muted); margin-top: 4px; }
    .badge-bar { display: flex; gap: 8px; flex-wrap: wrap; }
    .badge { font-size: 12px; padding: 4px 10px; border-radius: 9999px; background: #334155; color: #e2e8f0; font-weight: 500; display: inline-flex; align-items: center; gap: 6px; }
    .badge.xpu { background: rgba(59, 130, 246, 0.2); color: #60a5fa; border: 1px solid rgba(59, 130, 246, 0.4); }
    .badge.ready { background: rgba(16, 185, 129, 0.2); color: #34d399; border: 1px solid rgba(16, 185, 129, 0.4); }
    .badge.provider { background: rgba(139, 92, 246, 0.2); color: #c084fc; border: 1px solid rgba(139, 92, 246, 0.4); }

    .card { background: var(--card-bg); border: 1px solid var(--border); border-radius: var(--radius); padding: 20px; margin-bottom: 20px; }
    .card-title { font-size: 15px; font-weight: 600; margin-bottom: 12px; color: #cbd5e1; text-transform: uppercase; letter-spacing: 0.5px; }

    .chips-title { font-size: 12px; color: var(--text-muted); margin-bottom: 8px; font-weight: 500; }
    .chips { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 16px; }
    .chip { font-size: 12px; background: #0f172a; border: 1px solid var(--border); color: #94a3b8; padding: 6px 12px; border-radius: 6px; cursor: pointer; transition: all 0.15s ease; }
    .chip:hover { border-color: var(--primary); color: #fff; background: rgba(59, 130, 246, 0.1); }

    textarea, input[type="text"] { width: 100%; background: #0f172a; border: 1px solid var(--border); border-radius: 6px; padding: 12px; color: #fff; font-size: 14px; outline: none; transition: border 0.15s; }
    textarea:focus, input[type="text"]:focus { border-color: var(--primary); }
    textarea { min-height: 85px; resize: vertical; margin-bottom: 14px; }

    .context-toggle { font-size: 13px; color: var(--primary); cursor: pointer; margin-bottom: 10px; display: inline-block; user-select: none; }
    .context-panel { display: none; margin-bottom: 14px; }
    .context-panel.open { display: block; }

    .action-row { display: flex; justify-content: space-between; align-items: center; }
    .btn { background: var(--primary); color: #fff; border: none; padding: 10px 20px; border-radius: 6px; font-weight: 600; cursor: pointer; transition: background 0.15s; font-size: 14px; display: inline-flex; align-items: center; gap: 8px; }
    .btn:hover { background: var(--primary-hover); }
    .btn:disabled { opacity: 0.5; cursor: not-allowed; }

    /* Results */
    #results-area { display: none; }
    .result-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 14px; margin-bottom: 18px; }
    .result-box { background: #0f172a; border: 1px solid var(--border); border-radius: 6px; padding: 14px; }
    .result-label { font-size: 11px; text-transform: uppercase; color: var(--text-muted); font-weight: 600; margin-bottom: 6px; }
    .result-val { font-size: 18px; font-weight: 700; color: #fff; }
    .pill { display: inline-block; padding: 3px 10px; border-radius: 4px; font-size: 13px; font-weight: 600; text-transform: uppercase; }
    .pill.quick { background: rgba(16, 185, 129, 0.2); color: #34d399; }
    .pill.general { background: rgba(59, 130, 246, 0.2); color: #60a5fa; }
    .pill.code { background: rgba(139, 92, 246, 0.2); color: #c084fc; }
    .pill.deep { background: rgba(245, 158, 11, 0.2); color: #fbbf24; }

    .meter-row { margin-bottom: 12px; }
    .meter-label { display: flex; justify-content: space-between; font-size: 12px; margin-bottom: 4px; color: var(--text-muted); }
    .meter-bar { height: 6px; background: #0f172a; border-radius: 9999px; overflow: hidden; border: 1px solid var(--border); }
    .meter-fill { height: 100%; border-radius: 9999px; transition: width 0.4s ease; }

    pre { background: #0f172a; padding: 12px; border-radius: 6px; font-size: 12px; overflow-x: auto; color: #cbd5e1; border: 1px solid var(--border); margin-top: 10px; }
    details { margin-top: 12px; cursor: pointer; font-size: 13px; color: var(--text-muted); }
  </style>
</head>
<body>
<div class="container">
  <header>
    <div class="logo-area">
      <h1>⚡ Laya Router</h1>
      <p>Local System 1 Sub-100ms LLM Decision & Routing Engine</p>
    </div>
    <div class="badge-bar">
      <div id="badge-device" class="badge xpu">Device: Loading...</div>
      <div id="badge-status" class="badge ready">Status: Loading...</div>
      <div id="badge-provider" class="badge provider">Provider: Loading...</div>
    </div>
  </header>

  <div class="card">
    <div class="card-title">Routing Playground</div>

    <div class="chips-title">Quick Presets:</div>
    <div class="chips">
      <div class="chip" onclick="loadPreset('code')">🐍 Python Script (Code)</div>
      <div class="chip" onclick="loadPreset('deep')">📐 Annuïtaire Hypotheek (Deep)</div>
      <div class="chip" onclick="loadPreset('quick')">🏠 Lampen Woonkamer (Quick)</div>
      <div class="chip" onclick="loadPreset('finance')">📊 DCF vs NPV Models (Deep)</div>
      <div class="chip" onclick="loadPreset('context')">💬 Context Follow-up ("ja, doe maar")</div>
    </div>

    <label style="font-size: 12px; font-weight: 600; color: #94a3b8; display: block; margin-bottom: 6px;">Prompt:</label>
    <textarea id="prompt-input" placeholder="Type a user message to evaluate routing and reasoning effort..."></textarea>

    <div class="context-toggle" onclick="toggleContext()">+ Toggle Previous Conversation Context (Optional)</div>
    <div id="context-panel" class="context-panel">
      <label style="font-size: 12px; color: #94a3b8; display: block; margin-bottom: 4px;">Context (Preceding turns):</label>
      <textarea id="context-input" style="min-height: 60px;" placeholder="user: Kan je een Python script maken om zonne-energie te berekenen?\nassistant: Zeker, zal ik dat script nu schrijven?"></textarea>
    </div>

    <div class="action-row">
      <span id="timing-hint" style="font-size: 12px; color: var(--text-muted);">Ready for live inference</span>
      <button id="route-btn" class="btn" onclick="executeRouting()">
        <span>🚀 Route Turn</span>
      </button>
    </div>
  </div>

  <div id="results-area" class="card">
    <div class="card-title">System 1 Evaluation Outcome</div>

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
        <div class="result-label">Hardware Latency</div>
        <div id="res-latency" class="result-val" style="color: #34d399;">-</div>
      </div>
    </div>

    <div class="meter-row">
      <div class="meter-label">
        <span>Family Confidence (<span id="conf-family-val">0%</span>)</span>
        <span id="checkpoint-tag" style="color: var(--primary);">Checkpoint: -</span>
      </div>
      <div class="meter-bar">
        <div id="meter-family" class="meter-fill" style="width: 0%; background: var(--primary);"></div>
      </div>
    </div>

    <div class="meter-row">
      <div class="meter-label">
        <span>Effort Confidence (<span id="conf-effort-val">0%</span>)</span>
        <span>Question Set: hermes-v1</span>
      </div>
      <div class="meter-bar">
        <div id="meter-effort" class="meter-fill" style="width: 0%; background: var(--warning);"></div>
      </div>
    </div>

    <details>
      <summary>View Raw JSON Payload</summary>
      <pre id="json-dump"></pre>
    </details>
  </div>
</div>

<script>
  // Initialize and load status
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
        document.getElementById('badge-provider').textContent = 'Provider: ' + modelData.provider.toUpperCase();
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
    code: { prompt: "Schrijf een python script om een CSV bestand in te lezen en te plotten met matplotlib", context: "" },
    deep: { prompt: "Bereken de annuïtaire hypotheeklasten voor 450k tegen 4% rente over 30 jaar", context: "" },
    quick: { prompt: "Zet alle lampen in de woonkamer uit", context: "" },
    finance: { prompt: "Compare discounted cash flow vs net present value models for capital allocation", context: "" },
    context: {
      prompt: "ja, doe maar",
      context: "user: Kan je een Python script maken om de zonne-energie te berekenen?\\nassistant: Zeker, zal ik dat script nu schrijven?"
    }
  };

  function loadPreset(key) {
    const item = presets[key];
    if (!item) return;
    document.getElementById('prompt-input').value = item.prompt;
    if (item.context) {
      document.getElementById('context-input').value = item.context;
      document.getElementById('context-panel').classList.add('open');
    }
  }

  async function executeRouting() {
    const prompt = document.getElementById('prompt-input').value.trim();
    if (!prompt) return;

    const btn = document.getElementById('route-btn');
    const timing = document.getElementById('timing-hint');
    btn.disabled = true;
    timing.textContent = 'Evaluating on System 1 neural weights...';

    const contextRaw = document.getElementById('context-input').value.trim();
    const recent_turns = [];
    if (contextRaw) {
      recent_turns.push(contextRaw);
    }

    const t0 = performance.now();
    try {
      const res = await fetch('./v1/route', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ prompt, recent_turns, question_set: 'hermes-v1' })
      });

      const wallMs = (performance.now() - t0).toFixed(1);

      if (!res.ok) {
        const err = await res.json();
        alert('Error: ' + (err.detail || 'Inference failed'));
        timing.textContent = 'Error occurred';
        btn.disabled = false;
        return;
      }

      const data = await res.json();
      renderResults(data, wallMs);
      timing.textContent = 'Inference completed in ' + data.latency_ms + ' ms (Wall clock: ' + wallMs + ' ms)';
    } catch (e) {
      alert('Network or API error: ' + e);
      timing.textContent = 'Failed to reach API';
    } finally {
      btn.disabled = false;
    }
  }

  function renderResults(data, wallMs) {
    document.getElementById('results-area').style.display = 'block';

    // Family badge
    const famEl = document.getElementById('res-family');
    famEl.innerHTML = '<span class="pill ' + data.family + '">' + data.family + '</span>';

    // Effort
    document.getElementById('res-effort').textContent = data.effort.toUpperCase();

    // Model & Provider
    document.getElementById('res-model').textContent = data.model + ' (' + data.provider + ')';

    // Latency
    document.getElementById('res-latency').textContent = data.latency_ms + ' ms';

    // Checkpoint
    document.getElementById('checkpoint-tag').textContent = 'Checkpoint: ' + data.checkpoint;

    // Confidences
    const famConf = Math.round((data.confidence.family || 0) * 100);
    const effConf = Math.round((data.confidence.effort || 0) * 100);

    document.getElementById('conf-family-val').textContent = famConf + '%';
    document.getElementById('meter-family').style.width = famConf + '%';

    document.getElementById('conf-effort-val').textContent = effConf + '%';
    document.getElementById('meter-effort').style.width = effConf + '%';

    // Dump
    document.getElementById('json-dump').textContent = JSON.stringify(data, null, 2);
  }

  loadStatus();
</script>
</body>
</html>"""
