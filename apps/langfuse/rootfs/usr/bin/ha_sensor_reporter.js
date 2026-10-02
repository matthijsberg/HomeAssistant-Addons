#!/usr/bin/env node
/**
 * Home Assistant KPI Sensor Reporter for Langfuse v4 Add-on.
 * Runs in Node.js (native to container), queries local ClickHouse, and publishes metrics to HA Core API.
 */

const http = require('http');

const SUPERVISOR_TOKEN = process.env.SUPERVISOR_TOKEN;
const HA_URL = process.env.HA_URL || 'http://supervisor/core/api';
const CLICKHOUSE_HOST = '127.0.0.1';
const CLICKHOUSE_PORT = 8123;
const INTERVAL_MS = 30000;

function log(msg) {
  const ts = new Date().toISOString();
  console.log(`[${ts}] [ha-sensors] ${msg}`);
}

function queryClickHouse(sql) {
  return new Promise((resolve) => {
    const query = encodeURIComponent(sql) + '%20FORMAT%20JSON';
    const req = http.get(
      `http://${CLICKHOUSE_HOST}:${CLICKHOUSE_PORT}/?query=${query}`,
      { timeout: 5000 },
      (res) => {
        let data = '';
        res.on('data', (chunk) => (data += chunk));
        res.on('end', () => {
          try {
            const parsed = JSON.parse(data);
            resolve(parsed.data || []);
          } catch (e) {
            resolve([]);
          }
        });
      }
    );
    req.on('error', () => resolve([]));
    req.on('timeout', () => {
      req.destroy();
      resolve([]);
    });
  });
}

function postSensor(entityId, state, attributes) {
  return new Promise((resolve) => {
    if (!SUPERVISOR_TOKEN) {
      resolve(false);
      return;
    }

    const payload = JSON.stringify({ state, attributes });
    const options = {
      hostname: 'supervisor',
      port: 80,
      path: `/core/api/states/${entityId}`,
      method: 'POST',
      headers: {
        Authorization: `Bearer ${SUPERVISOR_TOKEN}`,
        'Content-Type': 'application/json',
        'Content-Length': Buffer.byteLength(payload)
      },
      timeout: 5000
    };

    const req = http.request(options, (res) => {
      resolve(res.statusCode === 200 || res.statusCode === 201);
    });

    req.on('error', () => resolve(false));
    req.on('timeout', () => {
      req.destroy();
      resolve(false);
    });

    req.write(payload);
    req.end();
  });
}

