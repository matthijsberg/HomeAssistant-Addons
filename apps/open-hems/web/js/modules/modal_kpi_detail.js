/**
 * Open HEMS - KPI Detail Breakdown Modal
 * Handles presentation and breakdowns for top metric cards (Costs, Solar, Savings, Heat Pump).
 */
(function(window) {
    'use strict';

    let currentForecastKpis = null;
    let currentHistoryKpis = null;

    function updateCurrentForecastKpis(kpis) {
        currentForecastKpis = kpis;
    }

    function updateCurrentHistoryKpis(kpis) {
        currentHistoryKpis = kpis;
    }

    function openKpiDetailModal(kpiType, sourceContext = 'prediction') {
        const modal = document.getElementById('kpi-detail-modal');
        if (!modal) return;

        const kpiStore = (sourceContext === 'history') ? currentHistoryKpis : currentForecastKpis;
        if (!kpiStore || !kpiStore[kpiType]) return;

        const item = kpiStore[kpiType];

        const iconEl = document.getElementById('kpi-modal-icon');
        const titleEl = document.getElementById('kpi-modal-title');
        const subtitleEl = document.getElementById('kpi-modal-subtitle');
        const heroMainEl = document.getElementById('kpi-modal-hero-main');
        const heroLabelEl = document.getElementById('kpi-modal-hero-label');
        const heroBadgeEl = document.getElementById('kpi-modal-hero-badge');
        const explEl = document.getElementById('kpi-modal-explanation');
        const listEl = document.getElementById('kpi-modal-breakdown-list');
        const footerEl = document.getElementById('kpi-modal-footer-text');

        const svgs = {
            costs: '<svg class="w-5 h-5 text-blue-400" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8c-1.657 0-3 .895-3 2s1.343 2 3 2 3 .895 3 2-1.343 2-3 2m0-8c1.11 0 2.08.402 2.599 1M12 8V7m0 1v8m0 0v1m0-1c-1.11 0-2.08-.402-2.599-1M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>',
            solar: '<svg class="w-5 h-5 text-amber-400" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 3v1m0 16v1m9-9h-1M4 12H3m15.364 6.364l-.707-.707M6.343 6.343l-.707-.707m12.728 0l-.707.707M6.343 17.657l-.707.707M16 12a4 4 0 11-8 0 4 4 0 018 0z"/></svg>',
            savings: '<svg class="w-5 h-5 text-emerald-400" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 7h8m0 0v8m0-8l-8 8-4-4-6 6"/></svg>',
            heatpump: '<svg class="w-5 h-5 text-cyan-400" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 10V3L4 14h7v7l9-11h-7z"/></svg>'
        };
        if (iconEl) iconEl.innerHTML = svgs[kpiType] || '';
        if (titleEl) titleEl.textContent = item.title || 'KPI Specificatie';
        if (subtitleEl) subtitleEl.textContent = item.sub || '';
        if (heroMainEl) heroMainEl.textContent = item.main || '';
        if (heroLabelEl) heroLabelEl.textContent = item.headline ? 'Overzicht' : 'Totaal';
        if (heroBadgeEl) {
            heroBadgeEl.innerHTML = item.main_extra ? `<span class="px-2 py-1 rounded bg-slate-800 text-slate-300 font-mono text-xs">${item.main_extra}</span>` : '';
        }
        if (explEl) explEl.textContent = item.explanation || item.sub || '';
        if (footerEl) footerEl.textContent = item.footer || '';

        if (listEl) {
            listEl.innerHTML = '';
            const items = item.breakdown || [];
            if (items.length === 0) {
                listEl.innerHTML = '<div class="p-3 text-slate-500 text-center font-mono">Geen specificaties beschikbaar.</div>';
            } else {
                const colorMap = {
                    blue: 'bg-blue-500',
                    pink: 'bg-pink-500',
                    indigo: 'bg-indigo-500',
                    amber: 'bg-amber-500',
                    cyan: 'bg-cyan-500',
                    emerald: 'bg-emerald-500'
                };
                items.forEach(b => {
                    const row = document.createElement('div');
                    row.className = 'pt-2.5 first:pt-0 pb-2.5 last:pb-0 flex items-start justify-between gap-3 text-xs';

                    const isNegative = (b.eur < 0 || b.kwh < 0);
                    const valColor = isNegative ? 'text-emerald-400' : 'text-slate-100';
                    const eurFormatted = (b.eur !== undefined && b.eur !== null)
                        ? (Math.abs(b.eur) < 0.005 ? '€0.00' : (b.eur < 0 ? `-€${Math.abs(b.eur).toFixed(2)}` : `€${b.eur.toFixed(2)}`))
                        : '';
                    const kwhFormatted = (b.kwh !== undefined && b.kwh !== null)
                        ? (Math.abs(b.kwh) < 0.05 ? '0.0 kWh' : `${b.kwh.toFixed(1)} kWh`)
                        : '';

                    const dotClass = colorMap[b.icon] || 'bg-slate-400';
                    row.innerHTML = `
                        <div class="flex items-start gap-2.5">
                            <span class="w-2 h-2 rounded-full ${dotClass} inline-block mt-1.5 flex-shrink-0 shadow-sm"></span>
                            <div>
                                <div class="font-bold text-white leading-snug">${b.label}</div>
                                <div class="text-[11px] text-slate-400 leading-tight mt-0.5">${b.desc || ''}</div>
                            </div>
                        </div>
                        <div class="text-right font-mono flex-shrink-0">
                            <div class="font-bold ${valColor}">${eurFormatted}</div>
                            <div class="text-[10px] text-slate-500">${kwhFormatted}</div>
                        </div>
                    `;
                    listEl.appendChild(row);
                });
            }
        }

        modal.classList.remove('hidden');
    }

    function closeKpiDetailModal() {
        const modal = document.getElementById('kpi-detail-modal');
        if (modal) modal.classList.add('hidden');
    }

    function handleKpiModalBackdrop(event) {
        if (event.target && event.target.id === 'kpi-detail-modal') {
            closeKpiDetailModal();
        }
    }

    window.updateCurrentForecastKpis = updateCurrentForecastKpis;
    window.updateCurrentHistoryKpis = updateCurrentHistoryKpis;
    window.openKpiDetailModal = openKpiDetailModal;
    window.closeKpiDetailModal = closeKpiDetailModal;
    window.handleKpiModalBackdrop = handleKpiModalBackdrop;

})(window);
