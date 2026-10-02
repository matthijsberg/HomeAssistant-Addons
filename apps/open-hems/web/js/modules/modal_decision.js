/**
 * Open HEMS - Decision Audit Detail Modal
 * Handles presentation and inspection of logged audit decisions and grouped events.
 */
(function(window) {
    'use strict';

    function openDecisionGroupModal(gIdx) {
        const g = (window.__foldedGroups || [])[gIdx];
        if (!g) return;
        const d = g.representative;
        const count = g.entries.length;
        const newest = g.entries[0];
        const oldest = g.entries[g.entries.length - 1];

        const isError = (d.category === 'ERROR' || d.chosen_mode === 'error');
        const isWarning = (d.category === 'WARNING' || d.chosen_mode === 'warning');
        const isAction = (d.category === 'ACTION' || d.decision_type === 'live_actuation' || d.domain === 'hardware');
        const typeBadge = document.getElementById('modal-type-badge');
        if (typeBadge) {
            if (isError) {
                typeBadge.className = 'px-2 py-0.5 rounded text-[10px] font-mono font-bold bg-red-950/80 border border-red-500/50 text-red-300';
                typeBadge.innerText = '❌ SYSTEEMFOUT';
            } else if (isWarning) {
                typeBadge.className = 'px-2 py-0.5 rounded text-[10px] font-mono font-bold bg-amber-950/80 border border-amber-500/50 text-amber-300';
                typeBadge.innerText = '⚠️ WAARSCHUWING';
            } else if (isAction) {
                typeBadge.className = 'px-2 py-0.5 rounded text-[10px] font-mono font-bold bg-blue-950/80 border border-blue-500/50 text-blue-300';
                typeBadge.innerText = '⚡ FYSIEKE ACTIE';
            } else {
                typeBadge.className = 'px-2 py-0.5 rounded text-[10px] font-mono font-bold bg-purple-950/80 border border-purple-500/50 text-purple-300';
                typeBadge.innerText = '🧠 PLAN-BESLUIT';
            }
        }

        const titleEl = document.getElementById('modal-title');
        if (titleEl) titleEl.innerText = d.reason + (count > 1 ? ` (${count}× geëvalueerd)` : '');
        
        const dtNew = new Date(newest.timestamp_iso);
        const dtOld = new Date(oldest.timestamp_iso);
        const timeNew = dtNew.toLocaleTimeString('nl-NL', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
        const timeOld = dtOld.toLocaleTimeString('nl-NL', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
        const dateStr = dtNew.toLocaleDateString('nl-NL', { day: '2-digit', month: '2-digit', year: 'numeric' });
        const timeEl = document.getElementById('modal-time');
        if (timeEl) {
            timeEl.innerText = (count > 1) 
                ? `${dateStr} ${timeOld} ➔ ${timeNew} (${Math.max(1, Math.round((dtNew - dtOld)/60000))} min)`
                : `${dateStr} ${timeNew}`;
        }

        const modeEl = document.getElementById('modal-mode');
        if (modeEl) {
            modeEl.className = 'px-2 py-0.5 rounded font-bold border ' + (
                (isError) ? 'bg-red-950/80 border-red-500/50 text-red-300' :
                (isWarning) ? 'bg-amber-950/80 border-amber-500/50 text-amber-300' :
                (d.chosen_mode === 'max_on') ? 'bg-purple-950/80 border-purple-500/50 text-purple-300' :
                (d.chosen_mode === 'forced_on') ? 'bg-emerald-950/80 border-emerald-500/50 text-emerald-300' :
                (d.chosen_mode === 'advised_on') ? 'bg-indigo-950/80 border-indigo-500/50 text-indigo-300' :
                (d.chosen_mode === 'forced_off') ? 'bg-red-950/80 border-red-500/50 text-red-300' :
                (d.chosen_mode === 'planned') ? 'bg-amber-950/80 border-amber-500/50 text-amber-300' :
                'bg-slate-800/80 border-slate-700 text-slate-300'
            );
            modeEl.innerText = d.chosen_mode;
        }

        const explEl = document.getElementById('modal-explanation');
        if (explEl) explEl.innerText = d.explanation;

        // Inputs table
        const inputsTable = document.getElementById('modal-inputs-table');
        if (inputsTable) {
            const combinedInputs = Object.assign({}, ...g.entries.map(e => e.inputs || {}));
            const entries = Object.entries(combinedInputs).filter(([_, v]) => v !== null && v !== undefined);
            if (entries.length > 0) {
                inputsTable.innerHTML = entries.map(([k, v]) => `
                    <div class="flex items-center justify-between p-2.5 hover:bg-slate-900/50">
                        <span class="text-slate-400">${k}</span>
                        <span class="text-white font-bold">${typeof v === 'boolean' ? (v ? 'WAAR (AAN)' : 'ONWAAR (UIT)') : v}</span>
                    </div>
                `).join('');
            } else {
                inputsTable.innerHTML = '<div class="p-3 text-slate-500 text-center">Geen aanvullende sensor-inputs geregistreerd.</div>';
            }
        }

        // Savings
        const savBox = document.getElementById('modal-savings-box');
        const savVal = document.getElementById('modal-savings-val');
        if (savBox && savVal) {
            if (d.savings_estimate_eur > 0) {
                savBox.classList.remove('hidden');
                savVal.innerText = `+€${Number(d.savings_estimate_eur).toFixed(2)}`;
            } else {
                savBox.classList.add('hidden');
            }
        }

        const modal = document.getElementById('decision-detail-modal');
        if (modal) modal.classList.remove('hidden');
    }

    function closeDecisionModal() {
        const modal = document.getElementById('decision-detail-modal');
        if (modal) modal.classList.add('hidden');
    }

    function handleDecisionModalBackdrop(event) {
        if (event.target && event.target.id === 'decision-detail-modal') {
            closeDecisionModal();
        }
    }

    window.openDecisionGroupModal = openDecisionGroupModal;
    window.closeDecisionModal = closeDecisionModal;
    window.handleDecisionModalBackdrop = handleDecisionModalBackdrop;

})(window);
