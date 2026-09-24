/**
 * Open HEMS - Thermal Trajectory Charts Module
 * Encapsulates Chart.js rendering for DHW (Boilervat) and Space Heating (CV 2R1C),
 * including counterfactual trajectories, uncertainty margins (P05/P95), and spitsblok overlays.
 */
(function(window) {
    'use strict';

    let dhwTempChartInstance = null;
    let heatingForecastChartInstance = null;
    let dhwHistoryChartInstance = null;
    let heatingHistoryChartInstance = null;

    // =========================================================================
    // 1. REUSABLE THERMAL TRAJECTORY CHART FACTORY
    // =========================================================================
    function createThermalTrajectoryChart(canvas, opts) {
        const existing = Chart.getChart(canvas);
        if (existing) {
            existing.destroy();
        }

        const chartDatasets = [];
        const isHistory = (opts.mode === 'history');

        // If not history mode, include uncertainty & counterfactual bands
        if (!isHistory) {
            // 1. Counterfactual Upper boundary: Zonder Verwarming P05
            if (opts.unhP05 && opts.unhP05.length > 0) {
                chartDatasets.push({
                    id: opts.unhP05Id || 'dhw_unh_p05',
                    label: opts.unhP05Label || 'Marge Onverwarmd P05 (°C)',
                    data: opts.unhP05 || [],
                    yAxisID: 'y',
                    borderColor: 'rgba(148, 163, 184, 0.25)',
                    backgroundColor: 'transparent',
                    borderWidth: 1.0,
                    borderDash: [2, 2],
                    fill: false,
                    pointRadius: 0,
                    tension: 0.25,
                    order: 6
                });
            }
            // 2. Counterfactual Lower boundary: Zonder Verwarming P95 with grey fill to P05
            if (opts.unhP95 && opts.unhP95.length > 0) {
                chartDatasets.push({
                    id: opts.unhP95Id || 'dhw_unh_p95',
                    label: opts.unhP95Label || 'Marge Onverwarmd (P05–P95)',
                    data: opts.unhP95 || [],
                    yAxisID: 'y',
                    borderColor: 'rgba(148, 163, 184, 0.35)',
                    backgroundColor: 'rgba(148, 163, 184, 0.12)',
                    borderWidth: 1.0,
                    borderDash: [3, 3],
                    fill: '-1',
                    pointRadius: 0,
                    tension: 0.25,
                    order: 7
                });
            }
            // 3. Counterfactual Line: Zonder Verwarming P50 (Light Slate Grey Dashed Line)
            if (opts.unhTemps && opts.unhTemps.length > 0) {
                chartDatasets.push({
                    id: (opts.unhP50Id === 'indoor_unh_p50') ? 'indoor_unh_p50' : 'dhw_unh_p50',
                    label: opts.unhP50Label || 'Zonder Verwarming (°C)',
                    data: opts.unhTemps || [],
                    yAxisID: 'y',
                    borderColor: '#94A3B8',
                    backgroundColor: 'transparent',
                    borderWidth: 2.2,
                    borderDash: [5, 4],
                    fill: false,
                    tension: 0.25,
                    order: 5,
                    pointRadius: 0,
                    pointHoverRadius: 5
                });
            }
            // 4. Expected Trajectory Upper boundary: P05
            if (opts.tempsP05 && opts.tempsP05.length > 0) {
                chartDatasets.push({
                    id: opts.p05Id || 'dhw_p05',
                    label: opts.p05Label || 'Marge Ondergrens P05 (°C)',
                    data: opts.tempsP05 || [],
                    yAxisID: 'y',
                    borderColor: opts.p05BorderColor || 'rgba(245, 158, 11, 0.35)',
                    backgroundColor: 'transparent',
                    borderWidth: 1.2,
                    borderDash: [3, 3],
                    fill: false,
                    pointRadius: 0,
                    tension: 0.25,
                    order: 1
                });
            }
            // 5. Expected Trajectory Lower boundary: P95 with filled margin to P05
            if (opts.tempsP95 && opts.tempsP95.length > 0) {
                chartDatasets.push({
                    id: opts.p95Id || 'dhw_p95',
                    label: opts.p95Label || 'Marge (P05–P95)',
                    data: opts.tempsP95 || [],
                    yAxisID: 'y',
                    borderColor: opts.p95BorderColor || 'rgba(245, 158, 11, 0.45)',
                    backgroundColor: opts.marginBgColor || 'rgba(251, 191, 36, 0.15)',
                    borderWidth: 1.2,
                    borderDash: [4, 4],
                    fill: '-1',
                    pointRadius: 0,
                    tension: 0.25,
                    order: 2
                });
            }
        }

        // Main Temperature Series (P50 or Actual)
        chartDatasets.push({
            id: (opts.p50Id === 'indoor_temp') ? 'indoor_temp' : (opts.p50Id || 'dhw_p50'),
            label: opts.p50Label || (isHistory ? 'Temperatuur (°C)' : 'Verwachte Temperatuur P50 (°C)'),
            data: opts.temps || [],
            yAxisID: 'y',
            borderColor: opts.primaryColor || '#F59E0B',
            backgroundColor: opts.fill ? (opts.primaryBgFill || 'rgba(245, 158, 11, 0.08)') : 'transparent',
            borderWidth: opts.borderWidth || 2.5,
            tension: opts.tension !== undefined ? opts.tension : 0.25,
            fill: opts.fill !== undefined ? opts.fill : false,
            pointRadius: 0,
            pointHoverRadius: 4,
            order: 3
        });

        // Reference lines (Comfort, Target, Boost) if provided
        if (opts.comfortTemp !== undefined && opts.comfortTemp !== null) {
            chartDatasets.push({
                id: opts.comfortId || 'dhw_comfort',
                label: opts.comfortLabel || 'Comfortgrens',
                data: Array(opts.labels.length).fill(opts.comfortTemp),
                yAxisID: 'y',
                borderColor: 'rgba(239, 68, 68, 0.75)',
                borderDash: [5, 5],
                backgroundColor: 'transparent',
                borderWidth: 1.5,
                pointRadius: 0,
                order: 4
            });
        }

        if (opts.effectiveComfortTemp !== undefined && opts.effectiveComfortTemp !== null && Math.abs(opts.effectiveComfortTemp - (opts.comfortTemp || 40.0)) > 0.05) {
            chartDatasets.push({
                id: 'dhw_effective_comfort',
                label: `Effectieve Grens (${opts.effectiveComfortTemp}°C)`,
                data: Array(opts.labels.length).fill(opts.effectiveComfortTemp),
                yAxisID: 'y',
                borderColor: 'rgba(244, 63, 94, 0.85)',
                borderDash: [2, 2],
                backgroundColor: 'transparent',
                borderWidth: 1.5,
                pointRadius: 0,
                order: 4
            });
        }

        if (opts.targetTemp !== undefined && opts.targetTemp !== null) {
            chartDatasets.push({
                id: opts.targetId || 'dhw_target',
                label: opts.targetLabel || 'Doeltemperatuur',
                data: Array(opts.labels.length).fill(opts.targetTemp),
                yAxisID: 'y',
                borderColor: 'rgba(16, 185, 129, 0.75)',
                borderDash: [5, 5],
                backgroundColor: 'transparent',
                borderWidth: 1.5,
                pointRadius: 0,
                order: 5
            });
        }

        if (opts.boostTemp !== undefined && opts.boostTemp !== null) {
            chartDatasets.push({
                id: opts.boostId || 'dhw_boost',
                label: opts.boostLabel || 'Zonnebuffer Doel',
                data: Array(opts.labels.length).fill(opts.boostTemp),
                yAxisID: 'y',
                borderColor: 'rgba(168, 85, 247, 0.75)',
                borderDash: [4, 4],
                backgroundColor: 'transparent',
                borderWidth: 1.5,
                pointRadius: 0,
                order: 6
            });
        }

        // Extra line (e.g. outdoor temperature)
        if (opts.extraLine) {
            chartDatasets.push({
                id: opts.extraLine.id || 'outdoor_temp',
                label: opts.extraLine.label,
                data: opts.extraLine.data,
                yAxisID: 'y',
                borderColor: opts.extraLine.borderColor || '#60A5FA',
                backgroundColor: 'transparent',
                borderWidth: opts.extraLine.borderWidth || 1.8,
                tension: opts.extraLine.tension !== undefined ? opts.extraLine.tension : 0.25,
                fill: false,
                pointRadius: 0,
                pointHoverRadius: 4,
                order: opts.extraLine.order || 0
            });
        }

        // Secondary axis bar dataset (Demand / Heat Loss)
        if (opts.demandKwh !== undefined) {
            chartDatasets.push({
                id: (opts.demandId === 'th_loss_demand') ? 'th_loss_demand' : (opts.demandId || 'dhw_demand'),
                label: opts.demandLabel || 'Warmtevraag (kWh)',
                data: opts.demandKwh || [],
                type: 'bar',
                yAxisID: 'y1',
                backgroundColor: 'rgba(56, 189, 248, 0.45)',
                borderColor: '#38BDF8',
                borderWidth: 1,
                hoverBackgroundColor: '#38BDF8',
                borderRadius: opts.demandBorderRadius || 2,
                order: 8
            });
        }

        const ctx = canvas.getContext('2d');
        return new Chart(ctx, {
            type: 'line',
            data: {
                labels: opts.labels,
                datasets: chartDatasets
            },
            options: {
                spitsblokRanges: opts.spitsblokRanges,
                advisedOffRanges: opts.advisedOffRanges,
                heatingRanges: opts.heatingRanges,
                responsive: true,
                maintainAspectRatio: false,
                interaction: { mode: 'index', intersect: false },
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        enabled: false,
                        external: function(context) {
                            if (opts.tooltipHandler) {
                                opts.tooltipHandler(context);
                            }
                        }
                    }
                },
                scales: {
                    x: {
                        grid: { color: 'rgba(255, 255, 255, 0.05)' },
                        ticks: {
                            color: '#94a3b8',
                            font: { size: 10, family: 'monospace' },
                            maxTicksLimit: 16
                        }
                    },
                    y: {
                        position: 'left',
                        min: opts.yMin !== undefined ? opts.yMin : undefined,
                        max: opts.yMax !== undefined ? opts.yMax : undefined,
                        suggestedMin: opts.ySuggestedMin,
                        suggestedMax: opts.ySuggestedMax,
                        grace: (opts.yMin !== undefined || opts.yMax !== undefined) ? undefined : '5%',
                        title: {
                            display: true,
                            text: opts.yTitle || 'Temperatuur (°C)',
                            color: opts.primaryColor || '#F59E0B',
                            font: { size: 10, weight: 'bold' }
                        },
                        grid: { color: 'rgba(255, 255, 255, 0.05)' },
                        ticks: {
                            color: opts.primaryColor || '#F59E0B',
                            font: { size: 10, family: 'monospace' },
                            callback: v => `${v}°C`
                        }
                    },
                    y1: {
                        position: 'right',
                        min: opts.y1Min !== undefined ? opts.y1Min : 0,
                        max: opts.y1Max !== undefined ? opts.y1Max : undefined,
                        suggestedMax: opts.y1SuggestedMax || 2.0,
                        grid: { drawOnChartArea: false },
                        title: {
                            display: true,
                            text: opts.y1Title || 'Warmtevraag (kWh)',
                            color: '#38BDF8',
                            font: { size: 10, weight: 'bold' }
                        },
                        ticks: {
                            color: '#38BDF8',
                            font: { size: 10, family: 'monospace' },
                            callback: v => `${Number(v).toFixed(1)} kWh`
                        }
                    }
                }
            }
        });
    }

    // =========================================================================
    // 2. DHW TOOLTIP HANDLER
    // =========================================================================
    function customDhwTooltipHandler(context) {
        const { chart, tooltip } = context;
        if (typeof window.createOrGetTooltipEl !== 'function' || typeof window.renderCustomTooltip !== 'function') return;
        const tooltipEl = window.createOrGetTooltipEl(chart);
        if (tooltip.opacity === 0 || !tooltip.body || !tooltip.dataPoints || tooltip.dataPoints.length === 0) {
            tooltipEl.style.opacity = '0';
            tooltipEl.style.pointerEvents = 'none';
            return;
        }
        const dataIndex = tooltip.dataPoints[0].dataIndex;

        let tempC = 0.0, comfort = 40.0, target = 50.0, liters = 0, kwhVal = 0.0, p05 = 0.0, p95 = 0.0, unheatedC = 0.0;
        chart.data.datasets.forEach(ds => {
            const v = ds.data[dataIndex];
            if (ds.id === 'dhw_p50') tempC = Number(v) || 0.0;
            else if (ds.id === 'dhw_unh_p50') unheatedC = Number(v) || 0.0;
            else if (ds.id === 'dhw_p05') p05 = Number(v) || 0.0;
            else if (ds.id === 'dhw_p95') p95 = Number(v) || 0.0;
            else if (ds.id === 'dhw_comfort') comfort = Number(v) || 0.0;
            else if (ds.id === 'dhw_target') target = Number(v) || 0.0;
            else if (ds.id === 'dhw_demand') {
                kwhVal = Number(v) || 0.0;
                liters = Math.round(kwhVal * 28.66);
            } else if (ds.label) {
                const lbl = ds.label.toLowerCase();
                if (lbl.includes('p50') || lbl.includes('boilertemperatuur')) tempC = Number(v) || 0.0;
                else if (lbl.includes('zonder')) unheatedC = Number(v) || 0.0;
                else if (lbl.includes('p05')) p05 = Number(v) || 0.0;
                else if (lbl.includes('p95')) p95 = Number(v) || 0.0;
                else if (lbl.includes('comfort')) comfort = Number(v) || 0.0;
                else if (lbl.includes('doel')) target = Number(v) || 0.0;
                else if (lbl.includes('warmtevraag')) {
                    kwhVal = Number(v) || 0.0;
                    liters = Math.round(kwhVal * 28.66);
                }
            }
        });

        if (tempC === 0.0) {
            const p50Ds = chart.data.datasets.find(d => d.id === 'dhw_p50' || (d.label && d.label.toLowerCase().includes('p50')));
            if (p50Ds && p50Ds.data[dataIndex] !== undefined) {
                tempC = Number(p50Ds.data[dataIndex]) || 0.0;
            }
        }

        const tempBadgeColor = tempC >= 45 ? 'text-emerald-400 bg-emerald-950/80 border-emerald-800' : (tempC >= 40 ? 'text-amber-400 bg-amber-950/80 border-amber-800' : 'text-red-400 bg-red-950/80 border-red-800');

        window.renderCustomTooltip(context, {
            dotColor: 'bg-amber-400',
            headerBadge: `<span class="text-[10px] font-mono font-semibold px-2 py-0.5 rounded border ${tempBadgeColor}">Tank: ${tempC.toFixed(1)}°C</span>`,
            rows: [
                { type: 'line', color: '#F59E0B', label: 'Boilertemperatuur (P50)', val: `${tempC.toFixed(1)}°C`, valClass: 'font-bold text-amber-300 font-mono' },
                p95 > 0 ? { type: 'band', bgColor: 'rgba(251, 191, 36, 0.25)', borderColor: 'rgba(245, 158, 11, 0.5)', label: 'Bandbreedte (P95–P05)', labelClass: 'text-amber-200/80', val: `${p95.toFixed(1)}°C (veel) – ${p05.toFixed(1)}°C (weinig)`, valClass: 'font-mono text-amber-300/90', textClass: 'text-[11px]' } : null,
                unheatedC > 0 ? { type: 'dashed-line', color: '#94A3B8', label: 'Zonder Verwarming', labelClass: 'text-slate-400', val: `${unheatedC.toFixed(1)}°C`, valClass: 'text-slate-300 font-mono', textClass: 'text-[11px]' } : null,
                { type: 'dashed-line', color: '#EF4444', label: 'Comfortgrens', labelClass: 'text-slate-400', val: `${comfort.toFixed(1)}°C`, valClass: 'text-red-400 font-mono' },
                { type: 'dashed-line', color: '#10B981', label: 'Doeltemperatuur', labelClass: 'text-slate-400', val: `${target.toFixed(1)}°C`, valClass: 'text-emerald-400 font-mono' },
                { type: 'bar', color: 'rgba(56, 189, 248, 0.6)', label: 'Warmtevraag', labelClass: 'text-slate-300', val: `${kwhVal.toFixed(2)} kWh (≈ ${liters} L V₄₀)`, valClass: 'font-bold text-sky-400 font-mono', borderTop: true }
            ]
        });
    }

    // =========================================================================
    // 3. CV SPACE HEATING TOOLTIP HANDLER
    // =========================================================================
    function customHeatingTooltipHandler(context) {
        const { chart, tooltip } = context;
        if (typeof window.createOrGetTooltipEl !== 'function' || typeof window.renderCustomTooltip !== 'function') return;
        const tooltipEl = window.createOrGetTooltipEl(chart);
        if (tooltip.opacity === 0 || !tooltip.body || !tooltip.dataPoints || tooltip.dataPoints.length === 0) {
            tooltipEl.style.opacity = '0';
            tooltipEl.style.pointerEvents = 'none';
            return;
        }
        const dataIndex = tooltip.dataPoints[0].dataIndex;

        let outTemp = 0.0, inTemp = 0.0, unhTemp = 0.0, inP05 = 0.0, inP95 = 0.0, thKwh = 0.0;
        chart.data.datasets.forEach(ds => {
            const v = ds.data[dataIndex];
            if (ds.id === 'outdoor_temp') outTemp = Number(v) || 0.0;
            else if (ds.id === 'indoor_temp') inTemp = Number(v) || 0.0;
            else if (ds.id === 'indoor_unh_p50') unhTemp = Number(v) || 0.0;
            else if (ds.id === 'indoor_p05') inP05 = Number(v) || 0.0;
            else if (ds.id === 'indoor_p95') inP95 = Number(v) || 0.0;
            else if (ds.id === 'th_loss_demand' || ds.id === 'th_loss' || ds.id === 'demand') thKwh = Number(v) || 0.0;
        });

        const inBadgeColor = inTemp >= 20.0 ? 'text-emerald-400 bg-emerald-950/80 border-emerald-800' : 'text-amber-400 bg-amber-950/80 border-amber-800';

        window.renderCustomTooltip(context, {
            dotColor: 'bg-amber-400',
            headerBadge: `<span class="text-[10px] font-mono font-semibold px-2 py-0.5 rounded border ${inBadgeColor}">Binnen: ${inTemp.toFixed(1)}°C</span>`,
            rows: [
                { type: 'line', color: '#F59E0B', label: 'Binnentemperatuur (P50)', val: `${inTemp.toFixed(1)}°C`, valClass: 'font-bold text-amber-300 font-mono' },
                inP95 > 0 ? { type: 'band', bgColor: 'rgba(251, 191, 36, 0.25)', borderColor: 'rgba(245, 158, 11, 0.5)', label: 'Bandbreedte (P95–P05)', labelClass: 'text-amber-200/80', val: `${inP05.toFixed(1)}°C – ${inP95.toFixed(1)}°C`, valClass: 'text-amber-300 font-mono', textClass: 'text-[11px]' } : null,
                unhTemp > 0 ? { type: 'dashed-line', color: '#94A3B8', label: 'Zonder Verwarming', labelClass: 'text-slate-400', val: `${unhTemp.toFixed(1)}°C`, valClass: 'font-medium text-slate-300 font-mono' } : null,
                { type: 'line', color: '#60A5FA', label: 'Buitentemperatuur', labelClass: 'text-slate-300', val: `${outTemp.toFixed(1)}°C`, valClass: 'font-medium text-blue-300 font-mono' },
                { type: 'bar', color: 'rgba(56, 189, 248, 0.7)', label: 'Warmtevraag Woning', labelClass: 'text-slate-300', val: `${thKwh.toFixed(2)} kWh`, valClass: 'font-bold text-sky-300 font-mono', borderTop: true }
            ]
        });
    }

    // =========================================================================
    // 4. CV SPACE HEATING FORECAST RENDERER
    // =========================================================================
    async function renderHeatingForecastChart() {
        const canvas = document.getElementById('chart-heating-forecast');
        if (!canvas) return;
        try {
            const horizonVal = window.OpenHEMSChartEngine ? window.OpenHEMSChartEngine.getHorizon('prediction') : '24h';
            const resMode = window.predictionResolution || '15m';
            const res = await fetch('./api/model/heating-forecast?resolution=' + encodeURIComponent(resMode) + '&horizon=' + encodeURIComponent(horizonVal));
            if (!res.ok) return;
            const d = await res.json();
            if (!d.labels || d.labels.length === 0) return;

            const isActive = (d.thermostat_active !== false);
            const tSet = d.thermostat_setpoint_c || 20.0;
            const tComfort = 19.6;

            const statusEl = document.getElementById('heating-kpi-status');
            if (statusEl) {
                if (isActive) {
                    statusEl.className = 'px-2.5 py-0.5 rounded-md text-[10px] font-bold border bg-emerald-950/60 border-emerald-500/40 text-emerald-300';
                    statusEl.textContent = `Thermostaat: Aan (${tSet.toFixed(1)}°C)`;
                } else {
                    statusEl.className = 'px-2.5 py-0.5 rounded-md text-[10px] font-bold border bg-slate-900 border-slate-700 text-slate-400';
                    statusEl.textContent = `Thermostaat: Uit (0 W)`;
                }
            }
            const kwhEl = document.getElementById('heating-kpi-kwh');
            if (kwhEl) kwhEl.textContent = `⚡ Stroom: ${d.total_electrical_kwh || 0} kWh`;
            const costEl = document.getElementById('heating-kpi-cost');
            if (costEl) costEl.textContent = `💶 Kosten: €${Number(d.total_cost_eur || 0).toFixed(2)}`;

            const labels = d.labels;
            const outTemps = d.outdoor_temps_c || [];
            const inTemps = d.indoor_temps_c || [];
            const unhTemps = d.unheated_temps_c || inTemps;
            const inTempsP05 = d.indoor_temps_p05_c || inTemps;
            const inTempsP95 = d.indoor_temps_p95_c || inTemps;
            const unhP05 = d.unheated_temps_p05_c || unhTemps;
            const unhP95 = d.unheated_temps_p95_c || unhTemps;
            const thLoss = d.thermal_loss_kw || [];

            const intervalMult = (resMode === '15m') ? 0.25 : 1.0;
            const demandKwh = thLoss.map(kw => Number((kw * intervalMult).toFixed(2)));

            const allTemps = [...inTemps, ...unhTemps, ...outTemps].filter(t => t !== null && t !== undefined);
            const minT = Math.min(...allTemps, tComfort, 18.0);
            const maxT = Math.max(...allTemps, tSet, 22.0);

            heatingForecastChartInstance = createThermalTrajectoryChart(canvas, {
                labels: labels,
                temps: inTemps,
                tempsP05: inTempsP05,
                tempsP95: inTempsP95,
                unhTemps: unhTemps,
                unhP05: unhP05,
                unhP95: unhP95,
                demandKwh: demandKwh,
                comfortTemp: tComfort,
                targetTemp: tSet,
                p50Id: 'indoor_temp',
                unhP50Id: 'indoor_unh_p50',
                p05Id: 'indoor_p05',
                p95Id: 'indoor_p95',
                unhP05Id: 'indoor_unh_p05',
                unhP95Id: 'indoor_unh_p95',
                comfortId: 'indoor_comfort',
                targetId: 'indoor_target',
                demandId: 'th_loss_demand',
                demandLabel: 'Warmteverlies (kWh)',
                comfortLabel: `Comfortgrens (${tComfort}°C)`,
                targetLabel: `Doeltemperatuur (${tSet.toFixed(1)}°C)`,
                primaryColor: '#F59E0B',
                primaryBgFill: 'rgba(251, 191, 36, 0.15)',
                p05BorderColor: 'rgba(245, 158, 11, 0.35)',
                p95BorderColor: 'rgba(245, 158, 11, 0.45)',
                marginBgColor: 'rgba(251, 191, 36, 0.15)',
                yTitle: 'Binnentemperatuur (°C)',
                y1Title: 'Warmteverlies (kWh)',
                ySuggestedMin: Math.floor(minT - 0.5),
                ySuggestedMax: Math.ceil(maxT + 0.5),
                y1SuggestedMax: (resMode === '15m') ? 1.5 : 4.0,
                spitsblokRanges: d.forced_off_ranges,
                advisedOffRanges: d.advised_off_ranges,
                heatingRanges: d.heating_ranges,
                tooltipHandler: customHeatingTooltipHandler,
                extraLine: {
                    id: 'outdoor_temp',
                    label: 'Buitentemperatuur (°C)',
                    data: outTemps,
                    borderColor: '#60A5FA',
                    borderWidth: 1.8,
                    tension: 0.35,
                    order: 0
                }
            });

            // Update Heating Decision Explanation Box
            const expl = d.decision_explanation;
            if (expl) {
                const textEl = document.getElementById('heating-eval-comfort-text');
                if (textEl && expl.comfort_text) textEl.textContent = expl.comfort_text;
                const runsEl = document.getElementById('heating-box-runs-val');
                if (runsEl && expl.planned_runs_text) runsEl.textContent = expl.planned_runs_text;
                const bufEl = document.getElementById('heating-box-buffer-val');
                if (bufEl && expl.buffer_text) bufEl.textContent = expl.buffer_text;
                const lockEl = document.getElementById('heating-box-lockout-val');
                if (lockEl && expl.lockout_text) lockEl.textContent = expl.lockout_text;
                const pillEl = document.getElementById('heating-box-status-pill');
                if (pillEl && expl.status_badge) {
                    pillEl.innerHTML = `<span class="px-2 py-0.5 rounded text-[10px] font-mono font-medium bg-emerald-950 text-emerald-300 border border-emerald-800/40">${expl.status_badge}</span>`;
                }
            }
        } catch (e) {
            console.warn("Error rendering heating forecast chart:", e);
        }
    }

    // =========================================================================
    // 5. DHW TEMPERATURE FORECAST RENDERER
    // =========================================================================
    async function renderDhwTemperatureChart() {
        const canvas = document.getElementById('chart-dhw-temperature');
        if (!canvas) return;
        try {
            const lang = window.OpenHEMSi18n ? window.OpenHEMSi18n.getLang() : 'nl';
            const horizonVal = window.OpenHEMSChartEngine ? window.OpenHEMSChartEngine.getHorizon('prediction') : '24h';
            const resMode = window.predictionResolution || '15m';
            const res = await fetch('./api/model/dhw-status?resolution=' + encodeURIComponent(resMode) + '&horizon=' + encodeURIComponent(horizonVal) + '&lang=' + encodeURIComponent(lang));
            if (!res.ok) return;
            const data = await res.json();
            const traj = data.trajectory || {};
            if (!traj.labels || traj.labels.length === 0) return;

            const labels = traj.labels;
            const temps = traj.temperatures_c || [];
            const tempsP05 = traj.temperatures_p05_c || temps;
            const tempsP95 = traj.temperatures_p95_c || temps;
            const demandsKwh = traj.demand_kwh_th || [];
            const kwhThArr = demandsKwh.map(k => Number(k || 0).toFixed(2));
            
            // Detect if 60C boost is present
            const maxTempInTraj = Math.max(...temps, ...tempsP05, 50.0);
            const isBoostMode = (maxTempInTraj >= 53.0);
            const ySuggestedMax = isBoostMode ? 64.0 : 55.0;

            const unh = data.unheated_trajectory || {};
            const unhTemps = unh.temperatures_c || [];
            const unhP05 = unh.temperatures_p05_c || unhTemps;
            const unhP95 = unh.temperatures_p95_c || unhTemps;

            // Update Decision Box below chart
            const dec = data.decision || {};
            const titleEl = document.getElementById('dhw-decision-box-title');
            if (titleEl && dec.box_title) titleEl.innerText = dec.box_title;

            const boxPill = document.getElementById('dhw-box-status-pill');
            if (boxPill) {
                if (dec.badge_html) {
                    boxPill.innerHTML = dec.badge_html;
                } else if (dec.mode_code) {
                    const meta = window.OpenHEMSModeCatalog ? window.OpenHEMSModeCatalog.get(dec.mode_code) : {};
                    boxPill.innerHTML = `<span class="px-2 py-0.5 rounded text-[10px] font-mono font-medium border" style="color: ${meta.color_hex || '#38BDF8'}; border-color: ${meta.color_hex || '#38BDF8'}40; background: ${meta.color_hex || '#38BDF8'}15;">${meta.label || dec.mode_code}</span>`;
                }
            }

            const comfortText = document.getElementById('dhw-eval-comfort-text');
            if (comfortText && dec.explanation) comfortText.innerText = dec.explanation;

            const decisionSummary = document.getElementById('dhw-eval-decision-summary');
            if (decisionSummary && dec.summary) decisionSummary.innerText = dec.summary;

            const runsVal = document.getElementById('dhw-box-runs-val');
            if (runsVal) {
                const runList = data.runs || [];
                if (runList.length === 0) {
                    runsVal.innerText = 'Geen runs gepland';
                } else {
                    runsVal.innerText = runList.map((r, i) => {
                        const targetT = r.target_temp_c ? `${Number(r.target_temp_c).toFixed(0)}°C` : '50°C';
                        return `Run ${i+1}: ${r.start_time}–${r.end_time} tot ${targetT}`;
                    }).join(' · ');
                }
            }

            dhwTempChartInstance = createThermalTrajectoryChart(canvas, {
                labels: labels,
                temps: temps,
                tempsP05: tempsP05,
                tempsP95: tempsP95,
                unhTemps: unhTemps,
                unhP05: unhP05,
                unhP95: unhP95,
                demandKwh: kwhThArr,
                comfortTemp: 40.0,
                effectiveComfortTemp: dec.comfort_boundary_c || null,
                targetTemp: 50.0,
                boostTemp: isBoostMode ? 60.0 : null,
                p50Id: 'dhw_p50',
                unhP50Id: 'dhw_unh_p50',
                p05Id: 'dhw_p05',
                p95Id: 'dhw_p95',
                unhP05Id: 'dhw_unh_p05',
                unhP95Id: 'dhw_unh_p95',
                comfortId: 'dhw_comfort',
                targetId: 'dhw_target',
                boostId: 'dhw_boost',
                demandId: 'dhw_demand',
                comfortLabel: 'Comfortgrens (40°C)',
                targetLabel: 'Doeltemperatuur (50°C)',
                boostLabel: 'Zonnebuffer Doel (60°C)',
                primaryColor: '#F59E0B',
                primaryBgFill: 'rgba(251, 191, 36, 0.15)',
                p05BorderColor: 'rgba(245, 158, 11, 0.35)',
                p95BorderColor: 'rgba(245, 158, 11, 0.45)',
                marginBgColor: 'rgba(251, 191, 36, 0.15)',
                yTitle: 'Boilertemperatuur (°C)',
                ySuggestedMin: 35.0,
                ySuggestedMax: ySuggestedMax,
                y1SuggestedMax: (resMode === '15m') ? 1.5 : 4.0,
                spitsblokRanges: data.forced_off_ranges,
                advisedOffRanges: data.advised_off_ranges,
                heatingRanges: data.heating_ranges,
                tooltipHandler: customDhwTooltipHandler
            });
        } catch (e) {
            console.warn("Error rendering DHW temperature chart:", e);
        }
    }

    // =========================================================================
    // 6. DHW & HEATING HISTORY CHARTS
    // =========================================================================
    async function loadDhwHistoryChart() {
        const canvas = document.getElementById('dhwHistoryChart');
        if (!canvas) return;

        try {
            const rangeSelect = document.getElementById('pp-range-select');
            const rangeVal = rangeSelect ? rangeSelect.value : '24h';
            const resVal = (typeof window.powerProducersResolution !== 'undefined' && window.powerProducersResolution) ? window.powerProducersResolution : '1h';
            const res = await fetch('./api/analytics/dhw_history?range=' + encodeURIComponent(rangeVal) + '&resolution=' + encodeURIComponent(resVal));
            const data = await res.json();
            if (data.status !== 'success') {
                console.error('DHW history error:', data.message);
                return;
            }

            dhwHistoryChartInstance = createThermalTrajectoryChart(canvas, {
                mode: 'history',
                labels: data.labels,
                temps: data.temperatures_c,
                p50Id: 'dhw_p50',
                p50Label: 'Boilertemperatuur (°C)',
                primaryColor: '#F59E0B',
                primaryBgFill: 'rgba(245, 158, 11, 0.08)',
                fill: true,
                tension: 0.3,
                demandKwh: data.demand_kwh_th,
                demandId: 'dhw_demand',
                demandLabel: 'Warmtevraag (kWh)',
                demandBorderRadius: 3,
                yTitle: 'Temperatuur (°C)',
                y1Title: 'Warmtevraag (kWh)',
                yMin: 30,
                yMax: 65,
                y1SuggestedMax: 2.0,
                spitsblokRanges: data.forced_off_ranges,
                advisedOffRanges: data.advised_off_ranges,
                heatingRanges: data.heating_ranges,
                tooltipHandler: customDhwTooltipHandler
            });
        } catch (e) {
            console.error('Failed to load DHW history chart:', e);
        }
    }

    async function loadHeatingHistoryChart() {
        const canvas = document.getElementById('heatingHistoryChart');
        if (!canvas) return;

        try {
            const rangeSelect = document.getElementById('pp-range-select');
            const rangeVal = rangeSelect ? rangeSelect.value : '24h';
            const resVal = (typeof window.powerProducersResolution !== 'undefined' && window.powerProducersResolution) ? window.powerProducersResolution : '1h';
            const res = await fetch('./api/analytics/heating_history?range=' + encodeURIComponent(rangeVal) + '&resolution=' + encodeURIComponent(resVal));
            const data = await res.json();
            if (data.status !== 'success') {
                console.error('Heating history error:', data.message);
                return;
            }

            heatingHistoryChartInstance = createThermalTrajectoryChart(canvas, {
                mode: 'history',
                labels: data.labels,
                temps: data.indoor_temperatures_c,
                p50Id: 'indoor_temp',
                p50Label: 'Binnentemperatuur (°C)',
                primaryColor: '#F59E0B',
                primaryBgFill: 'rgba(245, 158, 11, 0.08)',
                fill: true,
                tension: 0.3,
                extraLine: {
                    id: 'outdoor_temp',
                    label: 'Buitentemperatuur (°C)',
                    data: data.outdoor_temperatures_c,
                    borderColor: '#60A5FA',
                    borderWidth: 1.8,
                    tension: 0.3,
                    order: 0
                },
                demandKwh: data.demand_kwh_th,
                demandId: 'th_loss_demand',
                demandLabel: 'Warmteverlies (kWh)',
                demandBorderRadius: 3,
                yTitle: 'Temperatuur (°C)',
                y1Title: 'Warmteverlies (kWh)',
                ySuggestedMin: 15,
                ySuggestedMax: 25,
                y1SuggestedMax: 2.0,
                spitsblokRanges: data.forced_off_ranges,
                advisedOffRanges: data.advised_off_ranges,
                heatingRanges: data.heating_ranges,
                tooltipHandler: customHeatingTooltipHandler
            });
        } catch (err) {
            console.error('Failed to load heating history chart:', err);
        }
    }

    // Expose public API
    window.createThermalTrajectoryChart = createThermalTrajectoryChart;
    window.customDhwTooltipHandler = customDhwTooltipHandler;
    window.customHeatingTooltipHandler = customHeatingTooltipHandler;
    window.renderHeatingForecastChart = renderHeatingForecastChart;
    window.renderDhwTemperatureChart = renderDhwTemperatureChart;
    window.loadDhwHistoryChart = loadDhwHistoryChart;
    window.loadHeatingHistoryChart = loadHeatingHistoryChart;

})(window);
