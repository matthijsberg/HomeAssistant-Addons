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

        const icons = { costs: '💶', solar: '☀️', savings: '💡', heatpump: '⚡' };
        if (iconEl) iconEl.textContent = icons[kpiType] || '📊';
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
                items.forEach(b => {
                    const row = document.createElement('div');
                    row.className = 'pt-2.5 first:pt-0 pb-2.5 last:pb-0 flex items-start justify-between gap-3 text-xs';

                    const isNegative = (b.eur < 0 || b.kwh < 0);
                    const valColor = isNegative ? 'text-emerald-400' : 'text-slate-100';
                    const eurFormatted = (b.eur !== undefined && b.eur !== null)
                        ? (b.eur < 0 ? `-€${Math.abs(b.eur).toFixed(2)}` : `€${b.eur.toFixed(2)}`)
                        : '';
                    const kwhFormatted = (b.kwh !== undefined && b.kwh !== null)
                        ? (b.kwh < 0 ? `${b.kwh.toFixed(1)} kWh` : `${b.kwh.toFixed(1)} kWh`)
                        : '';

                    row.innerHTML = `
                        <div class="flex items-start gap-2.5">
                            <span class="text-base select-none mt-0.5">${b.icon || '▪️'}</span>
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