async function collectAndPublish() {
  const now = new Date();
  const year = now.getFullYear();
  const month = String(now.getMonth() + 1).padStart(2, '0');
  const day = String(now.getDate()).padStart(2, '0');
  const todayIso = `${year}-${month}-${day} 00:00:00`;

  // 1. Aggregated metrics today
  const sqlToday = `
    SELECT
      count() AS total_generations,
      sum(ifNotFinite(usage_details['total'], 0)) AS total_tokens,
      sum(ifNotFinite(usage_details['input'], 0)) AS input_tokens,
      sum(ifNotFinite(usage_details['output'], 0)) AS output_tokens,
      sum(ifNotFinite(usage_details['reasoning'], 0)) AS reasoning_tokens,
      sum(ifNotFinite(total_cost, 0)) AS total_cost_usd,
      round(avg(if(end_time IS NOT NULL AND start_time IS NOT NULL, dateDiff('millisecond', start_time, end_time) / 1000.0, NULL)), 2) AS avg_latency_s
    FROM default.observations
    WHERE start_time >= '${todayIso}'
      AND type = 'GENERATION'
      AND is_deleted = 0
  `;

  const rows = await queryClickHouse(sqlToday);
  const metrics = rows[0] || {};

  const totalTokens = parseInt(metrics.total_tokens || 0, 10);
  const inputTokens = parseInt(metrics.input_tokens || 0, 10);
  const outputTokens = parseInt(metrics.output_tokens || 0, 10);
  const reasoningTokens = parseInt(metrics.reasoning_tokens || 0, 10);
  const totalCostUsd = parseFloat(Number(metrics.total_cost_usd || 0).toFixed(4));
  const totalGenerations = parseInt(metrics.total_generations || 0, 10);
  const avgLatency = metrics.avg_latency_s !== null && metrics.avg_latency_s !== undefined
    ? parseFloat(metrics.avg_latency_s)
    : 0.0;

  // 2. Latest observation
  const sqlLatest = `
    SELECT
      provided_model_name,
      ifNotFinite(usage_details['total'], 0) AS last_tokens,
      ifNotFinite(total_cost, 0) AS last_cost,
      start_time
    FROM default.observations
    WHERE type = 'GENERATION' AND is_deleted = 0
    ORDER BY start_time DESC
    LIMIT 1
  `;
  const latestRows = await queryClickHouse(sqlLatest);
  const latest = latestRows[0] || {};
  const lastModel = latest.provided_model_name || 'none';
  const lastTokens = parseInt(latest.last_tokens || 0, 10);
  const lastCost = parseFloat(Number(latest.last_cost || 0).toFixed(4));
  const lastTime = latest.start_time || '';

  const nowIso = now.toISOString();

  // Publish Sensor 1: Tokens Today
  await postSensor('sensor.langfuse_tokens_today', totalTokens, {
    friendly_name: 'Langfuse Tokens Today',
    icon: 'mdi:counter',
    unit_of_measurement: 'tokens',
    state_class: 'total',
    input_tokens: inputTokens,
    output_tokens: outputTokens,
    reasoning_tokens: reasoningTokens,
    last_updated: nowIso
  });

  // Publish Sensor 2: Cost Today (USD)
  await postSensor('sensor.langfuse_cost_today', totalCostUsd, {
    friendly_name: 'Langfuse Cost Today',
    icon: 'mdi:currency-usd',
    unit_of_measurement: '$',
    state_class: 'total',
    last_updated: nowIso
  });

  // Publish Sensor 3: Generations / Prompts Today
  await postSensor('sensor.langfuse_generations_today', totalGenerations, {
    friendly_name: 'Langfuse LLM Calls Today',
    icon: 'mdi:brain',
    unit_of_measurement: 'calls',
    state_class: 'total',
    last_updated: nowIso
  });

  // Publish Sensor 4: Average Latency
  await postSensor('sensor.langfuse_avg_latency', avgLatency, {
    friendly_name: 'Langfuse Average Latency',
    icon: 'mdi:timer-outline',
    unit_of_measurement: 's',
    state_class: 'measurement',
    last_updated: nowIso
  });

  // Publish Sensor 5: Last Model Used
  await postSensor('sensor.langfuse_last_model', lastModel, {
    friendly_name: 'Langfuse Last Used Model',
    icon: 'mdi:robot',
    last_tokens: lastTokens,
    last_cost: lastCost,
    last_timestamp: lastTime,
    last_updated: nowIso
  });
}

async function main() {
  log(`Starting Langfuse Home Assistant Sensor Reporter (interval: ${INTERVAL_MS / 1000}s)...`);

  // Wait for ClickHouse readiness
  for (let i = 0; i < 30; i++) {
    const ready = await new Promise((res) => {
      const req = http.get(`http://${CLICKHOUSE_HOST}:${CLICKHOUSE_PORT}/ping`, { timeout: 2000 }, (r) => {
        res(r.statusCode === 200);
      });
      req.on('error', () => res(false));
    });
    if (ready) {
      log('ClickHouse is ready.');
      break;
    }
    await new Promise((r) => setTimeout(r, 2000));
  }

  // Initial publish
  await collectAndPublish();

  // Periodic loop
  setInterval(async () => {
    try {
      await collectAndPublish();
    } catch (err) {
      log(`Error in reporter loop: ${err.message}`);
    }
  }, INTERVAL_MS);
}

main();
