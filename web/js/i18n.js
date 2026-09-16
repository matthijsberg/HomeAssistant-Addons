/**
 * Open HEMS Client-side Internationalization (i18n) Engine
 * =======================================================
 * Manages locale loading, localStorage persistence, and DOM updates.
 */

window.OpenHEMSi18n = (function() {
    let currentLang = localStorage.getItem('open_hems_lang') || 'nl';
    let translations = {};

    async function init() {
        await loadLanguage(currentLang);
        renderLanguageSelector();
    }

    async function loadLanguage(lang) {
        try {
            const res = await fetch(`./locales/${lang}.json?v=${Date.now()}`);
            if (res.ok) {
                translations = await res.json();
                currentLang = lang;
                localStorage.setItem('open_hems_lang', lang);
                applyTranslationsToDOM();
                // Dispatch custom event so charts and views can re-render
                window.dispatchEvent(new CustomEvent('openhems:languageChanged', { detail: { lang: lang } }));
            }
        } catch (e) {
            console.warn('[i18n] Failed loading language:', lang, e);
        }
    }

    function t(key, defaultVal = '') {
        const parts = key.split('.');
        let val = translations;
        for (const p of parts) {
            if (val && typeof val === 'object' && p in val) {
                val = val[p];
            } else {
                return defaultVal || key;
            }
        }
        return val || defaultVal || key;
    }

    function getLang() {
        return currentLang;
    }

    function applyTranslationsToDOM() {
        document.querySelectorAll('[data-i18n]').forEach(el => {
            const key = el.getAttribute('data-i18n');
            const translated = t(key);
            if (translated && translated !== key) {
                if (el.tagName === 'INPUT' && el.getAttribute('placeholder')) {
                    el.setAttribute('placeholder', translated);
                } else {
                    el.innerHTML = translated;
                }
            }
        });
    }

    function renderLanguageSelector() {
        const container = document.getElementById('header-lang-selector');
        if (!container) return;

        const isNl = (currentLang === 'nl');
        container.innerHTML = `
            <div class="inline-flex items-center rounded-lg bg-slate-900/90 p-0.5 border border-slate-800 text-[11px] font-bold">
                <button type="button" onclick="OpenHEMSi18n.switchLang('nl')" class="px-2 py-1 rounded-md transition ${isNl ? 'bg-cyan-500/20 text-cyan-400 border border-cyan-500/40 shadow-sm' : 'text-slate-400 hover:text-slate-200'}" title="Nederlands">
                    🇳🇱 NL
                </button>
                <button type="button" onclick="OpenHEMSi18n.switchLang('en')" class="px-2 py-1 rounded-md transition ${!isNl ? 'bg-cyan-500/20 text-cyan-400 border border-cyan-500/40 shadow-sm' : 'text-slate-400 hover:text-slate-200'}" title="English">
                    🇬🇧 EN
                </button>
            </div>
        `;
    }

    async function switchLang(lang) {
        if (lang === currentLang) return;
        await loadLanguage(lang);
        renderLanguageSelector();
        // Refresh active views
        if (typeof window.refreshAllData === 'function') {
            window.refreshAllData();
        }
    }

    return {
        init,
        t,
        getLang,
        switchLang,
        loadLanguage
    };
})();

// Auto-initialize when DOM ready
document.addEventListener('DOMContentLoaded', () => {
    window.OpenHEMSi18n.init();
});
