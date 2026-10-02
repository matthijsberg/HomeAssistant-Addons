/**
 * Open HEMS - Model Parameter History & Calibration Audit Modal
 * Manages the parameter adjustment inspection dialog, stepped trajectory chart, and audit log.
 */
(function(window) {
    'use strict';

    let activeParamHistoryId = 'building_ua';
    let activeParamHistoryTimeframe = 'quarter';
    let paramHistoryChartInstance = null;

    async function openParameterHistoryModal(paramId, timeframe = 'quarter') {
        activeParamHistoryId = paramId || 'building_ua';
        activeParamHistoryTimeframe = timeframe || 'quarter';
        const modal = document.getElementById('param-history-modal');
        if (!modal) return;
        modal.classList.remove('hidden');
        await setParamHistoryTimeframe(activeParamHistoryTimeframe);
    }

    async function setParamHistoryTimeframe(tf) {
        activeParamHistoryTimeframe = tf;
        ['30d', 'quarter', '1y', 'all'].forEach(t => {
            const btn = document.getElementById('btn-ph-tf-' + t);
            if (btn) {
                if (t === tf) {
                    btn.className = 'px-2.5 py-1 rounded transition font-medium bg-blue-600 text-white shadow';
                } else {
                    btn.className = 'px-2.5 py-1 rounded transition font-medium text-slate-400 hover:text-white';
                }
            }
        });
        await loadParameterHistory();
    }

    async function loadParameterHistory() {
        try {
            const res = await fetch(`./api/model/parameter-history?parameter_id=${encodeURIComponent(activeParamHistoryId)}&timeframe=${encodeURIComponent(activeParamHistoryTimeframe)}`);
            if (!res.ok) return;
            const d = await res.json();

            const titleEl = document.getElementById('param-hist-modal-title');
            const descEl = document.getElementById('param-hist-modal-desc');
            const statCurEl = document.getElementById('param-hist-stat-current');
            const statStartEl = document.getElementById('param-hist-stat-start');
            const statDriftEl = document.getElementById('param-hist-stat-drift');
            const statCountEl = document.getElementById('param-hist-stat-count');
            const chartUnitEl = document.getElementById('param-hist-chart-unit');
            const driftBadgeEl = document.getElementById('param-hist-net-drift-badge');

            if (titleEl) titleEl.textContent = `${d.parameter_name || activeParamHistoryId}`;
            if (descEl) descEl.textContent = `${d.description || ''} · ${d.timeframe_label || ''}`;
            if (statCurEl) statCurEl.textContent = `${d.current_value} ${d.unit}`;
            if (statStartEl) statStartEl.textContent = `${d.start_value} ${d.unit}`;
            if (chartUnitEl) chartUnitEl.textContent = `Eenheid: ${d.unit}`;

            const drift = Number(d.net_drift_pct || 0);
            const driftSign = drift > 0 ? '+' : '';
            const driftColor = drift === 0 ? 'text-slate-400' : (drift < 0 ? 'text-blue-400' : 'text-amber-400');
            if (statDriftEl) {
                statDriftEl.className = `text-base font-bold font-mono mt-0.5 ${driftColor}`;
                statDriftEl.textContent = `${driftSign}${drift}%`;
            }
            if (statCountEl) statCountEl.textContent = `${d.total_adjustments || 0} updates`;

            if (driftBadgeEl) {
                const badgeBg = Math.abs(drift) <= 5.0 ? 'bg-emerald-950/60 text-emerald-300 border-emerald-500/40' : 'bg-amber-950/60 text-amber-300 border-amber-500/40';
                driftBadgeEl.innerHTML = `<span class="px-2.5 py-0.5 rounded-full text-[10px] font-mono font-bold border ${badgeBg}">Netto Drift: ${driftSign}${drift}%</span>`;
            }

            // Render Audit Table
            const tBody = document.getElementById('param-hist-table-body');
            if (tBody) {
                const records = d.records || [];
                if (records.length === 0) {
                    tBody.innerHTML = '<tr><td colspan="5" class="py-4 text-center text-slate-500 font-mono">Geen aanpassingen gevonden in deze periode.</td></tr>';
                } else {
                    tBody.innerHTML = records.map(r => {
                        const rDrift = Number(r.drift_pct || 0);
                        const rDriftSign = rDrift > 0 ? '+' : '';
                        const rDriftColor = rDrift === 0 ? 'text-slate-400' : (rDrift < 0 ? 'text-blue-400' : 'text-amber-400');
                        let typeBadge = '';
                        if (r.change_type === 'accepted') {
                            typeBadge = '<span class="px-2 py-0.5 rounded-full text-[9px] font-bold bg-blue-950 text-blue-300 border border-blue-800">Geaccepteerd</span>';
                        } else if (r.change_type === 'auto_applied') {
                            typeBadge = '<span class="px-2 py-0.5 rounded-full text-[9px] font-bold bg-emerald-950 text-emerald-400 border border-emerald-800">Automatisch</span>';
                        } else {
                            typeBadge = '<span class="px-2 py-0.5 rounded-full text-[9px] font-bold bg-slate-800 text-slate-300">Basis</span>';
                        }

                        return `
                            <tr class="hover:bg-slate-800/30 transition">
                                <td class="py-2 px-3 font-mono text-slate-300 whitespace-nowrap">${r.formatted_date}</td>
                                <td class="py-2 px-3 text-center font-mono">
                                    <span class="text-slate-500">${r.old_value}</span>
                                    <span class="text-slate-600 px-1">&rarr;</span>
                                    <span class="text-white font-bold">${r.new_value}</span>
                                    <span class="text-[10px] text-slate-400 ml-0.5">${d.unit}</span>
                                </td>
                                <td class="py-2 px-3 text-center font-mono font-bold ${rDriftColor}">${rDriftSign}${rDrift}%</td>
                                <td class="py-2 px-3 text-center whitespace-nowrap">${typeBadge}</td>
                                <td class="py-2 px-3 text-slate-400 text-[10px] leading-tight font-sans">${r.evidence || '--'}</td>
                            </tr>
                        `;
                    }).join('');
                }
            }

            // Render Chart
            renderParamHistoryChart(d);
        } catch (e) {
            console.warn('Error loading parameter history:', e);
        }
    }

    function renderParamHistoryChart(data) {
        const canvas = document.getElementById('chart-param-history');
        if (!canvas) return;

        if (paramHistoryChartInstance) {
            paramHistoryChartInstance.destroy();
            paramHistoryChartInstance = null;
        }

        const points = data.timeline || [];
        if (points.length === 0) return;

        const labels = points.map(p => p.date_short || p.label);
        const values = points.map(p => p.value);

        const ctx = canvas.getContext('2d');
        const gradient = ctx.createLinearGradient(0, 0, 0, 200);
        gradient.addColorStop(0, 'rgba(59, 130, 246, 0.35)');
        gradient.addColorStop(1, 'rgba(59, 130, 246, 0.0)');

        paramHistoryChartInstance = new Chart(canvas, {
            type: 'line',
            data: {
                labels: labels,
                datasets: [{
                    label: data.parameter_name,
                    data: values,
                    borderColor: '#3B82F6',
                    backgroundColor: gradient,
                    borderWidth: 2.2,
                    pointBackgroundColor: '#60A5FA',
                    pointBorderColor: '#1E3A8A',
                    pointHoverRadius: 6,
                    pointRadius: 4,
                    fill: true,
                    stepped: true,
                    tension: 0
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                interaction: {
                    mode: 'index',
                    intersect: false
                },
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        backgroundColor: '#0B0F17',
                        borderColor: '#1E293B',
                        borderWidth: 1,
                        titleColor: '#F8FAFC',
                        bodyColor: '#94A3B8',
                        callbacks: {
                            label: function(context) {
                                const pt = points[context.dataIndex];
                                const drift = pt ? (pt.drift_pct ? ` (Drift: ${pt.drift_pct}%)` : '') : '';
                                return `Waarde: ${context.parsed.y} ${data.unit}${drift}`;
                            },
                            afterLabel: function(context) {
                                const pt = points[context.dataIndex];
                                return pt && pt.evidence ? `Grondslag: ${pt.evidence}` : '';
                            }
                        }
                    }
                },
                scales: {
                    x: {
                        grid: { color: 'rgba(30, 41, 59, 0.4)' },
                        ticks: { color: '#64748B', font: { family: 'monospace', size: 10 } }
                    },
                    y: {
                        grid: { color: 'rgba(30, 41, 59, 0.4)' },
                        ticks: {
                            color: '#94A3B8',
                            font: { family: 'monospace', size: 10 },
                            callback: val => `${val} ${data.unit}`
                        }
                    }
                }
            }
        });
    }

    function closeParamHistoryModal() {
        const modal = document.getElementById('param-history-modal');
        if (modal) modal.classList.add('hidden');
    }

    function handleParamHistoryModalBackdrop(event) {
        if (event.target && event.target.id === 'param-history-modal') {
            closeParamHistoryModal();
        }
    }

    window.openParameterHistoryModal = openParameterHistoryModal;
    window.setParamHistoryTimeframe = setParamHistoryTimeframe;
    window.closeParamHistoryModal = closeParamHistoryModal;
    window.handleParamHistoryModalBackdrop = handleParamHistoryModalBackdrop;

})(window);
