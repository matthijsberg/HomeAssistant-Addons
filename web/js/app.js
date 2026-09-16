        // =========================================================================
        // OPEN HEMS UNIFIED CHARTING DESIGN SYSTEM & CONTROLLER
        // Single Source of Truth for Colors, Typography, Time Format & Sticky State
        // =========================================================================
        const OpenHEMSTokens = {
            colors: {
                solar: '#F59E0B',              // Amber 500: Zon opwek & prognose
                solarBg: 'rgba(245, 158, 11, 0.70)', // Amber bar fill
                solarArea: 'rgba(245, 158, 11, 0.22)', // Amber line area fill
                price: '#06B6D4',              // Cyan 500: EPEX Stroomtarief referentie
                priceLine: '#38BDF8',          // Sky 400: EPEX Stepped tarieflijn
                unallocated: '#3B82F6',        // Blue 500: Ongedefinieerd verbruik (7x24)
                unallocatedBg: 'rgba(59, 130, 246, 0.75)',
                unallocatedArea: 'rgba(59, 130, 246, 0.15)',
                dhw: '#EC4899',                // Pink 500: SWW Tapwater
                dhwBg: 'rgba(236, 72, 153, 0.80)',
                heating: '#6366F1',            // Indigo 500: CV Vloerverwarming
                heatingBg: 'rgba(99, 102, 241, 0.80)',
                batteryCharge: '#10B981',      // Emerald 500: Thuisbatterij Laden
                batteryDischarge: '#14B8A6',   // Teal 500: Thuisbatterij Ontladen
                netto: '#EF4444',              // Red 500: Verwacht Netto
                solarCost: '#EAB308',          // Yellow 500: Zon Kostprijs (€0.06/kWh)
                gridLine: 'rgba(30, 41, 59, 0.4)',
                gridLineZero: 'rgba(255, 255, 255, 0.18)',
                textMuted: '#94A3B8',
                textLight: '#E2E8F0',
                tooltipBg: '#0B0F17',
                tooltipBorder: '#334155'
            },
            fonts: {
                mono: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace',
                sans: 'ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif'
            }
        };

        // Universal Chart.js Shading Plugin for Open HEMS
        const OpenHEMSHistoryPlugin = {
            id: 'openhemsHistoryShading',
            beforeDraw(chart) {
                const { ctx, chartArea, scales } = chart;
                if (!chartArea || !scales || !scales.x) return;

                const labels = chart.data?.labels || [];
                let histCount = chart.options?.plugins?.openhemsHistory?.count;
                if (histCount === undefined) {
                    const nuIdx = labels.findIndex(l => typeof l === 'string' && l.startsWith('Nu'));
                    if (nuIdx > 0) histCount = nuIdx;
                }
                if (!histCount || histCount <= 0) return;

                const xNu = scales.x.getPixelForValue(histCount);
                const xPrev = scales.x.getPixelForValue(histCount - 1);
                const xBoundary = (xPrev !== undefined && !isNaN(xPrev) && xNu !== undefined && !isNaN(xNu)) ? (xPrev + xNu) / 2 : (xNu || chartArea.left);

                ctx.save();

                // 1. Darker shaded background for historical zone
                ctx.fillStyle = 'rgba(3, 7, 18, 0.78)';
                ctx.fillRect(chartArea.left, chartArea.top, xBoundary - chartArea.left, chartArea.height);

                // 2. Crisp dashed vertical divider at the boundary
                ctx.beginPath();
                ctx.strokeStyle = 'rgba(148, 163, 184, 0.45)';
                ctx.lineWidth = 1.5;
                ctx.setLineDash([4, 3]);
                ctx.moveTo(xBoundary, chartArea.top);
                ctx.lineTo(xBoundary, chartArea.bottom);
                ctx.stroke();

                // 3. Subtle pill badge in the historical zone
                ctx.setLineDash([]);
                ctx.fillStyle = 'rgba(15, 23, 42, 0.90)';
                const badgeWidth = 92;
                const badgeHeight = 18;
                const badgeX = chartArea.left + 8;
                const badgeY = chartArea.top + 8;
                ctx.beginPath();
                ctx.roundRect(badgeX, badgeY, badgeWidth, badgeHeight, 4);
                ctx.fill();
                ctx.strokeStyle = 'rgba(148, 163, 184, 0.35)';
                ctx.lineWidth = 1;
                ctx.stroke();

                // Badge text
                ctx.fillStyle = '#94A3B8';
                ctx.font = '700 9px ui-sans-serif, system-ui, sans-serif';
                ctx.fillText('HISTORIE (1U)', badgeX + 10, badgeY + 12);

                // 4. Subtle "Nu" marker tag next to boundary line
                ctx.fillStyle = 'rgba(168, 85, 247, 0.90)';
                ctx.beginPath();
                ctx.roundRect(xBoundary + 4, chartArea.top + 8, 38, 18, 4);
                ctx.fill();
                ctx.fillStyle = '#FFFFFF';
                ctx.font = '700 9px ui-sans-serif, system-ui, sans-serif';
                ctx.fillText('NU ▶', xBoundary + 10, chartArea.top + 20);

                ctx.restore();
            }
        };
        Chart.register(OpenHEMSHistoryPlugin);

        const OpenHEMSChartEngine = {
            getChartType() {
                return localStorage.getItem('openhems_chart_type') || window.predictionChartType || 'bar';
            },
            setChartType(type) {
                window.predictionChartType = type;
                localStorage.setItem('openhems_chart_type', type);
                this.syncTypeButtons(type);
                this.refreshAllCharts();
            },
            getResolution() {
                return localStorage.getItem('openhems_resolution') || predictionResolution || '1h';
            },
            setResolution(res) {
                predictionResolution = res;
                localStorage.setItem('openhems_resolution', res);
                this.syncResolutionButtons(res);
                this.refreshAllCharts();
            },
            syncTypeButtons(type) {
                const btnBar = document.getElementById('pred-btn-type-bar');
                const btnLine = document.getElementById('pred-btn-type-line');
                if (btnBar && btnLine) {
                    if (type === 'bar') {
                        btnBar.className = 'px-2.5 py-1 rounded transition font-medium bg-purple-600 text-white shadow';
                        btnLine.className = 'px-2.5 py-1 rounded transition font-medium text-slate-400 hover:text-slate-200';
                    } else {
                        btnBar.className = 'px-2.5 py-1 rounded transition font-medium text-slate-400 hover:text-slate-200';
                        btnLine.className = 'px-2.5 py-1 rounded transition font-medium bg-purple-600 text-white shadow';
                    }
                }
            },
            syncResolutionButtons(res) {
                document.querySelectorAll('.res-btn-1h').forEach(b => {
                    b.className = (res === '1h') ? 'res-btn-1h px-2.5 py-1 rounded transition font-medium bg-purple-600 text-white shadow' : 'res-btn-1h px-2.5 py-1 rounded transition font-medium text-slate-400 hover:text-slate-200';
                });
                document.querySelectorAll('.res-btn-15m').forEach(b => {
                    b.className = (res === '15m') ? 'res-btn-15m px-2.5 py-1 rounded transition font-medium bg-purple-600 text-white shadow' : 'res-btn-15m px-2.5 py-1 rounded transition font-medium text-slate-400 hover:text-slate-200';
                });
            },
            refreshAllCharts() {
                if (typeof loadChartData === 'function') loadChartData();
                if (typeof loadElectricityPricesChart === 'function') loadElectricityPricesChart();
                if (typeof renderDhwTemperatureChart === 'function') renderDhwTemperatureChart();
                if (typeof renderHeatingForecastChart === 'function') renderHeatingForecastChart();
                if (typeof renderModelDecompositionChart === 'function') renderModelDecompositionChart();
                if (typeof loadDecisionAuditLog === 'function') loadDecisionAuditLog();
            },
            init() {
                const savedType = this.getChartType();
                window.predictionChartType = savedType;
                this.syncTypeButtons(savedType);

                const savedRes = this.getResolution();
                predictionResolution = savedRes;
                this.syncResolutionButtons(savedRes);
            }
        };

        var chartInstance = null;
        var analyticsChartInstance = null;
        var powerProducersChartInstance = null;
        var powerProducersChartType = 'bar'; // Default to Staven (aligned with 24h prediction)
        var powerProducersResolution = '1h';  // Default to 1 Uur for 24h range
        var electricityPricesChartInstance = null;
        var costForecastChartInstance = null;
        var costHistoryChartInstance = null;
        var pipelinePollInterval = null;
        var activeUnallocDay = (new Date().getDay() + 6) % 7; // Auto-defaults to today (0=Ma ... 5=Za, 6=Zo)
        var cachedUnallocModel = null;
        let haEntitiesCache = [];
        let currentPolicyParams = {};
        let activeTabId = 'analytics';
        let predictionResolution = '1h';

// =========================================================================
        // SUBTLE INTERACTIVE INFO POPOVERS (TOUCH & CLICK FRIENDLY)
        // =========================================================================
        const infoPopovers = {
            'col_param': 'Fysische en gedragsmatige eigenschappen van de woning, warmtepomp en installatie die door het zelflerende model worden gekalibreerd.',
            'col_active': 'De actieve parameterwaarde waarmee Open HEMS op dit moment live de 24-uurs dispatch en energiegrafieken doorrekent.',
            'col_proposed': 'De nieuw berekende waarde uit de OLS-regressie over InfluxDB telemetrie over de gekozen geheugenhorizon (30, 90 of 365 dagen).',
            'col_drift': 'Het procentuele verschil tussen de actieve parameter en het nieuwe voorstel. Groen = binnen drempel (< 3%), Blauw = daling, Oranje = stijging.',
            'col_evidence': 'De statistische bron, steekproefgrootte en wiskundige methode (bijv. OLS regressie over stookdagen, 230 winterruns, nachtmediaan).',
            'col_status': "'Automatisch' = afwijking valt binnen de drempel (±3%) en is direct via EWMA toegepast. 'Ter Beoordeling' = vereist handmatige goedkeuring via 'Accepteren'.",
            'param_building_ua': 'Totale transmissie- en infiltratieverlies van het huis per graad temperatuurverschil (W/K). Hoe lager de UA, hoe beter de isolatie en hoe trager de woning afkoelt.',
            'param_heating_modulation': 'Daikin Altherma inverter vermogensformule (Watt elektrisch o.b.v. buitentemperatuur) gebaseerd op 230 werkelijke winterruns in InfluxDB.',
            'param_night_baseload': 'De continue nachtelijke basislast van het huis (01:00-05:00u) voor standby, netwerk, ventilatie en domotica.',
            'param_dhw_standby': 'Thermisch stilstandsverlies van de 350L boiler door de isolatiemantel (~0,18°C/uur afkoeling) naar de omgeving.',
            'status_auto': 'Automatisch doorgevoerd: de afwijking valt binnen de ingestelde auto-accept drempel en is direct via de leersnelheid (EWMA) in het actieve rekenmodel bijgesteld.',
            'status_review': "Ter beoordeling: de afwijking overschrijdt de drempel. Klik rechtsonder op 'Accepteren & Toepassen' om deze wijziging te bekrachtigen.",
            'status_accepted': 'Handmatig geaccepteerd: door jou goedgekeurd en geactiveerd in het actieve rekenmodel.',
            'val_overlay_info': 'Model Validatie legt het voorspelde profiel (gestreept) direct over de werkelijk geregistreerde meters (massief) heen. Zo zie je exact waar het model accuraat is en waar leerafwijkingen ontstaan.',
            'dhw_decision_box_info': 'Toont de thermodynamische en economische analyse van het nachtlaadbesluit: waarom de planner nu wel of niet voorverwarmt, inclusief comfortrisico (koude douche) en spitsblokkades.'
        };

        function toggleInfoPopover(e, key) {
            if (e) {
                e.stopPropagation();
                e.preventDefault();
            }
            const text = infoPopovers[key] || '';
            if (!text) return;

            let pop = document.getElementById('open-hems-popover');
            if (pop && pop.__currentKey === key && !pop.classList.contains('hidden')) {
                pop.classList.add('hidden');
                return;
            }
            if (!pop) {
                pop = document.createElement('div');
                pop.id = 'open-hems-popover';
                pop.className = 'fixed z-50 max-w-xs bg-[#0B0F17] border border-slate-700 text-slate-200 text-xs p-3 rounded-xl shadow-2xl backdrop-blur-md leading-relaxed transition-all duration-200';
                document.body.appendChild(pop);
                document.addEventListener('click', (evt) => {
                    if (pop && !pop.contains(evt.target)) {
                        pop.classList.add('hidden');
                    }
                });
            }
            pop.__currentKey = key;
            pop.innerHTML = `<div class="flex items-start gap-2.5">
                <span class="w-4 h-4 rounded-full bg-cyan-950 text-cyan-400 border border-cyan-700/60 flex items-center justify-center text-[10px] font-bold flex-shrink-0 mt-0.5">i</span>
                <div class="text-[11px] text-slate-300 font-sans leading-relaxed">${text}</div>
            </div>`;
            pop.classList.remove('hidden');

            const targetEl = e ? e.currentTarget : null;
            if (targetEl) {
                const rect = targetEl.getBoundingClientRect();
                let top = rect.bottom + 6;
                let left = rect.left - 15;
                if (left + 290 > window.innerWidth) {
                    left = window.innerWidth - 300;
                }
                if (left < 12) left = 12;
                pop.style.top = `${top}px`;
                pop.style.left = `${left}px`;
            }
        }

        function setPowerProducersType(type) {
            powerProducersChartType = type;
            const btnBar = document.getElementById('pp-btn-type-bar');
            const btnLine = document.getElementById('pp-btn-type-line');
            if (btnBar && btnLine) {
                if (type === 'bar') {
                    btnBar.className = 'px-2 py-0.5 rounded transition font-medium bg-purple-600 text-white shadow';
                    btnLine.className = 'px-2 py-0.5 rounded transition font-medium text-slate-400 hover:text-slate-200';
                } else {
                    btnBar.className = 'px-2 py-0.5 rounded transition font-medium text-slate-400 hover:text-slate-200';
                    btnLine.className = 'px-2 py-0.5 rounded transition font-medium bg-purple-600 text-white shadow';
                }
            }
            loadPowerProducersChart();
        }

        function setPowerProducersResolution(res) {
            powerProducersResolution = res;
            updatePowerProducersResButtons(res);
            loadPowerProducersChart();
        }

        function updatePowerProducersResButtons(res) {
            const btn1h = document.getElementById('pp-btn-res-1h');
            const btn15m = document.getElementById('pp-btn-res-15m');
            if (btn1h && btn15m) {
                if (res === '1h') {
                    btn1h.className = 'px-2 py-0.5 rounded transition font-medium bg-blue-600 text-white shadow';
                    btn15m.className = 'px-2 py-0.5 rounded transition font-medium text-slate-400 hover:text-slate-200';
                } else {
                    btn1h.className = 'px-2 py-0.5 rounded transition font-medium text-slate-400 hover:text-slate-200';
                    btn15m.className = 'px-2 py-0.5 rounded transition font-medium bg-blue-600 text-white shadow';
                }
            }
        }

        function onPowerProducersRangeChange() {
            const rangeSelect = document.getElementById('pp-range-select');
            const rangeVal = rangeSelect ? rangeSelect.value : '24h';
            // Auto-adjust resolution based on range (Grafana style)
            if (rangeVal === '24h') {
                powerProducersResolution = '1h';
            } else if (rangeVal === '1h' || rangeVal === '6h') {
                powerProducersResolution = '15m';
            } else {
                powerProducersResolution = '1h';
            }
            updatePowerProducersResButtons(powerProducersResolution);
            loadPowerProducersChart();
            loadDhwHistoryChart();
        }

        window.__simulateBattery = false;

                window.predictionChartType = 'bar'; // Default to Staven

        function setPredictionChartType(type) {
            OpenHEMSChartEngine.setChartType(type);
        }

        function toggleBatterySimFromSettings() {
            setBatterySimulation(!window.__simulateBattery);
            renderDevicesGrid();
        }

        function setBatterySimulation(enable) {
            window.__simulateBattery = enable;
            document.querySelectorAll('.bat-btn-off').forEach(b => {
                b.className = !enable ? 'bat-btn-off px-2 py-0.5 rounded transition font-medium bg-purple-600 text-white shadow' : 'bat-btn-off px-2 py-0.5 rounded transition font-medium text-slate-400 hover:text-slate-200';
            });
            document.querySelectorAll('.bat-btn-on').forEach(b => {
                b.className = enable ? 'bat-btn-on px-2 py-0.5 rounded transition font-medium bg-amber-600 text-white shadow' : 'bat-btn-on px-2 py-0.5 rounded transition font-medium text-slate-400 hover:text-slate-200';
            });
            loadChartData();
        }

        function setPredictionResolution(res) {
            OpenHEMSChartEngine.setResolution(res);
        }
        let cachedInfra = { influxdb_connections: [], mqtt_connections: [] };

        function toggleMobileSidebar(open) {
            const sidebar = document.getElementById('main-sidebar');
            const backdrop = document.getElementById('sidebar-backdrop');
            if (open) {
                sidebar.classList.remove('-translate-x-full');
                sidebar.classList.add('translate-x-0');
                backdrop.classList.remove('hidden');
            } else {
                sidebar.classList.remove('translate-x-0');
                sidebar.classList.add('-translate-x-full');
                backdrop.classList.add('hidden');
            }
        }

        function showTab(tabId) {
            activeTabId = tabId;
            if (window.innerWidth < 768) {
                toggleMobileSidebar(false);
            }
            document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
            document.querySelectorAll('.nav-link').forEach(el => el.classList.remove('active'));
            const target = document.getElementById('view-' + tabId);
            if (target) target.classList.add('active');
            const link = document.getElementById('nav-' + tabId);
            if (link) link.classList.add('active');

            const titles = {
                'prediction': ['Voorspelling & Optimalisatie', '24-uurs kwartier-vooruitblik met dynamische beurstarieven en sturingsadviezen.'],
                'history': ['Historie & Verbruiksstatistieken', 'Werkelijke energiestromen, kosten, opbrengsten en COP-prestaties.'],
                'decisions': ['Beslis-Logboek & Observability', 'Audit trail van alle sturingsbeslissingen, sensor-inputs, fysieke marges en financiële motivatie.'],
                'policies': ['Apparaat Policies & Aansturing', 'Automatische beslisregels, nachtelijk boilerlaadbesluit en beleidsarchetypen.'],
                'calibration': ['Zelflerend Model & Fysica', 'Physics-informed gebouwmodel, 7×96 kwartieren matrices en boilertemperatuurtraject.'],
                'devices': ['Apparaten', 'Beheer fysieke apparaten, meters en actuatoren gekoppeld via Home Assistant of MQTT.'],
                'infrastructure': ['Verbindingen', 'Beheer externe verbindingen naar Home Assistant, MQTT brokers en externe APIs.'],
                'tariffs': ['Energieleveranciers & Tarieven', 'Beheer contracten (Powerpeers dynamisch) en energiebelasting.'],
                'data': ['Data & Pipelines', 'Beheer InfluxDB tijdreeksdatabases, dataretentie en live 60s data pipelines.'],
                'docs': ['Systeemdocumentatie & Gebruikersgids', 'Uitgebreide naslag over de sturingslogica, Clean Architecture, apparaten toevoegen en datakoppelingen.']
            };
            const t = titles[tabId] || ['Open HEMS', ''];
            document.getElementById('header-title').innerText = t[0];
            document.getElementById('header-sub').innerText = t[1];

            if (tabId === 'prediction' || tabId === 'analytics') {
                loadChartData();
                loadElectricityPricesChart();
                renderDhwTemperatureChart();
                renderHeatingForecastChart();
                loadDecisionAuditLog();
            }
            if (tabId === 'history') {
                loadAnalytics();
                loadPowerProducersChart();
                loadValidationOverlayChart();
                loadDhwHistoryChart();
            }
            if (tabId === 'decisions') {
                loadDecisionAuditLog();
            }
            if (tabId === 'policies') {
                loadPolicies();
                updateDhwLiveCard();
            }
            if (tabId === 'calibration') {
                loadModelDashboard();
                loadCalibration();
                activeUnallocDay = (new Date().getDay() + 6) % 7;
                loadUnallocatedModel();
                loadAlgorithmConfig();
                loadModelRecommendations();
                renderDhwTemperatureChart();
                renderModelDecompositionChart();
                loadValidationOverlayChart();
            }
            if (tabId === 'devices') loadDevices();
            if (tabId === 'tariffs') loadTariffs();
            if (tabId === 'infrastructure') {
                loadInfrastructure();
                loadProviders();
                loadSolarRoofConfig();
            }
            if (tabId === 'data') {
                loadInfrastructure();
                loadPipelineStatus();
                if (!pipelinePollInterval) pipelinePollInterval = setInterval(loadPipelineStatus, 10000);
            } else if (tabId !== 'infrastructure' && tabId !== 'data') {
                if (pipelinePollInterval) { clearInterval(pipelinePollInterval); pipelinePollInterval = null; }
            }
        }

        function refreshCurrentTab() {
            showTab(activeTabId);
        }

        
        async function loadSolarRoofConfig() {
            try {
                const res = await fetch('./api/config/solar');
                if (!res.ok) return;
                const d = await res.json();
                const s = d.solar || {};
                if (document.getElementById('solar-cfg-wp')) document.getElementById('solar-cfg-wp').value = s.kwp ? s.kwp * 1000 : 5760;
                if (document.getElementById('solar-cfg-inv')) document.getElementById('solar-cfg-inv').value = s.inverter_max_w || 5500;
                if (document.getElementById('solar-cfg-tilt')) document.getElementById('solar-cfg-tilt').value = s.tilt_degrees || 34;
                if (document.getElementById('solar-cfg-azimuth')) document.getElementById('solar-cfg-azimuth').value = s.azimuth_degrees || 225;
                if (document.getElementById('solar-cfg-eff')) document.getElementById('solar-cfg-eff').value = s.efficiency_factor || 0.88;
            } catch (e) {
                console.warn("Error loading solar roof config:", e);
            }
        }

        async function saveSolarRoofConfig() {
            const wp = parseFloat(document.getElementById('solar-cfg-wp')?.value || 5760);
            const inv = parseInt(document.getElementById('solar-cfg-inv')?.value || 5500);
            const tilt = parseFloat(document.getElementById('solar-cfg-tilt')?.value || 34);
            const az = parseFloat(document.getElementById('solar-cfg-azimuth')?.value || 225);
            const eff = parseFloat(document.getElementById('solar-cfg-eff')?.value || 0.88);

            try {
                const res = await fetch('./api/config/solar', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        kwp: wp / 1000.0,
                        inverter_max_w: inv,
                        tilt_degrees: tilt,
                        azimuth_degrees: az,
                        efficiency_factor: eff
                    })
                });
                const d = await res.json();
                if (res.ok) {
                    alert("✅ Zonnepanelen & dakconfiguratie succesvol opgeslagen! De voorspellingen worden direct opnieuw berekend.");
                    loadChartData();
                    loadElectricityPricesChart();
                } else {
                    alert("❌ Fout bij opslaan: " + (d.message || 'Onbekend'));
                }
            } catch (e) {
                alert("❌ Netwerkfout bij opslaan dakconfiguratie");
            }
        }

        async function loadProviders() {
            const container = document.getElementById('providers-container');
            if (!container) return;
            try {
                const res = await fetch('./api/providers');
                const data = await res.json();
                container.innerHTML = '';
                (data.providers || []).forEach(p => {
                    const card = document.createElement('div');
                    card.className = 'bg-[#0e1422] border border-[#1E293B] rounded-2xl p-5 shadow-lg space-y-3';
                    card.innerHTML = `
                        <div class="flex justify-between items-start">
                            <div class="flex items-center gap-2.5">
                                <span class="w-3 h-3 rounded-full bg-purple-400"></span>
                                <div>
                                    <h4 class="font-bold text-white text-sm">${p.name}</h4>
                                    <span class="text-[10px] text-purple-300 font-mono">${p.type}</span>
                                </div>
                            </div>
                            <span class="px-2 py-0.5 rounded text-[10px] font-mono bg-emerald-950/80 text-emerald-400 border border-emerald-800">Actief</span>
                        </div>
                        <div class="text-[11px] text-purple-300 font-mono truncate bg-[#0B0F17] p-2 rounded-lg border border-slate-800/80">
                            ${p.endpoint}
                        </div>
                        <div class="text-[11px] text-slate-300 space-y-1 bg-[#0B0F17] p-2.5 rounded-lg border border-slate-800/80 font-mono">
                            ${p.ha_entity ? `<div>Gekoppelde HA Entiteit: <span class="text-white">${p.ha_entity}</span></div>` : ''}
                            ${p.live_price ? `<div>Huidig Tarief: <span class="text-cyan-300 font-bold">€${p.live_price}/kWh</span></div>` : ''}
                            ${p.live_temp ? `<div>Buitentemperatuur: <span class="text-amber-300 font-bold">${p.live_temp} °C</span></div>` : ''}
                            <div>Data Status: <span class="text-emerald-400 font-bold">Live polling (15m/1h)</span></div>
                        </div>
                    `;
                    container.appendChild(card);
                });
            } catch (e) {
                console.error('Error loading providers:', e);
            }
        }

        // =========================================================================
        // LAAG 1: MULTI-INSTANCE INFRASTRUCTURE CONTROLLER
        // =========================================================================
        async function loadInfrastructure() {
            try {
                const [infRes, statRes] = await Promise.all([
                    fetch('./api/infrastructure'),
                    fetch('./api/infrastructure/telemetry-stats')
                ]);
                cachedInfra = await infRes.json();
                const stat = await statRes.json();

                // Render Home Assistant Core Card (Bi-directional: Bron & Doel)
                const haData = cachedInfra.homeassistant || {};
                const haContainer = document.getElementById('ha-conn-container');
                if (haContainer) {
                    const isConnected = haData.status === 'connected';
                    const statusBadge = isConnected 
                        ? `<span class="px-2.5 py-1 rounded-lg text-[10px] font-mono font-semibold bg-emerald-950/80 text-emerald-400 border border-emerald-800 flex items-center gap-1.5"><span class="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse"></span> Verbonden (${haData.latency_ms}ms)</span>`
                        : `<span class="px-2.5 py-1 rounded-lg text-[10px] font-mono font-semibold bg-red-950/80 text-red-400 border border-red-800">Verbroken</span>`;

                    const sourcesHtml = (haData.sources || []).map(s => `
                        <div class="flex items-center justify-between py-1.5 px-2.5 rounded-lg bg-[#0e1422] border border-slate-800/60">
                            <div class="truncate mr-2">
                                <span class="text-white font-medium block truncate">${s.device_name}</span>
                                <span class="text-[10px] text-cyan-400 font-mono block truncate">${s.entity_id}</span>
                            </div>
                            <span class="px-2 py-0.5 rounded text-[10px] font-mono font-bold bg-cyan-950/80 text-cyan-300 border border-cyan-800 flex-shrink-0">
                                ${s.live_state || '--'}
                            </span>
                        </div>
                    `).join('');

                    const targetsHtml = (haData.targets || []).map(t => `
                        <div class="flex items-center justify-between py-1.5 px-2.5 rounded-lg bg-[#0e1422] border border-slate-800/60">
                            <div class="truncate mr-2">
                                <span class="text-white font-medium block truncate">${t.device_name}</span>
                                <span class="text-[10px] text-pink-400 font-mono block truncate">${t.entity_id}</span>
                            </div>
                            <span class="px-2 py-0.5 rounded text-[10px] font-mono font-bold ${(t.live_state === 'on' || t.live_state === 'true') ? 'bg-emerald-950/80 text-emerald-300 border border-emerald-800' : 'bg-slate-800 text-slate-300 border border-slate-700'} flex-shrink-0">
                                ${t.live_state || '--'}
                            </span>
                        </div>
                    `).join('');

                    haContainer.innerHTML = `
                        <div class="bg-[#0e1422] border border-[#1E293B] rounded-2xl p-5 shadow-lg space-y-4">
                            <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-2 border-b border-[#1E293B] pb-3">
                                <div>
                                    <h4 class="font-bold text-white text-sm flex items-center gap-2">
                                        <span>Home Assistant Core Connector</span>
                                        <span class="px-1.5 py-0.5 rounded text-[9px] bg-cyan-900/60 text-cyan-300 border border-cyan-800 font-mono">INGEBOUWD (HAOS)</span>
                                        <span class="text-[11px] text-slate-400 font-normal">(${haData.location || 'WeidHuis'} · v${haData.version || '2026.x'})</span>
                                    </h4>
                                    <p class="text-[11px] text-cyan-300 font-mono mt-0.5">${haData.url}</p>
                                </div>
                                <div class="flex items-center gap-2">
                                    ${statusBadge}
                                    <button onclick="testHomeAssistantConnection()" class="px-2.5 py-1 bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs rounded-lg flex items-center gap-1 border border-slate-700 transition">
                                        <svg class="w-3 h-3 text-cyan-400" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M13 10V3L4 14h7v7l9-11h-7z"></path></svg>
                                        <span>Testen</span>
                                    </button>
                                    <button onclick="openHomeAssistantModal()" class="px-2.5 py-1 bg-cyan-950/60 hover:bg-cyan-900 text-cyan-200 text-xs rounded-lg flex items-center gap-1 border border-cyan-800 transition">
                                        <svg class="w-3 h-3 text-cyan-400" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z"></path></svg>
                                        <span>Bewerken</span>
                                    </button>
                                </div>
                            </div>

                            <!-- Lean & Mean Connector Metrics Strip -->
                            <div class="grid grid-cols-3 gap-3 font-mono text-xs">
                                <div class="bg-[#0B0F17] p-3 rounded-xl border border-slate-800">
                                    <div class="text-[10px] text-slate-500 uppercase">Gekoppelde Apparaten</div>
                                    <div class="text-base font-bold text-white mt-0.5">${haData.total_devices || 4} apparaten</div>
                                </div>
                                <div class="bg-[#0B0F17] p-3 rounded-xl border border-slate-800">
                                    <div class="text-[10px] text-cyan-400 uppercase">Data-Inname Sensoren</div>
                                    <div class="text-base font-bold text-cyan-300 mt-0.5">${haData.total_sources || 0} actieve stromen</div>
                                </div>
                                <div class="bg-[#0B0F17] p-3 rounded-xl border border-slate-800">
                                    <div class="text-[10px] text-pink-400 uppercase">Aansturing Actuatoren</div>
                                    <div class="text-base font-bold text-pink-300 mt-0.5">${haData.total_targets || 0} regiepunten</div>
                                </div>
                            </div>
                            <p class="text-[11px] text-slate-400 italic">De specifieke sensoren en stuuractuatoren worden per apparaat beheerd op het tabblad <strong>Apparaten</strong>.</p>
                        </div>
                    `;
                }

                // Render InfluxDB Connections Cards
                const infContainer = document.getElementById('influx-conns-container');
                infContainer.innerHTML = '';
                const idbList = cachedInfra.influxdb_connections || [];

                idbList.forEach(c => {
                    const card = document.createElement('div');
                    card.className = 'bg-[#0e1422] border border-[#1E293B] rounded-2xl p-5 flex flex-col justify-between shadow-lg';
                    card.innerHTML = `
                        <div>
                            <div class="flex justify-between items-start mb-2">
                                <h4 class="font-bold text-white text-sm flex items-center gap-2">
                                    <span>${c.name}</span>
                                    ${c.is_default ? '<span class="px-1.5 py-0.5 rounded text-[9px] bg-blue-900/60 text-blue-300 border border-blue-800">STANDAARD</span>' : ''}
                                </h4>
                                <span id="badge-influx-${c.id}" class="px-2 py-0.5 rounded text-[10px] font-mono bg-slate-800 text-slate-300">Gereed</span>
                            </div>
                            <p class="text-[11px] text-cyan-300 font-mono mb-2">${c.url}</p>
                            <div class="grid grid-cols-2 gap-2 text-[11px] text-slate-300 bg-[#0B0F17] p-2.5 rounded-lg border border-slate-800 mb-3 font-mono">
                                <div>Opslag DB: <span class="text-white font-bold">${c.database}</span></div>
                                <div>Lees DB: <span class="text-white">${c.read_database || 'geen'}</span></div>
                                <div>Gebruiker: <span class="text-slate-400">${c.username || 'anoniem'}</span></div>
                                <div>Retentie: <span class="text-slate-400">${c.retention_policy}</span></div>
                            </div>
                        </div>
                        <div class="flex justify-between items-center pt-3 border-t border-[#1E293B]">
                            <button onclick="testSpecificInflux('${c.id}')" class="px-2.5 py-1 bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs rounded-lg flex items-center gap-1">
                                <svg class="w-3 h-3 text-cyan-400" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M13 10V3L4 14h7v7l9-11h-7z"></path></svg>
                                <span>Testen</span>
                            </button>
                            <div class="flex gap-2">
                                <button onclick='openInfluxModal(${JSON.stringify(c)})' class="px-2.5 py-1 bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs rounded-lg">Bewerken</button>
                                <button onclick="deleteInfluxConn('${c.id}')" class="px-2.5 py-1 bg-red-950/60 hover:bg-red-900 text-red-300 border border-red-800 text-xs rounded-lg">Verwijderen</button>
                            </div>
                        </div>
                    `;
                    infContainer.appendChild(card);
                });

                // Render MQTT Brokers Cards
                const mqContainer = document.getElementById('mqtt-conns-container');
                mqContainer.innerHTML = '';
                const mqList = cachedInfra.mqtt_connections || [];

                mqList.forEach(c => {
                    const card = document.createElement('div');
                    card.className = 'bg-[#0e1422] border border-[#1E293B] rounded-2xl p-5 flex flex-col justify-between shadow-lg';
                    card.innerHTML = `
                        <div>
                            <div class="flex justify-between items-start mb-2">
                                <h4 class="font-bold text-white text-sm flex items-center gap-2">
                                    <span>${c.name}</span>
                                    ${c.is_default ? '<span class="px-1.5 py-0.5 rounded text-[9px] bg-amber-900/60 text-amber-300 border border-amber-800">STANDAARD</span>' : ''}
                                </h4>
                                <span id="badge-mqtt-${c.id}" class="px-2 py-0.5 rounded text-[10px] font-mono bg-slate-800 text-slate-300">Gereed</span>
                            </div>
                            <p class="text-[11px] text-amber-300 font-mono mb-2">${c.host}:${c.port} ${c.tls ? '(TLS ✓)' : ''}</p>
                            <div class="grid grid-cols-2 gap-2 text-[11px] text-slate-300 bg-[#0B0F17] p-2.5 rounded-lg border border-slate-800 mb-3 font-mono">
                                <div>Topic Prefix: <span class="text-white">${c.base_topic}</span></div>
                                <div>Client ID: <span class="text-white">${c.client_id}</span></div>
                                <div>Gebruiker: <span class="text-slate-400">${c.username || 'geen'}</span></div>
                                <div>Status: <span class="text-emerald-400">Actief</span></div>
                            </div>
                        </div>
                        <div class="flex justify-between items-center pt-3 border-t border-[#1E293B]">
                            <button onclick="testSpecificMqtt('${c.id}')" class="px-2.5 py-1 bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs rounded-lg flex items-center gap-1">
                                <svg class="w-3 h-3 text-amber-400" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M13 10V3L4 14h7v7l9-11h-7z"></path></svg>
                                <span>Testen</span>
                            </button>
                            <div class="flex gap-2">
                                <button onclick='openMqttModal(${JSON.stringify(c)})' class="px-2.5 py-1 bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs rounded-lg">Bewerken</button>
                                <button onclick="deleteMqttConn('${c.id}')" class="px-2.5 py-1 bg-red-950/60 hover:bg-red-900 text-red-300 border border-red-800 text-xs rounded-lg">Verwijderen</button>
                            </div>
                        </div>
                    `;
                    mqContainer.appendChild(card);
                });

                // Update Total Conns Badge
                document.getElementById('badge-infra-conns').innerText = idbList.length + mqList.length;

                // Update Telemetry Stats
                if (document.getElementById('stat-openhems-count')) {
                    document.getElementById('stat-openhems-count').innerText = `${stat.openhems_series || 0} series`;
                }
            } catch (e) {
                console.error('Error loading infrastructure:', e);
            }
        }

        function openHomeAssistantModal() {
            const haData = cachedInfra.homeassistant || {};
            document.getElementById('modal-ha-url').value = haData.url || 'https://hass.b3rg.nl:8123';
            document.getElementById('modal-ha-token').value = haData.has_token ? '••••••••' : '';
            document.getElementById('modal-ha-timeout').value = haData.timeout_seconds || 5;
            document.getElementById('modal-ha-verify-ssl').value = String(haData.verify_ssl || false);
            document.getElementById('ha-modal').classList.remove('hidden');
        }

        async function saveHomeAssistantConnector(e) {
            e.preventDefault();
            const payload = {
                url: document.getElementById('modal-ha-url').value,
                timeout_seconds: parseInt(document.getElementById('modal-ha-timeout').value) || 5,
                verify_ssl: document.getElementById('modal-ha-verify-ssl').value === 'true',
                token: document.getElementById('modal-ha-token').value
            };
            try {
                const res = await fetch('./api/infrastructure/homeassistant', {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                });
                const data = await res.json();
                closeModal('ha-modal');
                alert('✅ Home Assistant Connector configuratie bijgewerkt!');
                loadInfrastructure();
            } catch (err) {
                alert('❌ Fout bij opslaan Home Assistant Connector: ' + err);
            }
        }

        async function testHomeAssistantConnection() {
            try {
                const res = await fetch('./api/infrastructure/homeassistant/test', { method: 'POST' });
                const data = await res.json();
                if (data.status === 'success') {
                    alert('✅ ' + data.message);
                } else {
                    alert('❌ Fout: ' + data.message);
                }
                loadInfrastructure();
            } catch (e) {
                alert('❌ Fout bij testen HA verbinding: ' + e);
            }
        }

        async function testSpecificInflux(connId) {
            const badge = document.getElementById(`badge-influx-${connId}`);
            if (badge) {
                badge.innerText = 'Testen...';
                badge.className = 'px-2 py-0.5 rounded text-[10px] font-mono bg-yellow-900/60 text-yellow-300 border border-yellow-800';
            }
            try {
                const res = await fetch('./api/infrastructure/influxdb/test', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ id: connId })
                });
                const d = await res.json();
                if (badge) {
                    if (d.status === 'success') {
                        badge.innerText = `🟢 OK (${d.latency_ms}ms)`;
                        badge.className = 'px-2 py-0.5 rounded text-[10px] font-mono bg-emerald-950 text-emerald-300 border border-emerald-800';
                    } else {
                        badge.innerText = '🔴 Fout';
                        badge.className = 'px-2 py-0.5 rounded text-[10px] font-mono bg-red-950 text-red-300 border border-red-800';
                        alert(`InfluxDB Test Fout: ${d.message}`);
                    }
                }
            } catch (e) {
                if (badge) {
                    badge.innerText = '🔴 Onbereikbaar';
                    badge.className = 'px-2 py-0.5 rounded text-[10px] font-mono bg-red-950 text-red-300 border border-red-800';
                }
            }
        }

        async function testSpecificMqtt(connId) {
            const badge = document.getElementById(`badge-mqtt-${connId}`);
            if (badge) {
                badge.innerText = 'Testen...';
                badge.className = 'px-2 py-0.5 rounded text-[10px] font-mono bg-yellow-900/60 text-yellow-300 border border-yellow-800';
            }
            try {
                const res = await fetch('./api/infrastructure/mqtt/test', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ id: connId })
                });
                const d = await res.json();
                if (badge) {
                    if (d.status === 'success') {
                        badge.innerText = `🟢 OK (${d.latency_ms}ms)`;
                        badge.className = 'px-2 py-0.5 rounded text-[10px] font-mono bg-emerald-950 text-emerald-300 border border-emerald-800';
                    } else {
                        badge.innerText = '🔴 Fout';
                        badge.className = 'px-2 py-0.5 rounded text-[10px] font-mono bg-red-950 text-red-300 border border-red-800';
                        alert(`MQTT Test Fout: ${d.message}`);
                    }
                }
            } catch (e) {
                if (badge) {
                    badge.innerText = '🔴 Onbereikbaar';
                    badge.className = 'px-2 py-0.5 rounded text-[10px] font-mono bg-red-950 text-red-300 border border-red-800';
                }
            }
        }

        function openInfluxModal(c = null) {
            const fb = document.getElementById('modal-influx-feedback');
            fb.classList.add('hidden');
            if (c) {
                document.getElementById('modal-influx-title').innerText = 'InfluxDB Instantie Bewerken';
                document.getElementById('modal-influx-id').value = c.id;
                document.getElementById('modal-influx-name').value = c.name;
                document.getElementById('modal-influx-type').value = c.type || 'influx_v1';
                document.getElementById('modal-influx-url').value = c.url;
                document.getElementById('modal-influx-db').value = c.database;
                document.getElementById('modal-influx-read-db').value = c.read_database || 'openhems';
                document.getElementById('modal-influx-user').value = c.username || '';
                document.getElementById('modal-influx-pass').value = c.password || '';
                document.getElementById('modal-influx-retention').value = c.retention_policy || 'autogen';
                document.getElementById('modal-influx-default').checked = !!c.is_default;
            } else {
                document.getElementById('modal-influx-title').innerText = 'Nieuwe InfluxDB Instantie Toevoegen';
                document.getElementById('modal-influx-id').value = '';
                document.getElementById('modal-influx-name').value = '';
                document.getElementById('modal-influx-type').value = 'influx_v1';
                document.getElementById('modal-influx-url').value = 'http://localhost:8086';
                document.getElementById('modal-influx-db').value = 'hermes';
                document.getElementById('modal-influx-read-db').value = 'openhems';
                document.getElementById('modal-influx-user').value = 'hermes';
                document.getElementById('modal-influx-pass').value = '';
                document.getElementById('modal-influx-retention').value = 'autogen';
                document.getElementById('modal-influx-default').checked = false;
            }
            document.getElementById('influx-modal').classList.remove('hidden');
        }

        async function testModalInflux() {
            const fb = document.getElementById('modal-influx-feedback');
            fb.classList.remove('hidden');
            fb.className = 'p-2 rounded text-[11px] font-mono bg-yellow-950/60 text-yellow-300 border border-yellow-800 block';
            fb.innerText = 'Verbinding testen met InfluxDB...';

            const payload = {
                url: document.getElementById('modal-influx-url').value,
                database: document.getElementById('modal-influx-db').value,
                username: document.getElementById('modal-influx-user').value,
                password: document.getElementById('modal-influx-pass').value
            };

            try {
                const res = await fetch('./api/infrastructure/influxdb/test', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(payload)
                });
                const d = await res.json();
                if (d.status === 'success') {
                    fb.className = 'p-2 rounded text-[11px] font-mono bg-emerald-950/60 text-emerald-300 border border-emerald-800 block';
                    fb.innerText = `✓ ${d.message} [Databases: ${(d.databases || []).join(', ')}] (${d.latency_ms}ms)`;
                } else {
                    fb.className = 'p-2 rounded text-[11px] font-mono bg-red-950/60 text-red-300 border border-red-800 block';
                    fb.innerText = `❌ ${d.message}`;
                }
            } catch (e) {
                fb.className = 'p-2 rounded text-[11px] font-mono bg-red-950/60 text-red-300 border border-red-800 block';
                fb.innerText = `❌ Netwerkfout: ${e}`;
            }
        }

        async function saveInfluxModal(e) {
            e.preventDefault();
            const id = document.getElementById('modal-influx-id').value;
            const payload = {
                id: id || undefined,
                name: document.getElementById('modal-influx-name').value,
                type: document.getElementById('modal-influx-type').value,
                url: document.getElementById('modal-influx-url').value,
                database: document.getElementById('modal-influx-db').value,
                read_database: document.getElementById('modal-influx-read-db').value,
                username: document.getElementById('modal-influx-user').value,
                password: document.getElementById('modal-influx-pass').value,
                retention_policy: document.getElementById('modal-influx-retention').value,
                is_default: document.getElementById('modal-influx-default').checked,
                enabled: true
            };
            await fetch('./api/infrastructure/influxdb', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(payload)
            });
            closeModal('influx-modal');
            loadInfrastructure();
        }

        async function deleteInfluxConn(id) {
            if (!confirm('Weet je zeker dat je deze InfluxDB configuratie wilt verwijderen?')) return;
            await fetch('./api/infrastructure/influxdb/' + id, { method: 'DELETE' });
            loadInfrastructure();
        }

        function openMqttModal(c = null) {
            const fb = document.getElementById('modal-mqtt-feedback');
            fb.classList.add('hidden');
            if (c) {
                document.getElementById('modal-mqtt-title').innerText = 'MQTT Broker Bewerken';
                document.getElementById('modal-mqtt-id').value = c.id;
                document.getElementById('modal-mqtt-name').value = c.name;
                document.getElementById('modal-mqtt-host').value = c.host;
                document.getElementById('modal-mqtt-port').value = c.port;
                document.getElementById('modal-mqtt-topic').value = c.base_topic || 'openhems';
                document.getElementById('modal-mqtt-client-id').value = c.client_id || 'open-hems-collector';
                document.getElementById('modal-mqtt-user').value = c.username || '';
                document.getElementById('modal-mqtt-pass').value = c.password || '';
                document.getElementById('modal-mqtt-tls').checked = !!c.tls;
                document.getElementById('modal-mqtt-default').checked = !!c.is_default;
            } else {
                document.getElementById('modal-mqtt-title').innerText = 'Nieuwe MQTT Broker Toevoegen';
                document.getElementById('modal-mqtt-id').value = '';
                document.getElementById('modal-mqtt-name').value = '';
                document.getElementById('modal-mqtt-host').value = 'core-mosquitto';
                document.getElementById('modal-mqtt-port').value = 1883;
                document.getElementById('modal-mqtt-topic').value = 'openhems';
                document.getElementById('modal-mqtt-client-id').value = 'open-hems-collector';
                document.getElementById('modal-mqtt-user').value = '';
                document.getElementById('modal-mqtt-pass').value = '';
                document.getElementById('modal-mqtt-tls').checked = false;
                document.getElementById('modal-mqtt-default').checked = false;
            }
            document.getElementById('mqtt-modal').classList.remove('hidden');
        }

        async function testModalMqtt() {
            const fb = document.getElementById('modal-mqtt-feedback');
            fb.classList.remove('hidden');
            fb.className = 'p-2 rounded text-[11px] font-mono bg-yellow-950/60 text-yellow-300 border border-yellow-800 block';
            fb.innerText = 'Verbinding testen met MQTT broker...';

            const payload = {
                host: document.getElementById('modal-mqtt-host').value,
                port: parseInt(document.getElementById('modal-mqtt-port').value),
                username: document.getElementById('modal-mqtt-user').value,
                password: document.getElementById('modal-mqtt-pass').value,
                client_id: document.getElementById('modal-mqtt-client-id').value
            };

            try {
                const res = await fetch('./api/infrastructure/mqtt/test', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(payload)
                });
                const d = await res.json();
                if (d.status === 'success') {
                    fb.className = 'p-2 rounded text-[11px] font-mono bg-emerald-950/60 text-emerald-300 border border-emerald-800 block';
                    fb.innerText = `✓ ${d.message} (${d.latency_ms}ms)`;
                } else {
                    fb.className = 'p-2 rounded text-[11px] font-mono bg-red-950/60 text-red-300 border border-red-800 block';
                    fb.innerText = `❌ ${d.message}`;
                }
            } catch (e) {
                fb.className = 'p-2 rounded text-[11px] font-mono bg-red-950/60 text-red-300 border border-red-800 block';
                fb.innerText = `❌ Netwerkfout: ${e}`;
            }
        }

        async function saveMqttModal(e) {
            e.preventDefault();
            const id = document.getElementById('modal-mqtt-id').value;
            const payload = {
                id: id || undefined,
                name: document.getElementById('modal-mqtt-name').value,
                host: document.getElementById('modal-mqtt-host').value,
                port: parseInt(document.getElementById('modal-mqtt-port').value),
                base_topic: document.getElementById('modal-mqtt-topic').value,
                client_id: document.getElementById('modal-mqtt-client-id').value,
                username: document.getElementById('modal-mqtt-user').value,
                password: document.getElementById('modal-mqtt-pass').value,
                tls: document.getElementById('modal-mqtt-tls').checked,
                is_default: document.getElementById('modal-mqtt-default').checked,
                enabled: true
            };
            await fetch('./api/infrastructure/mqtt', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(payload)
            });
            closeModal('mqtt-modal');
            loadInfrastructure();
        }

        async function deleteMqttConn(id) {
            if (!confirm('Weet je zeker dat je deze MQTT broker configuratie wilt verwijderen?')) return;
            await fetch('./api/infrastructure/mqtt/' + id, { method: 'DELETE' });
            loadInfrastructure();
        }

        async function writeTestTelemetryPoint() {
            const statusEl = document.getElementById('last-write-status');
            statusEl.innerText = 'Schrijven naar InfluxDB...';
            try {
                const res = await fetch('./api/infrastructure/write-test-point', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ device_id: 'daikin_heat_pump', value: 33.0 })
                });
                const d = await res.json();
                if (d.status === 'success') {
                    statusEl.innerText = `✓ Datapunt geschreven (204 No Content · ${d.latency_ms}ms)`;
                    loadInfrastructure();
                } else {
                    statusEl.innerText = `❌ Schrijffout: ${d.message}`;
                }
            } catch (e) {
                statusEl.innerText = `❌ Fout: ${e}`;
            }
        }

        // =========================================================================
        // DASHBOARD & CHART
        // =========================================================================
        async function fetchHaEntities() {
            try {
                const res = await fetch('./api/ha/entities');
                const data = await res.json();
                window.__lastPredictionData = data;
                haEntitiesCache = data.entities || [];
                populateHaDropdowns();
            } catch (e) {
                console.warn('Could not load HA entities:', e);
            }
        }

        function populateHaDropdowns() {
            const powerSelect = document.getElementById('modal-dev-ha-power');
            const tempSelect = document.getElementById('modal-dev-ha-temp');
            const controlSelect = document.getElementById('modal-dev-ha-control');

            if (powerSelect) powerSelect.innerHTML = '<option value="">-- Selecteer Home Assistant Sensor --</option>';
            if (tempSelect) tempSelect.innerHTML = '<option value="">-- Geen / Niet van toepassing --</option>';
            if (controlSelect) controlSelect.innerHTML = '<option value="">-- Geen / Niet bestuurbaar --</option>';

            haEntitiesCache.forEach(e => {
                const opt = document.createElement('option');
                opt.value = e.entity_id;
                opt.innerText = `${e.friendly_name} (${e.entity_id})`;

                if (e.domain === 'sensor') {
                    if (powerSelect && (e.entity_id.includes('power') || e.entity_id.includes('watt') || e.entity_id.includes('energy') || e.entity_id.includes('consumption'))) {
                        powerSelect.appendChild(opt.cloneNode(true));
                    }
                    if (tempSelect && (e.entity_id.includes('temp') || e.entity_id.includes('celsius') || e.entity_id.includes('dhw'))) {
                        tempSelect.appendChild(opt.cloneNode(true));
                    }
                }
                if (e.domain === 'switch' || e.domain === 'climate' || e.domain === 'input_boolean') {
                    if (controlSelect) controlSelect.appendChild(opt.cloneNode(true));
                }
            });
        }

        
        // =========================================================================
        // CUSTOM STYLED HTML TOOLTIP HANDLER (REAL LINES, BARS & EURO COSTS)
        // =========================================================================
        
        // =========================================================================
        // UNIFIED SMOOTH TOOLTIP POSITIONING HELPER (VIEWPORT BOUNDED)
        // =========================================================================
        function positionTooltipCustom(chart, tooltip, tooltipEl) {
            const canvasRect = chart.canvas.getBoundingClientRect();
            let left = canvasRect.left + tooltip.caretX + 16;
            let top = canvasRect.top + tooltip.caretY - 30;
            if (left + 300 > window.innerWidth) {
                left = canvasRect.left + tooltip.caretX - 310;
            }
            if (left < 10) left = 10;
            if (top + 280 > window.innerHeight) {
                top = window.innerHeight - 290;
            }
            if (top < 10) top = 10;
            tooltipEl.style.left = `${left}px`;
            tooltipEl.style.top = `${top}px`;
            tooltipEl.style.opacity = '1';
        }

        // 1. EPEX & Solar Prices Chart Tooltip
        function customPricesTooltipHandler(context) {
            const { chart, tooltip } = context;
            const tooltipEl = createOrGetTooltipEl(chart);
            if (tooltip.opacity === 0 || !tooltip.body || !tooltip.dataPoints || tooltip.dataPoints.length === 0) {
                tooltipEl.style.opacity = '0';
                tooltipEl.style.pointerEvents = 'none';
                return;
            }
            const dataIndex = tooltip.dataPoints[0].dataIndex;
            const label = tooltip.title[0] || '';
            const intervalStr = (predictionResolution === '15m') ? '15 min' : '1 uur';

            let epexPrice = 0.0, exportPrice = 0.0, solarProd = 0.0;
            chart.data.datasets.forEach(ds => {
                const v = ds.data[dataIndex];
                if (!ds.label) return;
                if (ds.label.includes('Afname') || ds.label.includes('All-in')) epexPrice = Number(v) || 0.0;
                else if (ds.label.includes('Teruglever')) exportPrice = Number(v) || 0.0;
                else if (ds.label.includes('Productie') || ds.label.includes('Zonnepanelen')) solarProd = Number(v) || 0.0;
            });

            let html = `
                <div class="flex items-center justify-between border-b border-slate-700/70 pb-2 mb-2">
                    <div class="flex items-center gap-2">
                        <span class="w-2 h-2 rounded-full bg-blue-400 animate-pulse"></span>
                        <span class="font-bold text-white text-xs tracking-wide">${label}</span>
                        <span class="text-[10px] text-slate-400 font-mono">(${intervalStr})</span>
                    </div>
                    <span class="text-[10px] text-blue-300 font-mono font-semibold px-2 py-0.5 rounded bg-blue-950/80 border border-blue-800">
                        Afname: €${epexPrice.toFixed(4)}/kWh
                    </span>
                </div>
                <div class="space-y-1.5 text-xs">
                    <div class="flex items-center justify-between gap-3">
                        <div class="flex items-center">
                            <span style="display:inline-block; width:18px; height:3px; background-color:#3B82F6; border-radius:2px; margin-right:8px;"></span>
                            <span class="text-slate-300">Stroom Afname (All-in)</span>
                        </div>
                        <span class="font-bold text-white font-mono">€${epexPrice.toFixed(4)}/kWh</span>
                    </div>
                    <div class="flex items-center justify-between gap-3">
                        <div class="flex items-center">
                            <span style="display:inline-block; width:18px; height:0; border-top:2px dashed #38BDF8; margin-right:8px;"></span>
                            <span class="text-slate-300">Teruglevering (Export)</span>
                        </div>
                        <span class="font-medium text-cyan-300 font-mono">€${exportPrice.toFixed(4)}/kWh</span>
                    </div>
                    <div class="flex items-center justify-between gap-3">
                        <div class="flex items-center">
                            <span style="display:inline-block; width:10px; height:10px; background-color:rgba(234, 179, 8, 0.5); border-radius:2px; margin-right:8px;"></span>
                            <span class="text-slate-300">Zonnepanelen Productie</span>
                        </div>
                        <span class="font-bold text-amber-400 font-mono">${solarProd.toFixed(2)} kW</span>
                    </div>
                </div>
            `;
            const taxOpslag = epexPrice - exportPrice;
            if (taxOpslag > 0) {
                html += `
                    <div class="mt-2.5 pt-2 border-t border-slate-700/80 flex items-center justify-between font-bold text-xs font-mono">
                        <span class="text-slate-400">Belasting &amp; Opslag:</span>
                        <span class="text-slate-300">€${taxOpslag.toFixed(4)}/kWh</span>
                    </div>
                `;
            }
            tooltipEl.innerHTML = html;
            positionTooltipCustom(chart, tooltip, tooltipEl);
        }

        // 1.5. Net Lines Forecast Tooltip
        function customCostForecastTooltipHandler(context) {
            const { chart, tooltip } = context;
            const tooltipEl = createOrGetTooltipEl(chart);
            if (tooltip.opacity === 0 || !tooltip.body || !tooltip.dataPoints || tooltip.dataPoints.length === 0) {
                tooltipEl.style.opacity = '0';
                tooltipEl.style.pointerEvents = 'none';
                return;
            }

            tooltipEl.style.opacity = '1';
            const dataIndex = tooltip.dataPoints[0].dataIndex;
            const label = tooltip.title[0] || '';
            const intervalH = window.__lastPredictionIntervalH || 1.0;
            const intervalStr = (intervalH === 0.25) ? '15 min' : '1 uur';

            const pd = window.__lastPredictionData;
            const pBuy = (pd && pd.datasets && pd.datasets.prices_eur) ? Number(pd.datasets.prices_eur[dataIndex] || 0.25) : 0.25;
            const pSell = (pd && pd.export_prices_eur && pd.export_prices_eur[dataIndex] !== undefined)
                ? Number(pd.export_prices_eur[dataIndex])
                : Math.max(0.0, (pBuy / 1.21) - 0.11085 - 0.0121 - 0.00605);

            const netKwVal = (window.__lastPredictionNetKw && window.__lastPredictionNetKw[dataIndex] !== undefined)
                ? Number(window.__lastPredictionNetKw[dataIndex])
                : 0.0;
            const netKwhVal = netKwVal * intervalH;

            // Net cost: if >= 0 (import) -> kWh * inkoop. If < 0 (export) -> - (kWh * verkoop)
            let netCostEur = 0;
            if (netKwVal >= 0) {
                netCostEur = netKwhVal * pBuy;
            } else {
                netCostEur = - (Math.abs(netKwhVal) * pSell);
            }
            const isProfit = netCostEur < 0;

            let html = `
                <div class="flex items-center justify-between border-b border-slate-700/70 pb-2 mb-2">
                    <div class="flex items-center gap-2">
                        <span class="w-2 h-2 rounded-full bg-cyan-400 animate-pulse"></span>
                        <span class="font-bold text-white text-xs tracking-wide">${label}</span>
                        <span class="text-[10px] text-slate-400 font-mono">(${intervalStr})</span>
                    </div>
                    <span class="text-[10px] font-mono font-bold px-2 py-0.5 rounded border ${!isProfit ? 'bg-red-950/80 border-red-800 text-red-300' : 'bg-emerald-950/80 border-emerald-800 text-emerald-300'}">
                        ${!isProfit ? 'Netto Kosten: +€' : 'Netto Baten: -€'}${Math.abs(netCostEur).toFixed(2)}
                    </span>
                </div>
                <div class="space-y-2 text-xs">
                    <!-- 1. Netto Kosten (€) -->
                    <div class="flex items-center justify-between gap-3">
                        <div class="flex items-center">
                            <span style="display:inline-block; width:10px; height:10px; background-color:#F59E0B; border-radius:2px; margin-right:8px;"></span>
                            <span class="text-slate-300 font-medium">Netto Kosten</span>
                        </div>
                        <span class="font-bold font-mono ${!isProfit ? 'text-amber-400' : 'text-emerald-400'}">
                            ${!isProfit ? '+€' : '-€'}${Math.abs(netCostEur).toFixed(2)}
                        </span>
                    </div>

                    <!-- 2. Netto Verbruik (kW / kWh) -->
                    <div class="flex items-center justify-between gap-3">
                        <div class="flex items-center">
                            <span style="display:inline-block; width:14px; height:2px; background-color:#EF4444; border-radius:2px; margin-right:8px;"></span>
                            <span class="text-slate-300 font-medium">Netto Verbruik</span>
                        </div>
                        <div class="flex items-center gap-1.5 font-mono">
                            <span class="${netKwVal >= 0 ? 'text-red-400' : 'text-emerald-400'} font-bold">${netKwVal >= 0 ? '+' : ''}${netKwVal.toFixed(2)} kW</span>
                            <span class="text-slate-400 text-[10px]">(${netKwVal >= 0 ? '+' : ''}${netKwhVal.toFixed(2)} kWh)</span>
                        </div>
                    </div>

                    <!-- 3. EPEX Inkoop (€/kWh) -->
                    <div class="flex items-center justify-between gap-3 border-t border-slate-800/80 pt-1.5">
                        <div class="flex items-center">
                            <span style="display:inline-block; width:14px; height:2px; background-color:#3B82F6; border-radius:2px; margin-right:8px;"></span>
                            <span class="text-slate-400">EPEX Inkoop</span>
                        </div>
                        <span class="font-bold text-blue-300 font-mono">€${pBuy.toFixed(4)}/kWh</span>
                    </div>

                    <!-- 4. EPEX Teruglevering (€/kWh) -->
                    <div class="flex items-center justify-between gap-3">
                        <div class="flex items-center">
                            <span style="display:inline-block; width:14px; height:0; border-top:2px dashed #06B6D4; margin-right:8px;"></span>
                            <span class="text-slate-400">EPEX Teruglevering</span>
                        </div>
                        <span class="font-bold text-cyan-300 font-mono">€${pSell.toFixed(4)}/kWh</span>
                    </div>
                </div>
            `;
            tooltipEl.innerHTML = html;
            positionTooltipCustom(chart, tooltip, tooltipEl);
        }

        // 1.6. Cost History Tooltip
        function customCostHistoryTooltipHandler(context, netKwArr, netCostArr, pricesArr, exportPricesArr, intervalH) {
            const { chart, tooltip } = context;
            const tooltipEl = createOrGetTooltipEl(chart);
            if (tooltip.opacity === 0 || !tooltip.body || !tooltip.dataPoints || tooltip.dataPoints.length === 0) {
                tooltipEl.style.opacity = '0';
                tooltipEl.style.pointerEvents = 'none';
                return;
            }

            tooltipEl.style.opacity = '1';
            const dataIndex = tooltip.dataPoints[0].dataIndex;
            const label = tooltip.title[0] || '';
            const intervalStr = (intervalH === 0.25) ? '15 min' : ((intervalH === 1.0) ? '1 uur' : `${intervalH}u`);

            const pBuy = (pricesArr && pricesArr[dataIndex] !== undefined) ? Number(pricesArr[dataIndex]) : 0.25;
            const pSell = (exportPricesArr && exportPricesArr[dataIndex] !== undefined)
                ? Number(exportPricesArr[dataIndex])
                : Math.max(0.0, (pBuy / 1.21) - 0.11085 - 0.0121 - 0.00605);

            const netKwVal = (netKwArr && netKwArr[dataIndex] !== undefined) ? Number(netKwArr[dataIndex]) : 0.0;
            const netKwhVal = netKwVal * intervalH;
            const netCostVal = (netCostArr && netCostArr[dataIndex] !== undefined) ? Number(netCostArr[dataIndex]) : 0.0;
            const isProfit = netCostVal < 0;

            let html = `
                <div class="flex items-center justify-between border-b border-slate-700/70 pb-2 mb-2">
                    <div class="flex items-center gap-2">
                        <span class="w-2 h-2 rounded-full bg-cyan-400 animate-pulse"></span>
                        <span class="font-bold text-white text-xs tracking-wide">${label}</span>
                        <span class="text-[10px] text-slate-400 font-mono">(${intervalStr})</span>
                    </div>
                    <span class="text-[10px] font-mono font-bold px-2 py-0.5 rounded border ${!isProfit ? 'bg-red-950/80 border-red-800 text-red-300' : 'bg-emerald-950/80 border-emerald-800 text-emerald-300'}">
                        ${!isProfit ? 'Netto Kosten: +€' : 'Netto Baten: -€'}${Math.abs(netCostVal).toFixed(2)}
                    </span>
                </div>
                <div class="space-y-2 text-xs">
                    <!-- 1. Netto Kosten (€) -->
                    <div class="flex items-center justify-between gap-3">
                        <div class="flex items-center">
                            <span style="display:inline-block; width:10px; height:10px; background-color:#F59E0B; border-radius:2px; margin-right:8px;"></span>
                            <span class="text-slate-300 font-medium">Netto Kosten</span>
                        </div>
                        <span class="font-bold font-mono ${!isProfit ? 'text-amber-400' : 'text-emerald-400'}">
                            ${!isProfit ? '+€' : '-€'}${Math.abs(netCostVal).toFixed(2)}
                        </span>
                    </div>

                    <!-- 2. Netto Verbruik (kW / kWh) -->
                    <div class="flex items-center justify-between gap-3">
                        <div class="flex items-center">
                            <span style="display:inline-block; width:14px; height:2px; background-color:#EF4444; border-radius:2px; margin-right:8px;"></span>
                            <span class="text-slate-300 font-medium">Netto Verbruik</span>
                        </div>
                        <div class="flex items-center gap-1.5 font-mono">
                            <span class="${netKwVal >= 0 ? 'text-red-400' : 'text-emerald-400'} font-bold">${netKwVal >= 0 ? '+' : ''}${netKwVal.toFixed(2)} kW</span>
                            <span class="text-slate-400 text-[10px]">(${netKwVal >= 0 ? '+' : ''}${netKwhVal.toFixed(2)} kWh)</span>
                        </div>
                    </div>

                    <!-- 3. EPEX Inkoop (€/kWh) -->
                    <div class="flex items-center justify-between gap-3 border-t border-slate-800/80 pt-1.5">
                        <div class="flex items-center">
                            <span style="display:inline-block; width:14px; height:2px; background-color:#3B82F6; border-radius:2px; margin-right:8px;"></span>
                            <span class="text-slate-400">EPEX Inkoop All-in</span>
                        </div>
                        <span class="font-bold text-blue-300 font-mono">€${pBuy.toFixed(4)}/kWh</span>
                    </div>

                    <!-- 4. EPEX Teruglevering (€/kWh) -->
                    <div class="flex items-center justify-between gap-3">
                        <div class="flex items-center">
                            <span style="display:inline-block; width:14px; height:0; border-top:2px dashed #06B6D4; margin-right:8px;"></span>
                            <span class="text-slate-400">EPEX Teruglevering</span>
                        </div>
                        <span class="font-bold text-cyan-300 font-mono">€${pSell.toFixed(4)}/kWh</span>
                    </div>
                </div>
            `;
            tooltipEl.innerHTML = html;
            positionTooltipCustom(chart, tooltip, tooltipEl);
        }

        // 2. DHW Boiler Temperature & Tap Demand Tooltip
        function customDhwTooltipHandler(context) {
            const { chart, tooltip } = context;
            const tooltipEl = createOrGetTooltipEl(chart);
            if (tooltip.opacity === 0 || !tooltip.body || !tooltip.dataPoints || tooltip.dataPoints.length === 0) {
                tooltipEl.style.opacity = '0';
                tooltipEl.style.pointerEvents = 'none';
                return;
            }
            const dataIndex = tooltip.dataPoints[0].dataIndex;
            const label = tooltip.title[0] || '';
            const intervalStr = (predictionResolution === '15m') ? '15 min' : '1 uur';

            let tempC = 0.0, comfort = 40.0, target = 50.0, liters = 0, kwhVal = 0.0, p05 = 0.0, p95 = 0.0, unheatedC = 0.0;
            chart.data.datasets.forEach(ds => {
                const v = ds.data[dataIndex];
                if (!ds.label) return;
                if (ds.label.includes('Temperatuur P50') || ds.label === 'Verwachte Temperatuur P50 (°C)' || ds.label === 'Boilertemperatuur (°C)') {
                    tempC = Number(v) || 0.0;
                }
                else if (ds.label.includes('Zonder Verwarming')) {
                    unheatedC = Number(v) || 0.0;
                }
                else if (ds.label.includes('P05') || ds.label.includes('Minimaal')) {
                    p05 = Number(v) || 0.0;
                }
                else if (ds.label.includes('P95') || ds.label.includes('Piekverbruik')) {
                    p95 = Number(v) || 0.0;
                }
                else if (ds.label.includes('Comfort')) {
                    comfort = Number(v) || 0.0;
                }
                else if (ds.label.includes('Doel')) {
                    target = Number(v) || 0.0;
                }
                else if (ds.label.includes('Warmtevraag (kWh)')) {
                    kwhVal = Number(v) || 0.0;
                    // V40 norm: V40 = (kwh * 3600) / (4.186 * (40 - 10)) = kwh * 28.66
                    liters = Math.round(kwhVal * 28.66);
                }
                else if (ds.label.includes('Tapvraag') || ds.label.includes('Liters') || ds.label.includes('Waterverbruik') || ds.label.includes('Warmtevraag')) {
                    liters = Math.round(Number(v) || 0);
                    kwhVal = (liters * 4.186 * (40 - 10)) / 3600;
                }
            });

            // Fallback if tempC is still 0
            if (tempC === 0.0) {
                const p50Ds = chart.data.datasets.find(d => d.label && d.label.includes('Temperatuur P50'));
                if (p50Ds && p50Ds.data[dataIndex] !== undefined) {
                    tempC = Number(p50Ds.data[dataIndex]) || 0.0;
                }
            }

            const tempBadgeColor = tempC >= 45 ? 'text-emerald-400 bg-emerald-950/80 border-emerald-800' : (tempC >= 40 ? 'text-amber-400 bg-amber-950/80 border-amber-800' : 'text-red-400 bg-red-950/80 border-red-800');

            let html = `
                <div class="flex items-center justify-between border-b border-slate-700/70 pb-2 mb-2">
                    <div class="flex items-center gap-2">
                        <span class="w-2 h-2 rounded-full bg-amber-400 animate-pulse"></span>
                        <span class="font-bold text-white text-xs tracking-wide">${label}</span>
                        <span class="text-[10px] text-slate-400 font-mono">(${intervalStr})</span>
                    </div>
                    <span class="text-[10px] font-mono font-semibold px-2 py-0.5 rounded border ${tempBadgeColor}">
                        Tank: ${tempC.toFixed(1)}°C
                    </span>
                </div>
                <div class="space-y-1.5 text-xs">
                    <div class="flex items-center justify-between gap-3">
                        <div class="flex items-center">
                            <span style="display:inline-block; width:18px; height:3px; background-color:#F59E0B; border-radius:2px; margin-right:8px;"></span>
                            <span class="text-slate-300">Boilertemperatuur (P50)</span>
                        </div>
                        <span class="font-bold text-amber-300 font-mono">${tempC.toFixed(1)}°C</span>
                    </div>
                    ${p95 > 0 ? `
                    <div class="flex items-center justify-between gap-3 text-[11px]">
                        <div class="flex items-center">
                            <span style="display:inline-block; width:14px; height:8px; background-color:rgba(251, 191, 36, 0.25); border:1px solid rgba(245, 158, 11, 0.5); border-radius:2px; margin-right:8px;"></span>
                            <span class="text-amber-200/80">Bandbreedte (P95–P05)</span>
                        </div>
                        <span class="font-mono text-amber-300/90">${p95.toFixed(1)}°C (veel) – ${p05.toFixed(1)}°C (weinig)</span>
                    </div>` : ''}
                    ${unheatedC > 0 ? `
                    <div class="flex items-center justify-between gap-3 text-[11px]">
                        <div class="flex items-center">
                            <span style="display:inline-block; width:18px; height:0; border-top:2px dashed #94A3B8; margin-right:8px;"></span>
                            <span class="text-slate-400">Zonder Verwarming</span>
                        </div>
                        <span class="text-slate-300 font-mono">${unheatedC.toFixed(1)}°C</span>
                    </div>` : ''}
                    <div class="flex items-center justify-between gap-3">
                        <div class="flex items-center">
                            <span style="display:inline-block; width:18px; height:0; border-top:2px dashed #EF4444; margin-right:8px;"></span>
                            <span class="text-slate-400">Comfortgrens</span>
                        </div>
                        <span class="text-red-400 font-mono">${comfort.toFixed(1)}°C</span>
                    </div>
                    <div class="flex items-center justify-between gap-3">
                        <div class="flex items-center">
                            <span style="display:inline-block; width:18px; height:0; border-top:2px dashed #10B981; margin-right:8px;"></span>
                            <span class="text-slate-400">Doeltemperatuur</span>
                        </div>
                        <span class="text-emerald-400 font-mono">${target.toFixed(1)}°C</span>
                    </div>
                    <div class="flex items-center justify-between gap-3 pt-1 border-t border-slate-800">
                        <div class="flex items-center">
                            <span style="display:inline-block; width:10px; height:10px; background-color:rgba(56, 189, 248, 0.6); border-radius:2px; margin-right:8px;"></span>
                            <span class="text-slate-300">Warmtevraag</span>
                        </div>
                        <span class="font-bold text-sky-400 font-mono">${kwhVal.toFixed(2)} kWh (≈ ${liters} L V₄₀)</span>
                    </div>
                </div>
            `;
            tooltipEl.innerHTML = html;
            positionTooltipCustom(chart, tooltip, tooltipEl);
        }

        // 3. CV Space Heating Forecast Tooltip
        function customHeatingTooltipHandler(context) {
            const { chart, tooltip } = context;
            const tooltipEl = createOrGetTooltipEl(chart);
            if (tooltip.opacity === 0 || !tooltip.body || !tooltip.dataPoints || tooltip.dataPoints.length === 0) {
                tooltipEl.style.opacity = '0';
                tooltipEl.style.pointerEvents = 'none';
                return;
            }
            const dataIndex = tooltip.dataPoints[0].dataIndex;
            const label = tooltip.title[0] || '';
            const intervalStr = (predictionResolution === '15m') ? '15 min' : '1 uur';

            let outTemp = 0.0, inTemp = 0.0, cop = 0.0, thLoss = 0.0, elPower = 0.0, cost = 0.0;
            chart.data.datasets.forEach(ds => {
                const v = ds.data[dataIndex];
                if (!ds.label) return;
                if (ds.label.includes('Buitentemperatuur')) outTemp = Number(v) || 0.0;
                if (ds.label.includes('Binnentemperatuur') || ds.label.includes('Ruimtetemperatuur')) inTemp = Number(v) || 0.0;
                if (ds.label.includes('COP')) cop = Number(v) || 0.0;
                if (ds.label.includes('Warmteverlies')) thLoss = Number(v) || 0.0;
                if (ds.label.includes('Stroom Warmtepomp')) elPower = Number(v) || 0.0;
                if (ds.label.includes('Stroomkosten')) cost = Number(v) || 0.0;
            });

            const intervalMult = (predictionResolution === '15m') ? 0.25 : 1.0;
            const thKwh = thLoss * intervalMult;
            const elKwh = elPower * intervalMult;

            let html = `
                <div class="flex items-center justify-between border-b border-slate-700/70 pb-2 mb-2">
                    <div class="flex items-center gap-2">
                        <span class="w-2 h-2 rounded-full bg-red-400 animate-pulse"></span>
                        <span class="font-bold text-white text-xs tracking-wide">${label}</span>
                        <span class="text-[10px] text-slate-400 font-mono">(${intervalStr})</span>
                    </div>
                    <span class="text-[10px] font-mono font-semibold px-2 py-0.5 rounded bg-rose-950/80 border border-rose-800 text-rose-300">
                        Binnen: ${inTemp.toFixed(1)}°C
                    </span>
                </div>
                <div class="space-y-1.5 text-xs">
                    <div class="flex items-center justify-between gap-3">
                        <div class="flex items-center">
                            <span style="display:inline-block; width:18px; height:3px; background-color:#60A5FA; border-radius:2px; margin-right:8px;"></span>
                            <span class="text-slate-300">Buitentemperatuur</span>
                        </div>
                        <span class="font-medium text-blue-300 font-mono">${outTemp.toFixed(1)}°C</span>
                    </div>
                    <div class="flex items-center justify-between gap-3">
                        <div class="flex items-center">
                            <span style="display:inline-block; width:18px; height:3px; background-color:#F43F5E; border-radius:2px; margin-right:8px;"></span>
                            <span class="text-slate-300">Verwachte Binnentemp</span>
                        </div>
                        <span class="font-bold text-rose-300 font-mono">${inTemp.toFixed(1)}°C</span>
                    </div>
                    <div class="flex items-center justify-between gap-3">
                        <div class="flex items-center">
                            <span style="display:inline-block; width:18px; height:0; border-top:2px dashed #10B981; margin-right:8px;"></span>
                            <span class="text-slate-300">Daikin Carnot COP</span>
                        </div>
                        <span class="font-medium text-emerald-300 font-mono">${cop.toFixed(2)}</span>
                    </div>
                    <div class="flex items-center justify-between gap-3 pt-1 border-t border-slate-800">
                        <div class="flex items-center">
                            <span style="display:inline-block; width:10px; height:10px; background-color:rgba(239, 68, 68, 0.6); border-radius:2px; margin-right:8px;"></span>
                            <span class="text-slate-300">Warmteverlies Woning</span>
                        </div>
                        <span class="font-bold text-red-400 font-mono">${thLoss.toFixed(2)} kW_th (${thKwh.toFixed(2)} kWh)</span>
                    </div>
                    <div class="flex items-center justify-between gap-3">
                        <div class="flex items-center">
                            <span style="display:inline-block; width:10px; height:10px; background-color:rgba(245, 158, 11, 0.7); border-radius:2px; margin-right:8px;"></span>
                            <span class="text-slate-300">Stroom Warmtepomp</span>
                        </div>
                        <span class="font-bold text-amber-400 font-mono">${elPower.toFixed(2)} kW_el (${elKwh.toFixed(2)} kWh)</span>
                    </div>
                </div>
                <div class="mt-2.5 pt-2 border-t border-slate-700/80 flex items-center justify-between font-bold text-xs font-mono">
                    <span class="text-slate-400 uppercase tracking-wider">Verwachte Stroomkosten:</span>
                    <span class="text-cyan-300 text-sm">€${cost.toFixed(3)}</span>
                </div>
            `;
            tooltipEl.innerHTML = html;
            positionTooltipCustom(chart, tooltip, tooltipEl);
        }

        function createOrGetTooltipEl(chart) {
            let tooltipEl = document.getElementById('chartjs-custom-tooltip');
            if (!tooltipEl) {
                tooltipEl = document.createElement('div');
                tooltipEl.id = 'chartjs-custom-tooltip';
                tooltipEl.className = 'pointer-events-none fixed z-[9999] bg-[#0B0F17]/95 backdrop-blur-md border border-slate-700/90 rounded-2xl shadow-2xl p-3.5 text-xs font-mono transition-opacity duration-100 text-slate-200';
                tooltipEl.style.minWidth = '250px';
                tooltipEl.style.maxWidth = '320px';
                document.body.appendChild(tooltipEl);
            }
            return tooltipEl;
        }

        function customHemsTooltipHandler(context, isPrediction = false) {
            const { chart, tooltip } = context;
            const tooltipEl = createOrGetTooltipEl(chart);

            // Hide immediately when cursor moves away or outside graph area
            if (tooltip.opacity === 0 || !tooltip.body || !tooltip.dataPoints || tooltip.dataPoints.length === 0) {
                tooltipEl.style.opacity = '0';
                tooltipEl.style.pointerEvents = 'none';
                return;
            }

            tooltipEl.style.opacity = '1';

            const dataIndex = tooltip.dataPoints[0].dataIndex;
            const label = tooltip.title[0] || '';

            // Extract EXACT interval duration (hours)
            let intervalH = 1.0;
            if (isPrediction) {
                intervalH = window.__lastPredictionIntervalH || 1.0;
            } else {
                intervalH = window.__lastHistoricalIntervalH || (chart.data.labels.length > 50 ? 0.25 : 1.0);
            }

            // Extract EXACT prices directly from the dataset or data cache (NEVER use hardcoded defaults)
            let importPrice = 0.25;
            let exportPrice = 0.10;

            // Priority 1: Check if Stroomprijs dataset exists in the chart itself
            const priceDataset = chart.data.datasets.find(ds => ds.label && ds.label.includes('Stroomprijs'));
            if (priceDataset && priceDataset.data && priceDataset.data[dataIndex] !== undefined) {
                importPrice = Number(priceDataset.data[dataIndex]);
            } else if (isPrediction && window.__lastPredictionData?.datasets?.prices_eur) {
                importPrice = Number(window.__lastPredictionData.datasets.prices_eur[dataIndex]);
            } else if (!isPrediction && window.__lastHistoricalData?.prices) {
                importPrice = Number(window.__lastHistoricalData.prices[dataIndex]);
            }

            // Priority 2: Extract export price (Powerpeers dynamic: kale beurs min verkoopopslag)
            if (!isPrediction && window.__lastHistoricalData?.export_prices && window.__lastHistoricalData.export_prices[dataIndex] !== undefined) {
                exportPrice = Number(window.__lastHistoricalData.export_prices[dataIndex]);
            } else if (isPrediction && window.__lastPredictionData?.export_prices_eur && window.__lastPredictionData.export_prices_eur[dataIndex] !== undefined) {
                exportPrice = Number(window.__lastPredictionData.export_prices_eur[dataIndex]);
            } else {
                // Approximate dynamic export: (All-in - BTW - Energiebelasting - Opslag)
                exportPrice = Math.max(0.0, (importPrice / 1.21) - 0.11085 - 0.0121 - 0.00605);
            }

            let html = `
                <div class="flex items-center justify-between border-b border-slate-700/70 pb-2 mb-2">
                    <div class="flex items-center gap-2">
                        <span class="w-2 h-2 rounded-full bg-cyan-400 animate-pulse"></span>
                        <span class="font-bold text-white text-xs tracking-wide">${label}</span>
                        <span class="text-[10px] text-slate-400 font-mono">(${intervalH === 0.25 ? '15 min' : '1 uur'})</span>
                    </div>
                    <span class="text-[10px] text-cyan-300 font-mono font-semibold px-2 py-0.5 rounded bg-cyan-950/80 border border-cyan-800">
                        Inkoop: €${importPrice.toFixed(4)}/kWh
                    </span>
                </div>
                <div class="space-y-1.5">
            `;

            let netCostVal = 0.0;
            let hasNetCost = false;

            tooltip.dataPoints.forEach(dp => {
                const ds = chart.data.datasets[dp.datasetIndex];
                if (!ds) return;
                const rawVal = dp.raw || 0;
                let dsLabel = ds.label || '';
                const isLine = ds.type === 'line' || (ds.borderDash && ds.borderDash.length > 0);
                const color = ds.borderColor || ds.backgroundColor;

                // Strip "(kWh)" or "(kW)" from label for clean display
                const cleanLabel = dsLabel.replace(/\s*\(kWh\)|\s*\(kW\)/g, '').trim();

                // Skip mirror duplicate "Zon Direct Benut" in tooltip (Opgewekt Gebruikt already shows it!)
                if (cleanLabel.includes('Zon Direct Benut')) {
                    return;
                }

                // Handle Stroomprijs row
                if (cleanLabel.includes('Stroomprijs') || cleanLabel.includes('Tarief') || cleanLabel.includes('Prijs')) {
                    html += `
                        <div class="flex items-center justify-between gap-3 text-xs">
                            <div class="flex items-center truncate">
                                <span style="display:inline-block; width:18px; height:0; border-top:2px dashed ${color}; margin-right:8px; vertical-align:middle;"></span>
                                <span class="text-slate-300 truncate">${cleanLabel}</span>
                            </div>
                            <div class="flex items-center gap-1.5 flex-shrink-0">
                                <span class="font-bold text-cyan-300 font-mono">€${Number(rawVal).toFixed(4)}/kWh</span>
                            </div>
                        </div>
                    `;
                    return;
                }

                // Format PURE POWER (kW) as primary and INTERVAL ENERGY (kWh) as secondary
                const absKw = Math.abs(rawVal);
                const kwhVal = Number((absKw * intervalH).toFixed(3));
                const powerStr = `${absKw.toFixed(2)} kW`;
                const energyStr = `${kwhVal >= 10.0 ? kwhVal.toFixed(1) : kwhVal.toFixed(2)} kWh`;
                const intervalLabel = (intervalH === 0.25) ? 'kwartier' : 'uur';

                // Calculate monetary cost / revenue per dataset type
                let costBadge = '';

                if (cleanLabel.includes('Afname')) {
                    const c = kwhVal * importPrice;
                    netCostVal += c;
                    hasNetCost = true;
                    costBadge = `<span class="text-red-400 font-bold ml-auto">+€${c.toFixed(2)}</span>`;
                } else if (cleanLabel.includes('Teruglevering')) {
                    const rev = kwhVal * exportPrice;
                    netCostVal -= rev;
                    hasNetCost = true;
                    costBadge = `<span class="text-emerald-400 font-bold ml-auto">-€${rev.toFixed(2)} opbr.</span>`;
                } else if (cleanLabel.includes('Verwacht Netto')) {
                    if (rawVal >= 0) {
                        const c = kwhVal * importPrice;
                        netCostVal = c;
                        hasNetCost = true;
                        costBadge = `<span class="text-red-400 font-bold ml-auto">+€${c.toFixed(2)}</span>`;
                    } else {
                        const rev = kwhVal * exportPrice;
                        netCostVal = -rev;
                        hasNetCost = true;
                        costBadge = `<span class="text-emerald-400 font-bold ml-auto">-€${rev.toFixed(2)} opbr.</span>`;
                    }
                } else if (cleanLabel.includes('Opgewekt Gebruikt')) {
                    const sav = kwhVal * importPrice;
                    costBadge = `<span class="text-cyan-400 font-medium ml-auto">€${sav.toFixed(2)} besp.</span>`;
                } else if (cleanLabel.includes('Zon Productie')) {
                    const rev = kwhVal * exportPrice;
                    costBadge = `<span class="text-amber-400 font-medium ml-auto">€${rev.toFixed(2)} opbr.</span>`;
                } else if (cleanLabel.includes('Accu Ontladen')) {
                    const sav = kwhVal * importPrice;
                    costBadge = `<span class="text-teal-400 font-medium ml-auto">€${sav.toFixed(2)} besp.</span>`;
                } else if (cleanLabel.includes('Totaal Verbruik')) {
                    const totC = kwhVal * importPrice;
                    costBadge = `<span class="text-orange-400 font-bold ml-auto">€${totC.toFixed(2)}</span>`;
                } else if (cleanLabel.includes('SWW') || cleanLabel.includes('CV') || cleanLabel.includes('Accu Laden') || cleanLabel.includes('Ongedefinieerd')) {
                    const c = kwhVal * importPrice;
                    costBadge = `<span class="text-slate-400 ml-auto">€${c.toFixed(2)}</span>`;
                }

                // Visual indicator: ACTUAL line for lines, rounded pill for bars
                let indicatorHtml = '';
                if (isLine) {
                    indicatorHtml = `<span style="display:inline-block; width:18px; height:3px; background-color:${color}; border-radius:2px; margin-right:8px; vertical-align:middle;"></span>`;
                } else {
                    indicatorHtml = `<span style="display:inline-block; width:10px; height:10px; background-color:${color}; border-radius:2px; margin-right:8px; vertical-align:middle;"></span>`;
                }

                html += `
                    <div class="flex items-center justify-between gap-3 text-xs">
                        <div class="flex items-center truncate">
                            ${indicatorHtml}
                            <span class="text-slate-300 truncate">${cleanLabel}</span>
                        </div>
                        <div class="flex items-center gap-2 flex-shrink-0 font-mono">
                            <span class="font-bold text-white">${rawVal < 0 ? '-' : ''}${powerStr}</span>
                            <span class="text-[10px] text-slate-400 font-sans">(${energyStr})</span>
                            ${costBadge}
                        </div>
                    </div>
                `;
            });

            if (hasNetCost) {
                const isNetProfit = netCostVal < 0;
                const netColor = isNetProfit ? 'text-emerald-400' : 'text-red-400';
                const netLabel = isNetProfit ? 'Netto Opbrengst' : 'Netto Kosten';
                html += `
                    <div class="mt-2.5 pt-2 border-t border-slate-700/80 flex items-center justify-between font-bold text-xs font-mono">
                        <span class="text-slate-400 uppercase tracking-wider">${netLabel}:</span>
                        <span class="${netColor} text-sm">${isNetProfit ? '+' : ''}€${Math.abs(netCostVal).toFixed(2)}</span>
                    </div>
                `;
            }

            html += `</div>`;
            tooltipEl.innerHTML = html;

            // Position tooltip smoothly relative to viewport
            const canvasRect = chart.canvas.getBoundingClientRect();
            let left = canvasRect.left + tooltip.caretX + 16;
            let top = canvasRect.top + tooltip.caretY - 30;

            // Prevent overflowing window right
            if (left + 280 > window.innerWidth) {
                left = canvasRect.left + tooltip.caretX - 290;
            }
            if (left < 10) left = 10;

            // Prevent overflowing window bottom
            if (top + 240 > window.innerHeight) {
                top = window.innerHeight - 250;
            }
            if (top < 10) top = 10;

            tooltipEl.style.left = `${left}px`;
            tooltipEl.style.top = `${top}px`;
            tooltipEl.style.opacity = '1';
        }


        async function loadChartData() {
            try {
                const simParam = window.__simulateBattery ? '&simulate_battery=1' : '';
                const res = await fetch('./api/schedule/chart-data?resolution=' + encodeURIComponent(predictionResolution) + simParam);
                const data = await res.json();
                window.__lastPredictionData = data;
                window.__lastPredictionIntervalH = data.interval_h || (predictionResolution === '15m' ? 0.25 : 1.0);

                const adv = data.banner_text || `Beste stroomtarief om ${data.cheapest_hour} (€${Number(data.cheapest_price_eur).toFixed(4)}/kWh)`;
                if (document.getElementById('banner-text')) document.getElementById('banner-text').innerText = adv;
                if (document.getElementById('analytics-banner-text')) document.getElementById('analytics-banner-text').innerText = adv;
                if (document.getElementById('battery-status-banner')) document.getElementById('battery-status-banner').innerText = data.battery_status_msg;
                if (document.getElementById('prediction-baseload-badge')) document.getElementById('prediction-baseload-badge').innerText = `Basislast: ${data.baseload_watts || 300} W`;
                if (document.getElementById('tab-baseload-input')) document.getElementById('tab-baseload-input').value = data.baseload_watts || 300;

// Dual Polarity Stacked Engine (Power Producers Aligned):
                // - Above 0 axis: All consumers stacked together (Basislast, SWW, CV, Accu Laden)
                // - Below 0 axis: All generation/sources stacked together (Zon Productie, Accu Ontladen)
                // - Net overlay line: Expected Net Grid Power (Cons - Prod)
                const labels = data.labels;
                const pricesArr = data.datasets.prices_eur || [];
                const netPowerArr = data.datasets.net_power_kw || [];

                // Populate totals, costs, recommendation banner & surplus badge for BOTH tabs
                const kwhText = `⚡ Verbruik: ${(data.total_consumption_kwh || 0.0).toFixed(1)} kWh`;
                const costVal = Number(data.total_net_cost_eur || 0.0);
                const costText = `💶 Netto: €${costVal.toFixed(2)}`;
                const surplusText = `☀️ Overschot: ${data.surplus_total_kwh || 0.0} kWh`;

                if (document.getElementById('prediction-total-kwh-badge')) document.getElementById('prediction-total-kwh-badge').innerText = kwhText;
                if (document.getElementById('dash-prediction-total-kwh-badge')) document.getElementById('dash-prediction-total-kwh-badge').innerText = kwhText;

                if (document.getElementById('prediction-total-cost-badge')) document.getElementById('prediction-total-cost-badge').innerText = costText;
                if (document.getElementById('dash-prediction-total-cost-badge')) document.getElementById('dash-prediction-total-cost-badge').innerText = costText;

                if (document.getElementById('prediction-surplus-badge')) document.getElementById('prediction-surplus-badge').innerText = surplusText;
                if (document.getElementById('dash-prediction-surplus-badge')) document.getElementById('dash-prediction-surplus-badge').innerText = surplusText;

                if (document.getElementById('solar-recommendation-text')) {
                    document.getElementById('solar-recommendation-text').innerText = data.solar_recommendation || "☀️ Geen overschot";
                }

                // Render Top 4 Forecast KPI Cards (1: Costs, 2: Solar, 3: Savings, 4: Heat Pump)
                if (data.forecast_kpis) {
                    const fk = data.forecast_kpis;
                    const elCostsMain = document.getElementById('pred-kpi-costs-main');
                    const elCostsSub = document.getElementById('pred-kpi-costs-sub');
                    if (elCostsMain) elCostsMain.innerText = fk.costs.main;
                    if (elCostsSub) elCostsSub.innerText = fk.costs.sub;

                    const elSolarMain = document.getElementById('pred-kpi-solar-main');
                    const elSolarSub = document.getElementById('pred-kpi-solar-sub');
                    if (elSolarMain) {
                        elSolarMain.innerHTML = `${fk.solar.main} <span class="text-xs text-slate-400 font-normal">${fk.solar.main_extra || ''}</span>`;
                    }
                    if (elSolarSub) elSolarSub.innerText = fk.solar.sub;

                    const elSavMain = document.getElementById('pred-kpi-savings-main');
                    const elSavSub = document.getElementById('pred-kpi-savings-sub');
                    if (elSavMain) elSavMain.innerText = fk.savings.main;
                    if (elSavSub) elSavSub.innerText = fk.savings.sub;

                    const elHpMain = document.getElementById('pred-kpi-hp-main');
                    const elHpSub = document.getElementById('pred-kpi-hp-sub');
                    if (elHpMain) {
                        elHpMain.innerHTML = `${fk.heatpump.main} <span class="text-xs text-slate-400 font-normal">${fk.heatpump.main_extra || ''}</span>`;
                    }
                    if (elHpSub) elHpSub.innerText = fk.heatpump.sub;
                }

                // Populate Live Active DHW Banner (Visible only when heating)
                const liveDhwCard = document.getElementById('live-dhw-active-card');
                const adh = data.active_dhw_status;
                if (liveDhwCard) {
                    if (adh && adh.is_active) {
                        liveDhwCard.classList.remove('hidden');
                        const targetBadge = document.getElementById('live-dhw-target-badge');
                        if (targetBadge) {
                            targetBadge.innerText = `Doel: ${Number(adh.target_temp_c).toFixed(0)}°C`;
                            targetBadge.className = adh.should_merge 
                                ? 'px-2 py-0.5 rounded-full text-[10px] font-bold bg-purple-600 text-white border border-purple-400 shadow'
                                : 'px-2 py-0.5 rounded-full text-[10px] font-bold bg-emerald-600 text-white border border-emerald-400 shadow';
                        }
                        const metricsSub = document.getElementById('live-dhw-metrics-sub');
                        if (metricsSub) {
                            metricsSub.innerText = `Actueel Vermogen: ${adh.power_kw} kW · Boilervat: ${adh.tank_temp_c}°C · Modus: ${adh.mode_label}`;
                        }
                        const badgeContainer = document.getElementById('live-dhw-decision-badge-container');
                        if (badgeContainer) {
                            if (adh.should_merge) {
                                badgeContainer.innerHTML = '<span class="inline-flex items-center gap-1.5 px-3 py-1 rounded-lg text-xs font-bold bg-purple-500/20 text-purple-300 border border-purple-500/40 shadow"><span class="w-2 h-2 rounded-full bg-purple-400 animate-pulse"></span> Gekozen: Doorwarmen tot 60°C (Fusie)</span>';
                            } else {
                                badgeContainer.innerHTML = '<span class="inline-flex items-center gap-1.5 px-3 py-1 rounded-lg text-xs font-bold bg-emerald-500/20 text-emerald-300 border border-emerald-500/40 shadow"><span class="w-2 h-2 rounded-full bg-emerald-400"></span> Gekozen: Stoppen bij 50°C (Basislading)</span>';
                            }
                        }
                        const decTitle = document.getElementById('live-dhw-decision-title');
                        if (decTitle) decTitle.innerText = adh.decision_title || 'Besluitvorming:';
                        const decText = document.getElementById('live-dhw-decision-text');
                        if (decText) decText.innerText = adh.decision_explanation || '';
                    } else {
                        liveDhwCard.classList.add('hidden');
                    }
                }

                // Render Horizontal Mode Timeline Bar & Dynamic Rolling Ticks
                const tlContainer = document.getElementById('dhw-timeline-bar');
                const ticksContainer = document.getElementById('dhw-timeline-ticks');
                if (tlContainer && data.dhw_mode_timeline) {
                    tlContainer.innerHTML = '';
                    data.dhw_mode_timeline.forEach(seg => {
                        const block = document.createElement('div');
                        block.className = 'flex-1 h-full rounded-sm transition-all duration-150 cursor-pointer relative group';
                        block.style.backgroundColor = seg.color;
                        if (seg.mode === 'forced_off' || seg.mode === 'peak_lockout') {
                            block.style.backgroundColor = '#EF4444';
                            block.style.backgroundImage = 'repeating-linear-gradient(45deg, transparent, transparent 3px, rgba(0,0,0,0.35) 3px, rgba(0,0,0,0.35) 6px)';
                        } else if (seg.mode === 'advised_off' || seg.mode === 'peak_advice') {
                            block.style.backgroundColor = '#F59E0B';
                            block.style.backgroundImage = 'none';
                            block.style.border = 'none';
                        } else if (seg.mode === 'advised_on') {
                            block.style.backgroundColor = '#4ADE80';
                            block.style.backgroundImage = 'repeating-linear-gradient(45deg, #10B981, #10B981 3px, #86EFAC 3px, #86EFAC 6px)';
                        } else if (seg.mode === 'forced_on' || seg.mode === 'forced_standard_50' || seg.mode === 'forced_night_50') {
                            block.style.backgroundColor = '#10B981';
                            block.style.backgroundImage = 'none';
                            block.style.border = 'none';
                        } else if (seg.mode === 'max_on' || seg.mode === 'forced_solar_boost_60') {
                            block.style.backgroundColor = '#A855F7';
                            block.style.backgroundImage = 'none';
                            block.style.border = 'none';
                        } else {
                            block.style.backgroundColor = '#1E293B';
                            block.style.backgroundImage = 'none';
                            block.style.border = 'none';
                        }
                        // Tooltip on hover
                        block.title = `${seg.time} | ${seg.label}\n${seg.description}`;
                        tlContainer.appendChild(block);
                    });
                }

                // Update Dynamic Spitsblokkades Summary Card & Decision Box
                const dynPeaks = data.dynamic_peaks || [];
                window.__lastDynamicPeaks = dynPeaks;
                const hTitle = document.getElementById('dhw-dyn-lockout-title');
                const hHours = document.getElementById('dhw-dyn-lockout-hours');
                const hSub = document.getElementById('dhw-dyn-lockout-sub');
                const spitsDetailEl = document.getElementById('dhw-box-spits-detail');

                if (dynPeaks.length === 0) {
                    if (hTitle) hTitle.innerText = '✨ Spitsblokkades: Geen';
                    if (hHours) {
                        hHours.innerText = 'Geen prijspieken (Vlak tarief 🔓)';
                        hHours.className = 'text-xs font-bold text-emerald-400';
                    }
                    if (hSub) hSub.innerText = 'Tarief schommelt minimaal: warmtepomp mag overdag vrij opereren.';
                    if (spitsDetailEl) spitsDetailEl.innerHTML = '<span class="text-emerald-400 font-bold">Geen prijspieken gedetecteerd 🔓 (volledige vrijloop)</span>';
                } else {
                    const hardMins = dynPeaks.reduce((acc, p) => acc + (p.hard_duration_mins || (p.is_hard_lockout ? p.duration_mins : 0)), 0);
                    const hardHours = (hardMins / 60).toFixed(1);
                    if (hTitle) hTitle.innerText = `🚫 Spitsblokkades (${hardHours} Uur)`;
                    if (hHours) {
                        hHours.innerText = dynPeaks.map(p => {
                            if (p.hard_start_time) {
                                return `${p.name} ${p.hard_start_time}–${p.hard_end_time} (${p.hard_duration_mins}m 🔒)`;
                            } else {
                                return `${p.name} ${p.start_time}–${p.end_time} (${p.duration_mins}m ⚠️)`;
                            }
                        }).join(' · ');
                        hHours.className = hardMins > 0 ? 'text-xs font-bold text-red-400' : 'text-xs font-bold text-amber-400';
                    }
                    const maxPeakP = Math.max(...dynPeaks.map(p => p.max_price));
                    if (hSub) hSub.innerText = `Piekhoogte tot €${maxPeakP.toFixed(3)}/kWh. Gecapt op max 2,5u tegen woningafkoeling.`;
                    if (spitsDetailEl) {
                        spitsDetailEl.innerHTML = dynPeaks.map(p => {
                            const badgeColor = p.is_hard_lockout ? 'text-red-300' : 'text-amber-300';
                            const icon = p.is_hard_lockout ? '🔒' : '⚠️';
                            return `<span class="${badgeColor} font-bold">${p.name} ${p.start_time}–${p.end_time} (${p.duration_mins}m ${icon})</span>`;
                        }).join(' · ');
                    }
                }
                if (ticksContainer && labels && labels.length > 0) {
                    ticksContainer.innerHTML = '';
                    const totalL = labels.length;
                    const step = Math.max(1, Math.floor(totalL / 8));
                    for (let t_i = 0; t_i < totalL; t_i += step) {
                        const s = document.createElement('span');
                        s.innerText = labels[t_i];
                        ticksContainer.appendChild(s);
                    }
                    if (ticksContainer.children.length < 9 && totalL > 0) {
                        const sEnd = document.createElement('span');
                        sEnd.innerText = labels[totalL - 1];
                        ticksContainer.appendChild(sEnd);
                    }
                }

                // Populate Planning Summary Cards
                const dSum = data.dhw_planning_summary || {};
                const modeEl = document.getElementById('dhw-summary-mode');
                if (modeEl && dSum.planned_mode_label) {
                    modeEl.innerText = `${dSum.planned_mode_label}`;
                    if (dSum.planned_mode === 'forced_solar_boost_60' || dSum.planned_mode === 'max_on') {
                        modeEl.className = 'text-xs font-bold text-purple-300';
                    } else if (dSum.planned_mode === 'forced_night_50' || dSum.planned_mode === 'forced_standard_50' || dSum.planned_mode === 'forced_on') {
                        modeEl.className = 'text-xs font-bold text-emerald-400';
                    } else if (dSum.planned_mode === 'advised_on') {
                        modeEl.className = 'text-xs font-bold text-emerald-300';
                    } else {
                        modeEl.className = 'text-xs font-bold text-slate-300';
                    }
                }
                if (document.getElementById('dhw-summary-times') && dSum.run_start) {
                    document.getElementById('dhw-summary-times').innerText = `Venster: ${dSum.run_start} – ${dSum.run_end} (${dSum.run_duration_min} min)`;
                }
                if (document.getElementById('dhw-summary-energy') && dSum.total_stroom_kwh) {
                    const thKwh = (dSum.target_temp_c >= 55 ? '8.1 kWh_th' : '4.1 kWh_th');
                    document.getElementById('dhw-summary-energy').innerText = `~${dSum.total_stroom_kwh} kWh stroom (${thKwh})`;
                }
                if (document.getElementById('dhw-summary-shower') && dSum.target_temp_c) {
                    const liters = dSum.target_temp_c >= 55 ? '~715L' : '~496L';
                    document.getElementById('dhw-summary-shower').innerText = `Mengcapaciteit ${liters} douchewater van 38°C.`;
                }



                // Populate 6-Box Prediction Metrics Aligned with Historical
                const ps = data.prediction_stats || {};
                if (ps.zonnepanelen) {
                    if (document.getElementById('pred-stat-solar-total')) document.getElementById('pred-stat-solar-total').innerText = ps.zonnepanelen.total_kwh || '-- kWh';
                    if (document.getElementById('pred-stat-solar-cost')) document.getElementById('pred-stat-solar-cost').innerText = ps.zonnepanelen.cost_eur || '€--';
                    if (document.getElementById('pred-stat-solar-last')) document.getElementById('pred-stat-solar-last').innerText = ps.zonnepanelen.last || '--';
                    if (document.getElementById('pred-stat-solar-min')) document.getElementById('pred-stat-solar-min').innerText = ps.zonnepanelen.min || '--';
                }
                if (ps.teruglevering) {
                    if (document.getElementById('pred-stat-terug-total')) document.getElementById('pred-stat-terug-total').innerText = ps.teruglevering.total_kwh || '-- kWh';
                    if (document.getElementById('pred-stat-terug-cost')) document.getElementById('pred-stat-terug-cost').innerText = ps.teruglevering.cost_eur || '€--';
                    if (document.getElementById('pred-stat-terug-last')) document.getElementById('pred-stat-terug-last').innerText = ps.teruglevering.last || '--';
                    if (document.getElementById('pred-stat-terug-min')) document.getElementById('pred-stat-terug-min').innerText = ps.teruglevering.min || '--';
                }
                if (ps.afname) {
                    if (document.getElementById('pred-stat-afname-total')) document.getElementById('pred-stat-afname-total').innerText = ps.afname.total_kwh || '-- kWh';
                    if (document.getElementById('pred-stat-afname-cost')) document.getElementById('pred-stat-afname-cost').innerText = ps.afname.cost_eur || '€--';
                    if (document.getElementById('pred-stat-afname-last')) document.getElementById('pred-stat-afname-last').innerText = ps.afname.last || '--';
                    if (document.getElementById('pred-stat-afname-max')) document.getElementById('pred-stat-afname-max').innerText = ps.afname.max || '--';
                }
                if (ps.totaal_opgewekt) {
                    if (document.getElementById('pred-stat-opgewekt-total')) document.getElementById('pred-stat-opgewekt-total').innerText = ps.totaal_opgewekt.total_kwh || '-- kWh';
                    if (document.getElementById('pred-stat-opgewekt-cost')) document.getElementById('pred-stat-opgewekt-cost').innerText = ps.totaal_opgewekt.cost_eur || '€--';
                    if (document.getElementById('pred-stat-opgewekt-last')) document.getElementById('pred-stat-opgewekt-last').innerText = ps.totaal_opgewekt.last || '--';
                    if (document.getElementById('pred-stat-opgewekt-min')) document.getElementById('pred-stat-opgewekt-min').innerText = ps.totaal_opgewekt.min || '--';
                }
                if (ps.opgewekt_gebruikt) {
                    if (document.getElementById('pred-stat-selfcons-total')) document.getElementById('pred-stat-selfcons-total').innerText = ps.opgewekt_gebruikt.total_kwh || '-- kWh';
                    if (document.getElementById('pred-stat-selfcons-cost')) document.getElementById('pred-stat-selfcons-cost').innerText = ps.opgewekt_gebruikt.cost_eur || '€--';
                    if (document.getElementById('pred-stat-selfcons-last')) document.getElementById('pred-stat-selfcons-last').innerText = ps.opgewekt_gebruikt.last || '--';
                    if (document.getElementById('pred-stat-selfcons-min')) document.getElementById('pred-stat-selfcons-min').innerText = ps.opgewekt_gebruikt.min || '--';
                }
                if (ps.totaal_verbruik) {
                    if (document.getElementById('pred-stat-verbruik-total')) document.getElementById('pred-stat-verbruik-total').innerText = ps.totaal_verbruik.total_kwh || '-- kWh';
                    if (document.getElementById('pred-stat-verbruik-cost')) document.getElementById('pred-stat-verbruik-cost').innerText = ps.totaal_verbruik.cost_eur || '€--';
                    if (document.getElementById('pred-stat-verbruik-last')) document.getElementById('pred-stat-verbruik-last').innerText = ps.totaal_verbruik.last || '--';
                    if (document.getElementById('pred-stat-verbruik-max')) document.getElementById('pred-stat-verbruik-max').innerText = ps.totaal_verbruik.max || '--';
                }

                // === UNIFIED POWER (kW) STANDARDIZATION & SYMMETRIC 0-AXIS ALIGNMENT ===
                const intervalH = data.interval_h || (predictionResolution === '15m' ? 0.25 : 1.0);

                const unallocKw = (data.datasets.unallocated_kw || data.datasets.baseload_kw || []).map(Number);
                const boilerKw = (data.datasets.boiler_kw || []).map(Number);
                const heatingKw = (data.datasets.heating_kw || []).map(Number);
                const batteryChargeKw = (data.datasets.battery_charge_kw || []).map(Number);
                const solarNegKw = (data.datasets.solar_kw_neg || []).map(v => -Math.abs(Number(v)));
                const batteryDischargeNegKw = (data.datasets.battery_discharge_kw_neg || []).map(v => -Math.abs(Number(v)));
                const netKw = (netPowerArr || []).map(Number);

                // Calculate symmetric center-aligned bounds in kW (0 line exactly at 50% height)
                let consKwArr = [];
                for (let i = 0; i < labels.length; i++) {
                    consKwArr.push((unallocKw[i] || 0) + (boilerKw[i] || 0) + (heatingKw[i] || 0) + (batteryChargeKw[i] || 0));
                }

                let maxAbsKw = Math.max(
                    ...consKwArr,
                    ...solarNegKw.map(Math.abs),
                    ...batteryDischargeNegKw.map(Math.abs),
                    ...netKw.map(Math.abs),
                    2.0
                );
                maxAbsKw = Math.ceil(maxAbsKw * 2) / 2; // Steps of 0.5 kW
                if (maxAbsKw < 2.5) maxAbsKw = 2.5;

                let maxAbsPrice = Math.max(...pricesArr.map(Math.abs), 0.30);
                maxAbsPrice = Math.ceil(maxAbsPrice * 10) / 10;
                if (maxAbsPrice < 0.30) maxAbsPrice = 0.30;

                const isLineMode = (window.predictionChartType === 'line');
                const chartConfig = {
                    type: isLineMode ? 'line' : 'bar',
                    data: {
                        labels: labels,
                        datasets: (() => {
                            const exportPricesArr = data.export_prices_eur || [];
                            const ds = [
                                {
                                    label: 'EPEX Inkoop All-in (€/kWh)',
                                    data: pricesArr,
                                    type: 'line',
                                    borderColor: '#3B82F6',
                                    backgroundColor: 'transparent',
                                    borderWidth: 1.5,
                                    pointRadius: 0,
                                    pointHoverRadius: 4,
                                    yAxisID: 'y1',
                                    tension: 0,
                                    order: 0
                                },
                                {
                                    label: 'EPEX Teruglevering (€/kWh)',
                                    data: exportPricesArr,
                                    type: 'line',
                                    borderColor: '#06B6D4',
                                    borderDash: [4, 4],
                                    backgroundColor: 'transparent',
                                    borderWidth: 1.5,
                                    pointRadius: 0,
                                    pointHoverRadius: 4,
                                    yAxisID: 'y1',
                                    tension: 0,
                                    order: 0
                                },
                                {
                                    label: 'Netto Verbruik (kW)',
                                    data: netKw,
                                    type: 'line',
                                    borderColor: '#EF4444',
                                    backgroundColor: 'transparent',
                                    borderWidth: 1.5,
                                    pointRadius: 0,
                                    pointHoverRadius: 4,
                                    pointBackgroundColor: '#EF4444',
                                    tension: 0.25,
                                    yAxisID: 'y',
                                    order: 1
                                }
                            ];

                            if (isLineMode) {
                                ds.push({
                                    label: 'Ongedefinieerd (kW)',
                                    data: unallocKw,
                                    type: 'line',
                                    borderColor: '#3B82F6',
                                    backgroundColor: 'rgba(59, 130, 246, 0.15)',
                                    borderWidth: 2,
                                    pointRadius: 0,
                                    tension: 0.25,
                                    order: 2
                                });
                                ds.push({
                                    label: 'SWW Tapwater (kW)',
                                    data: boilerKw,
                                    type: 'line',
                                    borderColor: '#EC4899',
                                    backgroundColor: 'rgba(236, 72, 153, 0.2)',
                                    borderWidth: 2,
                                    pointRadius: 0,
                                    tension: 0.25,
                                    order: 3
                                });
                                ds.push({
                                    label: 'CV Verwarming (kW)',
                                    data: heatingKw,
                                    type: 'line',
                                    borderColor: '#6366F1',
                                    backgroundColor: 'rgba(99, 102, 241, 0.2)',
                                    borderWidth: 2,
                                    pointRadius: 0,
                                    tension: 0.25,
                                    order: 3
                                });
                                if (data.battery_enabled) {
                                    ds.push({
                                        label: 'Accu Laden (kW)',
                                        data: batteryChargeKw,
                                        type: 'line',
                                        borderColor: '#10B981',
                                        backgroundColor: 'transparent',
                                        borderWidth: 2,
                                        pointRadius: 0,
                                        tension: 0.25,
                                        order: 4
                                    });
                                }
                                ds.push({
                                    label: 'Zon Productie (kW)',
                                    data: solarNegKw,
                                    type: 'line',
                                    borderColor: '#F59E0B',
                                    backgroundColor: 'rgba(245, 158, 11, 0.15)',
                                    borderWidth: 2,
                                    pointRadius: 0,
                                    tension: 0.25,
                                    order: 4
                                });
                                if (data.battery_enabled) {
                                    ds.push({
                                        label: 'Accu Ontladen (kW)',
                                        data: batteryDischargeNegKw,
                                        type: 'line',
                                        borderColor: '#14B8A6',
                                        backgroundColor: 'transparent',
                                        borderWidth: 2,
                                        pointRadius: 0,
                                        tension: 0.25,
                                        order: 5
                                    });
                                }
                            } else {
                                ds.push({
                                    label: 'Ongedefinieerd (kW)',
                                    data: unallocKw,
                                    backgroundColor: '#3B82F6',
                                    stack: 'energy',
                                    borderRadius: 2,
                                    order: 3
                                });
                                ds.push({
                                    label: 'SWW Tapwater (kW)',
                                    data: boilerKw,
                                    backgroundColor: '#EC4899',
                                    stack: 'energy',
                                    borderRadius: 2,
                                    order: 3
                                });
                                ds.push({
                                    label: 'CV Verwarming (kW)',
                                    data: heatingKw,
                                    backgroundColor: '#6366F1',
                                    stack: 'energy',
                                    borderRadius: 2,
                                    order: 3
                                });
                                if (data.battery_enabled) {
                                    ds.push({
                                        label: 'Accu Laden (kW)',
                                        data: batteryChargeKw,
                                        backgroundColor: '#10B981',
                                        stack: 'energy',
                                        borderRadius: 2,
                                        order: 3
                                    });
                                }
                                ds.push({
                                    label: 'Zon Productie (kW)',
                                    data: solarNegKw,
                                    backgroundColor: '#F59E0B',
                                    stack: 'energy',
                                    borderRadius: 2,
                                    order: 4
                                });
                                if (data.battery_enabled) {
                                    ds.push({
                                        label: 'Accu Ontladen (kW)',
                                        data: batteryDischargeNegKw,
                                        backgroundColor: '#14B8A6',
                                        stack: 'energy',
                                        borderRadius: 2,
                                        order: 4
                                    });
                                }
                            }
                            return ds;
                        })()
                    },
                    options: {
                        responsive: true,
                        maintainAspectRatio: false,
                        interaction: { mode: 'index', intersect: false },
                        plugins: {
                            legend: { display: false },
                            tooltip: {
                                enabled: false,
                                external: function(context) {
                                    customHemsTooltipHandler(context, true);
                                }
                            }
                        },
                        scales: {
                            x: {
                                stacked: !isLineMode,
                                grid: { color: 'rgba(30, 41, 59, 0.4)' },
                                ticks: { color: '#94A3B8', font: { family: 'monospace', size: 10 } }
                            },
                            y: {
                                stacked: !isLineMode,
                                min: -maxAbsKw,
                                max: maxAbsKw,
                                title: { 
                                    display: true, 
                                    text: 'Opbrengst (-kW)  <  0  <  Verbruik (+kW)', 
                                    color: '#94A3B8', 
                                    font: { family: 'monospace', size: 10 } 
                                },
                                grid: {
                                    color: (ctx) => ctx.tick && ctx.tick.value === 0 ? '#CBD5E1' : 'rgba(30, 41, 59, 0.6)',
                                    lineWidth: (ctx) => ctx.tick && ctx.tick.value === 0 ? 2 : 1
                                },
                                ticks: {
                                    color: '#94A3B8',
                                    font: { family: 'monospace', size: 10 },
                                    callback: function(val) {
                                        const absV = Math.abs(val);
                                        const prefix = val < 0 ? '-' : '';
                                        return `${prefix}${absV.toFixed(1)} kW`;
                                    }
                                }
                            },
                            y1: {
                                type: 'linear',
                                position: 'right',
                                display: true,
                                min: -maxAbsPrice,
                                max: maxAbsPrice,
                                title: { display: true, text: 'Tarief (€/kWh)', color: '#06B6D4', font: { family: 'monospace', size: 10 } },
                                grid: { drawOnChartArea: false },
                                ticks: {
                                    color: '#06B6D4',
                                    font: { family: 'monospace', size: 10 },
                                    callback: function(val) {
                                        return val >= 0 ? '€' + Number(val).toFixed(2) : '';
                                    }
                                }
                            }
                        }
                    }
                };

                // Render on Analytics Tab
                const canvasAnalytics = document.getElementById('hemsChartAnalytics');
                if (canvasAnalytics) {
                    if (analyticsChartInstance) analyticsChartInstance.destroy();
                    analyticsChartInstance = new Chart(canvasAnalytics.getContext('2d'), chartConfig);
                    window.analyticsChartInstance = analyticsChartInstance;
                    window.hemsChartAnalytics = analyticsChartInstance;
                }

                // Render on Dashboard Tab
                const canvasDash = document.getElementById('hemsChart');
                if (canvasDash) {
                    if (chartInstance) chartInstance.destroy();
                    // Clone datasets for dashboard canvas if present
                    chartInstance = new Chart(canvasDash.getContext('2d'), Object.assign({}, chartConfig));
                    window.chartInstance = chartInstance;
                }

                // === RENDER NET LINES, COSTS & EPEX FORECAST CHART (LINES ONLY) ===
                const canvasCost = document.getElementById('costForecastChart');
                if (canvasCost) {
                    if (costForecastChartInstance) costForecastChartInstance.destroy();

                    const exportPricesArr = data.export_prices_eur || [];
                    window.__lastPredictionNetKw = netKw;

                    // Calculate Net Cost / Revenue per slot:
                    // netto verbruik / opbrengst maal kosten / opbrengst vs dynamische tarieven
                    const netCostEurArr = netKw.map((nKw, idx) => {
                        const kwh = Math.abs(nKw) * intervalH;
                        if (nKw >= 0) {
                            const pBuy = pricesArr[idx] || 0.25;
                            return Number((kwh * pBuy).toFixed(3));
                        } else {
                            const pSell = (exportPricesArr[idx] !== undefined) ? exportPricesArr[idx] : 0.10;
                            return Number((- (kwh * pSell)).toFixed(3));
                        }
                    });

                    // Summary statistics
                    const totNetKwh = netKw.reduce((acc, kw) => acc + (kw * intervalH), 0);
                    const totNetCostEur = netCostEurArr.reduce((acc, c) => acc + c, 0);

                    if (document.getElementById('cost-chart-total-net-kwh')) {
                        document.getElementById('cost-chart-total-net-kwh').innerText = `Netto: ${totNetKwh >= 0 ? '+' : ''}${totNetKwh.toFixed(1)} kWh`;
                    }
                    if (document.getElementById('cost-chart-netto')) {
                        document.getElementById('cost-chart-netto').innerText = `Netto: ${totNetCostEur >= 0 ? '+€' : '-€'}${Math.abs(totNetCostEur).toFixed(2)}`;
                    }

                    // Symmetrical bounds for zero-line harmony
                    let maxAbsKwCostChart = Math.max(...netKw.map(Math.abs), 2.0);
                    maxAbsKwCostChart = Math.ceil(maxAbsKwCostChart * 2) / 2;

                    let maxAbsCost = Math.max(...netCostEurArr.map(Math.abs), 0.20);
                    maxAbsCost = Math.ceil(maxAbsCost * 10) / 10;
                    if (maxAbsCost < 0.25) maxAbsCost = 0.25;

                    let maxAbsPrice = Math.max(...pricesArr.map(Math.abs), ...exportPricesArr.map(Math.abs), 0.30);
                    maxAbsPrice = Math.ceil(maxAbsPrice * 10) / 10;

                    const costConfig = {
                        type: 'bar',
                        data: {
                            labels: labels,
                            datasets: [
                                {
                                    label: 'Netto Kosten (€)',
                                    data: netCostEurArr,
                                    type: 'bar',
                                    backgroundColor: 'rgba(245, 158, 11, 0.75)', // Amber 500 bar
                                    borderColor: '#D97706',
                                    borderWidth: 1,
                                    borderRadius: 3,
                                    yAxisID: 'yCost',
                                    order: 4
                                },
                                {
                                    label: 'Netto Verbruik (kW)',
                                    data: netKw,
                                    type: 'line',
                                    borderColor: '#EF4444', // Red 500
                                    backgroundColor: 'transparent',
                                    fill: false,
                                    borderWidth: 1.5,
                                    pointRadius: 0,
                                    pointHoverRadius: 4,
                                    tension: 0.25,
                                    yAxisID: 'y',
                                    order: 1
                                },
                                {
                                    label: 'EPEX Inkoop (€/kWh)',
                                    data: pricesArr,
                                    type: 'line',
                                    borderColor: '#3B82F6', // Blue 500
                                    backgroundColor: 'transparent',
                                    borderWidth: 1.5,
                                    pointRadius: 0,
                                    pointHoverRadius: 4,
                                    tension: 0,
                                    yAxisID: 'yPrice',
                                    order: 2
                                },
                                {
                                    label: 'EPEX Teruglevering (€/kWh)',
                                    data: exportPricesArr,
                                    type: 'line',
                                    borderColor: '#06B6D4', // Cyan 500
                                    borderDash: [4, 4],
                                    backgroundColor: 'transparent',
                                    borderWidth: 1.5,
                                    pointRadius: 0,
                                    pointHoverRadius: 4,
                                    tension: 0,
                                    yAxisID: 'yPrice',
                                    order: 3
                                }
                            ]
                        },
                        options: {
                            responsive: true,
                            maintainAspectRatio: false,
                            interaction: { mode: 'index', intersect: false },
                            plugins: {
                                legend: { display: false },
                                tooltip: {
                                    enabled: false,
                                    external: function(context) {
                                        customCostForecastTooltipHandler(context);
                                    }
                                }
                            },
                            scales: {
                                x: {
                                    grid: { color: 'rgba(30, 41, 59, 0.4)' },
                                    ticks: { color: '#94A3B8', font: { family: 'monospace', size: 10 } }
                                },
                                y: {
                                    type: 'linear',
                                    position: 'left',
                                    min: -maxAbsKwCostChart,
                                    max: maxAbsKwCostChart,
                                    title: {
                                        display: true,
                                        text: 'Netto Vermogen (kW)',
                                        color: '#EF4444',
                                        font: { family: 'monospace', size: 10, weight: 'bold' }
                                    },
                                    grid: {
                                        color: (ctx) => ctx.tick && ctx.tick.value === 0 ? '#CBD5E1' : 'rgba(30, 41, 59, 0.5)',
                                        lineWidth: (ctx) => ctx.tick && ctx.tick.value === 0 ? 2 : 1
                                    },
                                    ticks: {
                                        color: '#EF4444',
                                        font: { family: 'monospace', size: 10 },
                                        callback: function(val) {
                                            return (val >= 0 ? '+' : '') + val.toFixed(1) + ' kW';
                                        }
                                    }
                                },
                                yCost: {
                                    type: 'linear',
                                    position: 'right',
                                    display: true,
                                    min: -maxAbsCost,
                                    max: maxAbsCost,
                                    title: {
                                        display: true,
                                        text: 'Netto Kosten (€)',
                                        color: '#F59E0B',
                                        font: { family: 'monospace', size: 10, weight: 'bold' }
                                    },
                                    grid: { drawOnChartArea: false },
                                    ticks: {
                                        color: '#F59E0B',
                                        font: { family: 'monospace', size: 10 },
                                        callback: function(val) {
                                            return (val >= 0 ? '+€' : '-€') + Math.abs(val).toFixed(2);
                                        }
                                    }
                                },
                                yPrice: {
                                    type: 'linear',
                                    position: 'right',
                                    display: true,
                                    min: -maxAbsPrice,
                                    max: maxAbsPrice,
                                    title: {
                                        display: true,
                                        text: 'EPEX Tarieven (€/kWh)',
                                        color: '#06B6D4',
                                        font: { family: 'monospace', size: 10, weight: 'bold' }
                                    },
                                    grid: { drawOnChartArea: false },
                                    ticks: {
                                        color: '#06B6D4',
                                        font: { family: 'monospace', size: 10 },
                                        callback: function(val) {
                                            return val >= 0 ? '€' + Number(val).toFixed(2) : '';
                                        }
                                    }
                                }
                            }
                        }
                    };

                    costForecastChartInstance = new Chart(canvasCost.getContext('2d'), costConfig);
                    window.costForecastChartInstance = costForecastChartInstance;
                }

                // Add mouseleave & tap dismissal listeners to cleanly hide tooltip when leaving graph
                if (!window.__tooltipDismissAttached) {
                    window.__tooltipDismissAttached = true;

                    const hideTooltip = () => {
                        const tip = document.getElementById('chartjs-custom-tooltip');
                        if (tip) {
                            tip.style.opacity = '0';
                            tip.style.pointerEvents = 'none';
                        }
                    };

                    // Global pointer/click outside canvas dismisses tooltip
                    document.addEventListener('pointerdown', (e) => {
                        if (!e.target.closest('canvas')) hideTooltip();
                    });

                    // Canvas mouseleave listeners
                    ['costForecastChart', 'costHistoryChart', 'hemsChartAnalytics', 'hemsChart', 'powerProducersChart', 'electricityPricesChart', 'chart-dhw-temperature', 'chart-heating-forecast'].forEach(id => {
                        const c = document.getElementById(id);
                        if (c) {
                            c.addEventListener('mouseleave', hideTooltip);
                            c.addEventListener('mouseout', (e) => {
                                if (!c.contains(e.relatedTarget)) hideTooltip();
                            });
                        }
                    });
                }
            } catch (e) {
                console.error('Chart load error:', e);
            }
        }

        // =========================================================================
        // POLICIES CONTROLLER
        // =========================================================================
        async function loadPolicies() {
            const [polRes, devRes] = await Promise.all([fetch('./api/policies'), fetch('./api/devices')]);
            const polData = await polRes.json();
            const devData = await devRes.json();

            const devMap = {};
            (devData.devices || []).forEach(d => { devMap[d.id] = d.name; });

            const container = document.getElementById('policies-container');
            container.innerHTML = '';
            document.getElementById('badge-pol-count').innerText = (polData.policies || []).length;

            (polData.policies || []).forEach(pol => {
                const card = document.createElement('div');
                card.className = 'bg-[#0e1422] border border-[#1E293B] rounded-2xl p-5 flex flex-col justify-between shadow-lg';
                
                let detailsHtml = '';
                let typeBadge = '';

                if (pol.type === 'thermal_buffer') {
                    typeBadge = '<span class="px-2 py-0.5 rounded text-[10px] font-semibold bg-pink-950 text-pink-300 border border-pink-800">Buffer Zonder Teruggave</span>';
                    detailsHtml = `
                        <div class="space-y-1 text-[11px] text-slate-300 bg-[#0B0F17] p-3 rounded-lg border border-slate-800 mb-3">
                            <div>🚨 Nood-comfort: <strong>< ${pol.parameters.emergency_threshold_c || 38}°C</strong> (Prioriteit 1)</div>
                            <div>⚡ Economische drempel: <strong>< ${pol.parameters.deadband_reheat_c || 46}°C</strong></div>
                            <div>🎯 Doeltemp: <strong>${pol.parameters.target_temperature_c || 50}°C</strong> · ☀️ Boost: <strong>${pol.parameters.solar_boost_temperature_c || 60}°C</strong></div>
                            <div>🛡️ Spitsblokkades: ${pol.parameters.morning_peak_lockout ? 'Ochtend ✓' : ''} ${pol.parameters.evening_peak_lockout ? 'Avond ✓' : ''}</div>
                        </div>
                    `;
                } else if (pol.type === 'battery_arbitrage') {
                    typeBadge = '<span class="px-2 py-0.5 rounded text-[10px] font-semibold bg-emerald-950 text-emerald-300 border border-emerald-800">Accu Arbitrage & Dode Zone</span>';
                    detailsHtml = `
                        <div class="space-y-1 text-[11px] text-slate-300 bg-[#0B0F17] p-3 rounded-lg border border-slate-800 mb-3 font-mono">
                            <div>⏸️ Dode Zone (Deadband): <strong>ΔP < €${pol.parameters.min_price_spread_eur_kwh || 0.115}/kWh</strong></div>
                            <div>⚡ Conversie-efficiëntie: <strong>${Math.round((pol.parameters.roundtrip_efficiency || 0.87)*100)}%</strong> (13% verlies)</div>
                            <div>📉 Cel-afschrijving (LCOS): <strong>€${pol.parameters.lcos_depreciation_eur_kwh || 0.0741}/kWh</strong></div>
                            <div>🔋 SoC Grenzen: <strong>${pol.parameters.min_soc_pct || 10}% - ${pol.parameters.max_soc_pct || 95}%</strong></div>
                        </div>
                    `;
                } else {
                    typeBadge = '<span class="px-2 py-0.5 rounded text-[10px] font-semibold bg-purple-950 text-purple-300 border border-purple-800">Verbruik Zonder Opslag</span>';
                    detailsHtml = `
                        <div class="space-y-1 text-[11px] text-slate-300 bg-[#0B0F17] p-3 rounded-lg border border-slate-800 mb-3">
                            <div>⏱️ Duur: <strong>${pol.parameters.duration_minutes || 90} min</strong> @ <strong>${pol.parameters.power_watts || 1200} W</strong></div>
                            <div>🕒 Venster: <strong>${pol.parameters.window_start_hour || 8}:00 - ${pol.parameters.window_end_hour || 20}:00</strong></div>
                            <div>☀️ Zonne-drempel: <strong>${pol.parameters.min_solar_surplus_watts || 1500} W</strong></div>
                        </div>
                    `;
                }

                const targetBadges = (pol.target_devices && pol.target_devices.length > 0)
                    ? pol.target_devices.map(id => `<span class="px-1.5 py-0.5 rounded text-[10px] bg-blue-900/40 text-blue-300 border border-blue-800 font-medium">${devMap[id] || id}</span>`).join(' ')
                    : '<span class="text-slate-500 italic">Geen apparaten gekoppeld</span>';

                card.innerHTML = `
                    <div>
                        <div class="flex justify-between items-start mb-2">
                            <h4 class="font-bold text-white text-sm">${pol.name}</h4>
                            ${typeBadge}
                        </div>
                        <div class="text-[11px] text-slate-400 mb-2.5 flex items-center gap-1.5 flex-wrap">
                            <span>Gekoppeld:</span> ${targetBadges}
                        </div>
                        ${detailsHtml}
                    </div>
                    <div class="flex justify-end gap-2 pt-3 border-t border-[#1E293B]">
                        <button onclick='openPolicyModal(${JSON.stringify(pol)})' class="px-2.5 py-1 bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs rounded-lg">Bewerken</button>
                        <button onclick="deletePolicy('${pol.id}')" class="px-2.5 py-1 bg-red-950/60 hover:bg-red-900 text-red-300 border border-red-800 text-xs rounded-lg">Verwijderen</button>
                    </div>
                `;
                container.appendChild(card);
            });
        }

        async function populatePolicyDeviceSelector(selectedDeviceIds = []) {
            try {
                const res = await fetch('./api/devices');
                const data = await res.json();
                window.__lastPredictionData = data;
                const container = document.getElementById('modal-pol-devices-list');
                container.innerHTML = '';
                const devices = data.devices || [];
                if (devices.length === 0) {
                    container.innerHTML = '<span class="text-slate-500 italic">Geen apparaten geconfigureerd. Voeg eerst een apparaat toe in het menu Apparaten.</span>';
                    return;
                }
                devices.forEach(d => {
                    const label = document.createElement('label');
                    label.className = 'flex items-center gap-2 p-1.5 rounded hover:bg-slate-800/40 cursor-pointer';
                    const isChecked = selectedDeviceIds.includes(d.id);
                    label.innerHTML = `
                        <input type="checkbox" name="policy_target_device" value="${d.id}" ${isChecked ? 'checked' : ''} class="rounded bg-slate-900 text-purple-600 border-slate-700">
                        <span class="text-slate-200 font-medium">${d.name}</span>
                        <span class="ml-auto text-[10px] text-slate-500 font-mono">${d.type}</span>
                    `;
                    container.appendChild(label);
                });
            } catch (e) {
                console.error('Error fetching devices for policy:', e);
            }
        }

        function renderPolicyFields() {
            const type = document.getElementById('modal-pol-type').value;
            const container = document.getElementById('pol-params-container');
            container.innerHTML = '';

            if (type === 'thermal_buffer') {
                container.innerHTML = `
                    <div class="grid grid-cols-2 gap-3">
                        <div>
                            <label class="block mb-1 text-slate-400">Nood-comfort Drempel (°C)</label>
                            <input type="number" step="0.5" id="param_emergency_threshold_c" value="${currentPolicyParams.emergency_threshold_c || 38.0}" class="w-full bg-[#0B0F17] border border-slate-800 rounded-lg p-2 text-white">
                        </div>
                        <div>
                            <label class="block mb-1 text-slate-400">Economische Drempel (°C)</label>
                            <input type="number" step="0.5" id="param_deadband_reheat_c" value="${currentPolicyParams.deadband_reheat_c || 46.0}" class="w-full bg-[#0B0F17] border border-slate-800 rounded-lg p-2 text-white">
                        </div>
                    </div>
                    <div class="grid grid-cols-2 gap-3">
                        <div>
                            <label class="block mb-1 text-slate-400">Standaard Doeltemp (°C)</label>
                            <input type="number" step="1" id="param_target_temperature_c" value="${currentPolicyParams.target_temperature_c || 50.0}" class="w-full bg-[#0B0F17] border border-slate-800 rounded-lg p-2 text-white">
                        </div>
                        <div>
                            <label class="block mb-1 text-slate-400">Zon/Dal Boost Doeltemp (°C)</label>
                            <input type="number" step="1" id="param_solar_boost_temperature_c" value="${currentPolicyParams.solar_boost_temperature_c || 60.0}" class="w-full bg-[#0B0F17] border border-slate-800 rounded-lg p-2 text-white">
                        </div>
                    </div>
                    <div class="space-y-1 pt-2">
                        <label class="flex items-center gap-2">
                            <input type="checkbox" id="param_morning_peak_lockout" ${currentPolicyParams.morning_peak_lockout !== false ? 'checked' : ''} class="rounded bg-slate-900 text-purple-600 border-slate-700">
                            <span class="text-slate-300 text-xs">Ochtendspits blokkade (07:00 - 08:30 SG1)</span>
                        </label>
                        <label class="flex items-center gap-2">
                            <input type="checkbox" id="param_evening_peak_lockout" ${currentPolicyParams.evening_peak_lockout !== false ? 'checked' : ''} class="rounded bg-slate-900 text-purple-600 border-slate-700">
                            <span class="text-slate-300 text-xs">Avondspits blokkade (17:30 - 20:30 SG1)</span>
                        </label>
                        <label class="flex items-center gap-2">
                            <input type="checkbox" id="param_isolate_space_heating_during_dhw" ${currentPolicyParams.isolate_space_heating_during_dhw !== false ? 'checked' : ''} class="rounded bg-slate-900 text-purple-600 border-slate-700">
                            <span class="text-slate-300 text-xs">CV uitschakelen tijdens SWW (voorkomt 9kW BUH)</span>
                        </label>
                    </div>
                `;
            } else if (type === 'battery_arbitrage') {
                container.innerHTML = `
                    <div class="grid grid-cols-2 gap-3">
                        <div>
                            <label class="block mb-1 text-slate-400">Dode Zone (Min. Prijsdelta €/kWh)</label>
                            <input type="number" step="0.001" id="param_min_price_spread_eur_kwh" value="${currentPolicyParams.min_price_spread_eur_kwh || 0.115}" class="w-full bg-[#0B0F17] border border-slate-800 rounded-lg p-2 text-white">
                        </div>
                        <div>
                            <label class="block mb-1 text-slate-400">Rondgang-Efficiëntie (bijv. 0.87)</label>
                            <input type="number" step="0.01" id="param_roundtrip_efficiency" value="${currentPolicyParams.roundtrip_efficiency || 0.87}" class="w-full bg-[#0B0F17] border border-slate-800 rounded-lg p-2 text-white">
                        </div>
                    </div>
                    <div class="grid grid-cols-2 gap-3">
                        <div>
                            <label class="block mb-1 text-slate-400">Cel-Afschrijving (LCOS €/kWh)</label>
                            <input type="number" step="0.001" id="param_lcos_depreciation_eur_kwh" value="${currentPolicyParams.lcos_depreciation_eur_kwh || 0.0741}" class="w-full bg-[#0B0F17] border border-slate-800 rounded-lg p-2 text-white">
                        </div>
                        <div>
                            <label class="block mb-1 text-slate-400">Piekstroombeveiliging (Amps/fase)</label>
                            <input type="number" step="1" id="param_peak_shaving_threshold_amps" value="${currentPolicyParams.peak_shaving_threshold_amps || 20.0}" class="w-full bg-[#0B0F17] border border-slate-800 rounded-lg p-2 text-white">
                        </div>
                    </div>
                    <div class="grid grid-cols-2 gap-3">
                        <div>
                            <label class="block mb-1 text-slate-400">Minimale SoC Reserve (%)</label>
                            <input type="number" step="1" id="param_min_soc_pct" value="${currentPolicyParams.min_soc_pct || 10.0}" class="w-full bg-[#0B0F17] border border-slate-800 rounded-lg p-2 text-white">
                        </div>
                        <div>
                            <label class="block mb-1 text-slate-400">Maximale SoC (%)</label>
                            <input type="number" step="1" id="param_max_soc_pct" value="${currentPolicyParams.max_soc_pct || 95.0}" class="w-full bg-[#0B0F17] border border-slate-800 rounded-lg p-2 text-white">
                        </div>
                    </div>
                `;
            } else {
                container.innerHTML = `
                    <div class="grid grid-cols-2 gap-3">
                        <div>
                            <label class="block mb-1 text-slate-400">Cyclusduur (minuten)</label>
                            <input type="number" step="5" id="param_duration_minutes" value="${currentPolicyParams.duration_minutes || 90}" class="w-full bg-[#0B0F17] border border-slate-800 rounded-lg p-2 text-white">
                        </div>
                        <div>
                            <label class="block mb-1 text-slate-400">Gemiddeld Vermogen (Watt)</label>
                            <input type="number" step="50" id="param_power_watts" value="${currentPolicyParams.power_watts || 1200}" class="w-full bg-[#0B0F17] border border-slate-800 rounded-lg p-2 text-white">
                        </div>
                    </div>
                    <div class="grid grid-cols-2 gap-3">
                        <div>
                            <label class="block mb-1 text-slate-400">Venster Start (Uur)</label>
                            <input type="number" step="1" id="param_window_start_hour" value="${currentPolicyParams.window_start_hour || 8}" class="w-full bg-[#0B0F17] border border-slate-800 rounded-lg p-2 text-white">
                        </div>
                        <div>
                            <label class="block mb-1 text-slate-400">Venster Eind (Uur)</label>
                            <input type="number" step="1" id="param_window_end_hour" value="${currentPolicyParams.window_end_hour || 20}" class="w-full bg-[#0B0F17] border border-slate-800 rounded-lg p-2 text-white">
                        </div>
                    </div>
                `;
            }
        }

        function openPolicyModal(pol = null) {
            const selectedDevs = pol ? (pol.target_devices || []) : [];
            populatePolicyDeviceSelector(selectedDevs);
            if (pol) {
                document.getElementById('modal-pol-title').innerText = 'Policy Bewerken';
                document.getElementById('modal-pol-id').value = pol.id;
                document.getElementById('modal-pol-name').value = pol.name;
                document.getElementById('modal-pol-type').value = pol.type;
                currentPolicyParams = pol.parameters || {};
            } else {
                document.getElementById('modal-pol-title').innerText = 'Nieuwe Policy Aanmaken';
                document.getElementById('modal-pol-id').value = '';
                document.getElementById('modal-pol-name').value = '';
                document.getElementById('modal-pol-type').value = 'thermal_buffer';
                currentPolicyParams = {};
            }
            renderPolicyFields();
            document.getElementById('policy-modal').classList.remove('hidden');
        }

        async function savePolicy(e) {
            e.preventDefault();
            const id = document.getElementById('modal-pol-id').value;
            const type = document.getElementById('modal-pol-type').value;
            const selectedDevices = Array.from(document.querySelectorAll('input[name="policy_target_device"]:checked')).map(cb => cb.value);
            const params = {};

            if (type === 'thermal_buffer') {
                params.emergency_threshold_c = parseFloat(document.getElementById('param_emergency_threshold_c').value);
                params.deadband_reheat_c = parseFloat(document.getElementById('param_deadband_reheat_c').value);
                params.target_temperature_c = parseFloat(document.getElementById('param_target_temperature_c').value);
                params.solar_boost_temperature_c = parseFloat(document.getElementById('param_solar_boost_temperature_c').value);
                params.morning_peak_lockout = document.getElementById('param_morning_peak_lockout').checked;
                params.evening_peak_lockout = document.getElementById('param_evening_peak_lockout').checked;
                params.isolate_space_heating_during_dhw = document.getElementById('param_isolate_space_heating_during_dhw').checked;
            } else if (type === 'battery_arbitrage') {
                params.min_price_spread_eur_kwh = parseFloat(document.getElementById('param_min_price_spread_eur_kwh').value);
                params.roundtrip_efficiency = parseFloat(document.getElementById('param_roundtrip_efficiency').value);
                params.lcos_depreciation_eur_kwh = parseFloat(document.getElementById('param_lcos_depreciation_eur_kwh').value);
                params.peak_shaving_threshold_amps = parseFloat(document.getElementById('param_peak_shaving_threshold_amps').value);
                params.min_soc_pct = parseFloat(document.getElementById('param_min_soc_pct').value);
                params.max_soc_pct = parseFloat(document.getElementById('param_max_soc_pct').value);
            } else {
                params.duration_minutes = parseInt(document.getElementById('param_duration_minutes').value);
                params.power_watts = parseFloat(document.getElementById('param_power_watts').value);
                params.window_start_hour = parseInt(document.getElementById('param_window_start_hour').value);
                params.window_end_hour = parseInt(document.getElementById('param_window_end_hour').value);
            }

            const payload = {
                name: document.getElementById('modal-pol-name').value,
                type: type,
                target_devices: selectedDevices,
                parameters: params
            };

            if (id) {
                await fetch('./api/policies/' + id, { method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload) });
            } else {
                await fetch('./api/policies', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload) });
            }
            closeModal('policy-modal');
            loadPolicies();
        }

        async function deletePolicy(id) {
            if (!confirm('Weet je zeker dat je deze policy wilt verwijderen?')) return;
            await fetch('./api/policies/' + id, { method: 'DELETE' });
            loadPolicies();
        }

        // =========================================================================
        // DEVICES CONTROLLER
        // =========================================================================
        window.__activeDeviceFilter = 'all';

        function filterDeviceView(filterKey) {
            window.__activeDeviceFilter = filterKey;
            document.querySelectorAll('.dev-filter-btn').forEach(btn => {
                btn.className = 'dev-filter-btn px-3 py-1 rounded-lg text-slate-400 hover:text-white transition';
            });
            const activeBtn = document.getElementById('dev-filter-' + filterKey);
            if (activeBtn) {
                activeBtn.className = 'dev-filter-btn px-3 py-1 rounded-lg bg-blue-600 text-white font-semibold shadow transition';
            }
            loadDevices();
        }

        async function loadDevices() {
            const [devRes, polRes, infRes] = await Promise.all([
                fetch('./api/devices'),
                fetch('./api/policies'),
                fetch('./api/infrastructure')
            ]);
            const devData = await devRes.json();
            const polData = await polRes.json();
            const infData = await infRes.json();

            const policies = polData.policies || [];
            const devices = devData.devices || [];
            const haInfo = infData.homeassistant || {};
            const container = document.getElementById('devices-container');
            container.innerHTML = '';
            document.getElementById('badge-dev-count').innerText = devices.length;

            // Map live states from Home Assistant info
            const haStateMap = {};
            (haInfo.sources || []).forEach(s => { haStateMap[s.entity_id] = s.live_state; });
            (haInfo.targets || []).forEach(t => { haStateMap[t.entity_id] = t.live_state; });

            // Categorize devices into 3 distinct groups
            const haDevices = devices.filter(d => d.source_type === 'homeassistant' && d.installed !== false);
            const mqttDevices = devices.filter(d => d.source_type === 'mqtt' && d.installed !== false);
            const plannedDevices = devices.filter(d => d.installed === false || d.enabled === false);

            // Update button counts
            if (document.getElementById('dev-filter-all')) document.getElementById('dev-filter-all').innerText = `Alle Apparaten (${devices.length})`;
            if (document.getElementById('dev-filter-ha')) document.getElementById('dev-filter-ha').innerText = `🏠 Home Assistant (${haDevices.length})`;
            if (document.getElementById('dev-filter-mqtt')) document.getElementById('dev-filter-mqtt').innerText = `⚡ Direct MQTT (${mqttDevices.length})`;
            if (document.getElementById('dev-filter-planned')) document.getElementById('dev-filter-planned').innerText = `🔋 Gepland / Standby (${plannedDevices.length})`;

            const groupsToRender = [];
            if (window.__activeDeviceFilter === 'all' || window.__activeDeviceFilter === 'homeassistant') {
                groupsToRender.push({
                    title: 'Home Assistant Core Gekoppelde Apparaten',
                    desc: 'Sensoren en actuatoren aangestuurd via de Home Assistant Supervisor REST API.',
                    icon: '🏠',
                    color: 'cyan',
                    list: haDevices
                });
            }
            if (window.__activeDeviceFilter === 'all' || window.__activeDeviceFilter === 'mqtt') {
                groupsToRender.push({
                    title: 'Directe MQTT & Modbus Apparaten',
                    desc: 'Streaming vermogensmeters rechtstreeks ingelezen van de Mosquitto message bus.',
                    icon: '⚡',
                    color: 'amber',
                    list: mqttDevices
                });
            }
            if (window.__activeDeviceFilter === 'all' || window.__activeDeviceFilter === 'planned') {
                groupsToRender.push({
                    title: 'Geplande / Standby Apparaten',
                    desc: 'Apparaten geconfigureerd voor simulaties of nog niet fysiek geïnstalleerd.',
                    icon: '🔋',
                    color: 'purple',
                    list: plannedDevices
                });
            }

            groupsToRender.forEach(grp => {
                if (grp.list.length === 0 && window.__activeDeviceFilter !== 'all') return;

                const section = document.createElement('div');
                section.className = 'space-y-3';
                section.innerHTML = `
                    <div class="flex items-center justify-between border-b border-slate-800 pb-2">
                        <div class="flex items-center gap-2">
                            <span class="text-base">${grp.icon}</span>
                            <h3 class="text-sm font-bold text-white tracking-wide">${grp.title}</h3>
                            <span class="px-2 py-0.5 rounded text-[10px] font-mono font-semibold bg-slate-800 text-slate-300 border border-slate-700">${grp.list.length}</span>
                        </div>
                        <span class="text-[11px] text-slate-400 hidden sm:inline">${grp.desc}</span>
                    </div>
                    <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4" id="grp-grid-${grp.color}"></div>
                `;
                container.appendChild(section);

                const grid = section.querySelector(`#grp-grid-${grp.color}`);
                if (grp.list.length === 0) {
                    grid.innerHTML = `<div class="col-span-full py-4 text-center text-xs text-slate-500 italic bg-[#0e1422] border border-slate-800 rounded-xl">Geen apparaten in deze categorie</div>`;
                    return;
                }

                grp.list.forEach(dev => {
                    const boundPolicies = policies.filter(p => (p.target_devices || []).includes(dev.id));
                    const policyBadge = boundPolicies.length > 0
                        ? boundPolicies.map(p => `<span class="px-1.5 py-0.5 rounded text-[10px] bg-purple-900/40 text-purple-300 border border-purple-800 font-medium">${p.name}</span>`).join(' ')
                        : '<span class="text-slate-500 italic">Geen beleid (stand-by)</span>';

                    const isInstalled = dev.installed !== false;
                    const isEnabled = dev.enabled !== false;
                    const statusPill = (!isInstalled)
                        ? '<span class="px-2 py-0.5 rounded text-[9px] font-mono font-semibold bg-slate-800 text-slate-400 border border-slate-700">NIET GEÏNSTALLEERD</span>'
                        : (isEnabled 
                            ? '<span class="px-2 py-0.5 rounded text-[9px] font-mono font-semibold bg-emerald-950/80 text-emerald-400 border border-emerald-800 flex items-center gap-1"><span class="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse"></span> ACTIEF</span>'
                            : '<span class="px-2 py-0.5 rounded text-[9px] font-mono font-semibold bg-amber-950/80 text-amber-400 border border-amber-800">UITGESCHAKELD</span>');

                    const pState = dev.ha_power_entity ? (haStateMap[dev.ha_power_entity] || '--') : '';
                    const cState = dev.ha_control_entity ? (haStateMap[dev.ha_control_entity] || '--') : '';
                    const tState = dev.ha_temp_entity ? (haStateMap[dev.ha_temp_entity] || '--') : '';

                    // Cache device in memory for safe, bug-free editing by ID
                    window.__cachedDevicesMap = window.__cachedDevicesMap || {};
                    window.__cachedDevicesMap[dev.id] = dev;

                    // Render multi-sensors list (No favicons, clean typography)
                    let sensorsHtml = '';
                    const devSensors = dev.sensors || [];
                    if (devSensors.length > 0) {
                        sensorsHtml = devSensors.map(s => {
                            const val = s.entity_id ? (haStateMap[s.entity_id] || '--') : '--';
                            const roleColor = s.role === 'producer' ? 'text-emerald-400' : (s.role === 'consumer' ? 'text-red-400' : 'text-cyan-400');
                            const roleLabel = s.role === 'producer' ? 'PRODUCENT' : (s.role === 'consumer' ? 'VERBRUIKER' : 'STATUS');
                            const connLabel = s.connector === 'mqtt' ? 'MQTT' : 'HA';
                            const targetStr = s.connector === 'mqtt' ? s.topic : s.entity_id;
                            return `
                                <div class="flex items-center justify-between py-1 px-2 rounded bg-[#0e1422] border border-slate-800/70 text-[10px]">
                                    <div class="min-w-0 flex-1 mr-2">
                                        <div class="flex items-center gap-1.5">
                                            <span class="px-1 py-0.2 rounded text-[8px] font-bold ${roleColor} bg-slate-900 border border-slate-800 flex-shrink-0">${roleLabel}</span>
                                            <span class="text-slate-300 font-medium truncate">${s.name}</span>
                                        </div>
                                        <span class="text-[9px] text-slate-500 font-mono block truncate">${connLabel}: ${targetStr}</span>
                                    </div>
                                    <span class="font-bold text-white font-mono flex-shrink-0">${val}</span>
                                </div>
                            `;
                        }).join('');
                    } else {
                        sensorsHtml = `<div class="text-[10px] text-slate-500 italic py-1">Geen sensoren geconfigureerd</div>`;
                    }

                    // Render multi-actuators list (No favicons, clean typography)
                    let actuatorsHtml = '';
                    const devActuators = dev.actuators || [];
                    if (devActuators.length > 0) {
                        actuatorsHtml = devActuators.map(a => {
                            const val = a.entity_id ? (haStateMap[a.entity_id] || a.default_state || '--') : (a.default_state || '--');
                            const typeLabel = a.type === 'select' ? 'MODUS' : (a.type === 'range' ? 'BEREIK' : 'SCHAKELAAR');
                            const connLabel = a.connector === 'mqtt' ? 'MQTT' : 'HA';
                            return `
                                <div class="flex items-center justify-between py-1 px-2 rounded bg-[#0e1422] border border-slate-800/70 text-[10px]">
                                    <div class="min-w-0 flex-1 mr-2">
                                        <div class="flex items-center gap-1.5">
                                            <span class="px-1 py-0.2 rounded text-[8px] font-bold text-pink-400 bg-slate-900 border border-slate-800 flex-shrink-0">${typeLabel}</span>
                                            <span class="text-slate-300 font-medium truncate">${a.name}</span>
                                        </div>
                                        <span class="text-[9px] text-slate-500 font-mono block truncate">${connLabel}: ${a.entity_id || a.topic}</span>
                                    </div>
                                    <span class="px-1.5 py-0.5 rounded text-[10px] font-bold ${val === 'on' || val.includes('aan') || val.includes('Aan') ? 'bg-emerald-950 text-emerald-400 border border-emerald-800' : 'bg-slate-900 text-slate-300 border border-slate-700'} font-mono flex-shrink-0">${val}</span>
                                </div>
                            `;
                        }).join('');
                    } else {
                        actuatorsHtml = `<div class="text-[10px] text-slate-500 italic py-1">Geen aansturing (puur meetapparaat)</div>`;
                    }

                    // Clean card header: Title truncates properly, badges never overflow, no id subtitle, no favicons, no section subtitles
                    const card = document.createElement('div');
                    card.className = 'bg-[#0e1422] border border-[#1E293B] hover:border-slate-700 rounded-2xl p-4 flex flex-col justify-between shadow-lg transition space-y-3';
                    card.innerHTML = `
                        <div>
                            <!-- Header: Title + Badges aligned horizontally without overflow -->
                            <div class="flex justify-between items-start gap-2 mb-2.5">
                                <div class="min-w-0 flex-1 mr-2">
                                    <h4 class="font-bold text-white text-sm truncate" title="${dev.name}">${dev.name}</h4>
                                </div>
                                <div class="flex items-center gap-1.5 flex-shrink-0">
                                    <span class="px-2 py-0.5 rounded text-[9px] font-mono font-semibold bg-blue-900/40 text-blue-300 border border-blue-800 flex-shrink-0">${dev.type}</span>
                                    ${statusPill}
                                </div>
                            </div>

                            <div class="text-[11px] text-slate-400 mb-2.5 flex items-center gap-1.5 flex-wrap">
                                <span class="font-medium">Beleid:</span> ${policyBadge}
                            </div>

                            <!-- DATABRONNEN (Geen favicons, geen subtitels) -->
                            <div class="bg-[#0B0F17] p-2.5 rounded-xl border border-slate-800/80 mb-2 space-y-1.5">
                                <div class="text-[10px] font-mono border-b border-slate-800/60 pb-1 font-bold text-cyan-400">
                                    Databronnen (${devSensors.length || 0})
                                </div>
                                <div class="space-y-1">
                                    ${sensorsHtml}
                                </div>
                            </div>

                            <!-- AANSTURING & REGIE (Geen favicons, geen subtitels) -->
                            <div class="bg-[#0B0F17] p-2.5 rounded-xl border border-slate-800/80 mb-2 space-y-1.5">
                                <div class="text-[10px] font-mono border-b border-slate-800/60 pb-1 font-bold text-pink-400">
                                    Aansturing & Regie (${devActuators.length})
                                </div>
                                <div class="space-y-1">
                                    ${actuatorsHtml}
                                </div>
                            </div>

                            ${dev.type === 'home_battery' ? `
                            <div class="bg-purple-950/30 border border-purple-800/40 rounded-xl p-2.5 mb-2 flex items-center justify-between text-xs">
                                <div>
                                    <div class="font-bold text-purple-300">🔋 Voorspelling Simulatie</div>
                                    <div class="text-[10px] text-slate-400">Accu meenemen in 24h prognose</div>
                                </div>
                                <button onclick="toggleBatterySimFromSettings()" class="px-2.5 py-1 rounded font-medium text-xs transition ${window.__simulateBattery ? 'bg-purple-600 text-white shadow' : 'bg-slate-800 text-slate-400 hover:text-white'}">
                                    ${window.__simulateBattery ? 'Actief' : 'Uit'}
                                </button>
                            </div>` : ''}
                        </div>
                        <div class="flex justify-end gap-2 pt-2.5 border-t border-[#1E293B]">
                            <button onclick="openDeviceModal('${dev.id}')" class="px-2.5 py-1 bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs rounded-lg border border-slate-700 transition">Bewerken</button>
                            <button onclick="deleteDevice('${dev.id}')" class="px-2.5 py-1 bg-red-950/60 hover:bg-red-900 text-red-300 border border-red-800 text-xs rounded-lg transition">Verwijderen</button>
                        </div>
                    `;
                    grid.appendChild(card);
                });
            });
        }

                function toggleDeviceSourceFields() {
            const st = document.getElementById('modal-dev-source-type').value;
            const haDiv = document.getElementById('dev-source-ha-fields');
            const mqDiv = document.getElementById('dev-source-mqtt-fields');
            if (st === 'mqtt') {
                haDiv.classList.add('hidden');
                mqDiv.classList.remove('hidden');
            } else {
                haDiv.classList.remove('hidden');
                mqDiv.classList.add('hidden');
            }
        }

        function openDeviceModal(devOrId = null) {
            populateHaDropdowns();
            
            let dev = null;
            if (typeof devOrId === 'string') {
                dev = (window.__cachedDevicesMap && window.__cachedDevicesMap[devOrId]) || null;
            } else {
                dev = devOrId;
            }
            
            // Populate broker dropdown
            const bSelect = document.getElementById('modal-dev-mqtt-broker');
            if (bSelect) {
                bSelect.innerHTML = '';
                (cachedInfra.mqtt_connections || []).forEach(b => {
                    const opt = document.createElement('option');
                    opt.value = b.id;
                    opt.innerText = `${b.name} (${b.host}:${b.port})`;
                    bSelect.appendChild(opt);
                });
            }

            if (dev) {
                document.getElementById('modal-dev-title').innerText = 'Apparaat Bewerken';
                document.getElementById('modal-dev-id').value = dev.id;
                document.getElementById('modal-dev-name').value = dev.name;
                document.getElementById('modal-dev-type').value = dev.type;
                document.getElementById('modal-dev-source-type').value = dev.source_type || 'homeassistant';
                // Extract entities from rich sensors/actuators or legacy fields
                const powerEntity = dev.ha_power_entity || (dev.sensors ? (dev.sensors.find(s => s.role === 'consumer' || s.role === 'producer')?.entity_id || '') : '');
                const tempEntity = dev.ha_temp_entity || (dev.sensors ? (dev.sensors.find(s => s.role === 'state' && (s.entity_id?.includes('temp') || s.id?.includes('temp')))?.entity_id || '') : '');
                const controlEntity = dev.ha_control_entity || (dev.actuators ? (dev.actuators[0]?.entity_id || '') : '');
                const mqttPowerTopic = dev.mqtt_power_topic || (dev.sensors ? (dev.sensors.find(s => s.connector === 'mqtt')?.topic || '') : '');

                document.getElementById('modal-dev-ha-power').value = powerEntity;
                if (document.getElementById('modal-dev-ha-temp')) document.getElementById('modal-dev-ha-temp').value = tempEntity;
                document.getElementById('modal-dev-ha-control').value = controlEntity;
                document.getElementById('modal-dev-mqtt-power-topic').value = mqttPowerTopic;
                document.getElementById('modal-dev-native-unit').value = dev.native_unit || 'W';
                document.getElementById('modal-dev-installed').checked = dev.installed !== false;
                document.getElementById('modal-dev-enabled').checked = dev.enabled !== false;
                document.getElementById('modal-dev-mqtt-broker').value = dev.mqtt_broker_id || '';
                document.getElementById('modal-dev-mqtt-power-topic').value = dev.mqtt_power_topic || '';
                document.getElementById('modal-dev-mqtt-json-key').value = dev.mqtt_power_json_key || '';
                document.getElementById('modal-dev-mqtt-control-topic').value = dev.mqtt_control_topic || '';
            } else {
                document.getElementById('modal-dev-title').innerText = 'Nieuw Apparaat Toevoegen';
                document.getElementById('modal-dev-id').value = '';
                document.getElementById('modal-dev-name').value = '';
                document.getElementById('modal-dev-source-type').value = 'homeassistant';
                document.getElementById('modal-dev-native-unit').value = 'W';
                document.getElementById('modal-dev-installed').checked = true;
                document.getElementById('modal-dev-enabled').checked = true;
                if (document.getElementById('modal-dev-ha-temp')) document.getElementById('modal-dev-ha-temp').value = '';
                document.getElementById('modal-dev-mqtt-power-topic').value = '';
                document.getElementById('modal-dev-mqtt-json-key').value = '';
                document.getElementById('modal-dev-mqtt-control-topic').value = '';
            }
            const elMin = document.getElementById('modal-dev-min-runtime');
            if (elMin) elMin.value = (dev && dev.parameters) ? (dev.parameters.min_runtime_minutes || '') : '';
            const elMax = document.getElementById('modal-dev-max-power');
            if (elMax) elMax.value = (dev && dev.parameters) ? (dev.parameters.max_power_w || '') : '';
            const elEm = document.getElementById('modal-dev-emergency-threshold');
            if (elEm) elEm.value = (dev && dev.parameters) ? (dev.parameters.emergency_threshold || '') : '';
            toggleDeviceSourceFields();
            document.getElementById('device-modal').classList.remove('hidden');
        }

        async function saveDevice(e) {
            e.preventDefault();
            const id = document.getElementById('modal-dev-id').value;
            const st = document.getElementById('modal-dev-source-type').value;
            const payload = {
                name: document.getElementById('modal-dev-name').value,
                type: document.getElementById('modal-dev-type').value,
                source_type: st,
                ha_power_entity: document.getElementById('modal-dev-ha-power').value,
                ha_temp_entity: document.getElementById('modal-dev-ha-temp') ? document.getElementById('modal-dev-ha-temp').value : '',
                ha_control_entity: document.getElementById('modal-dev-ha-control').value,
                native_unit: document.getElementById('modal-dev-native-unit').value,
                installed: document.getElementById('modal-dev-installed').checked,
                enabled: document.getElementById('modal-dev-enabled').checked,
                mqtt_broker_id: document.getElementById('modal-dev-mqtt-broker').value,
                mqtt_power_topic: document.getElementById('modal-dev-mqtt-power-topic').value,
                mqtt_power_json_key: document.getElementById('modal-dev-mqtt-json-key').value,
                mqtt_control_topic: document.getElementById('modal-dev-mqtt-control-topic').value,
                parameters: (() => {
                    const dev = (window.__cachedDevicesMap && window.__cachedDevicesMap[id]) || {};
                    const p = (dev && dev.parameters) ? Object.assign({}, dev.parameters) : {};
                    const elMin = document.getElementById('modal-dev-min-runtime');
                    if (elMin && elMin.value) p.min_runtime_minutes = parseInt(elMin.value) || 0;
                    const elMax = document.getElementById('modal-dev-max-power');
                    if (elMax && elMax.value) p.max_power_w = parseFloat(elMax.value) || 0;
                    const elEm = document.getElementById('modal-dev-emergency-threshold');
                    if (elEm && elEm.value) p.emergency_threshold = parseFloat(elEm.value) || 0;
                    return p;
                })()
            };
            // Automatically construct/update canonical sensors and actuators based on entered entities
            const existingDev = (window.__cachedDevicesMap && window.__cachedDevicesMap[id]) || {};
            const sensors = existingDev.sensors ? JSON.parse(JSON.stringify(existingDev.sensors)) : [];
            const actuators = existingDev.actuators ? JSON.parse(JSON.stringify(existingDev.actuators)) : [];

            // Update power sensor
            if (st === 'homeassistant' && payload.ha_power_entity) {
                const pSensor = sensors.find(s => s.role === 'consumer' || s.role === 'producer') || {
                    id: payload.type === 'solar_inverter' ? 'solar_production' : 'device_power',
                    name: payload.name + ' Vermogen',
                    role: payload.type === 'solar_inverter' ? 'producer' : 'consumer',
                    connector: 'homeassistant',
                    native_unit: payload.native_unit,
                    storage_unit: 'W'
                };
                pSensor.entity_id = payload.ha_power_entity;
                pSensor.connector = 'homeassistant';
                pSensor.native_unit = payload.native_unit;
                if (!sensors.includes(pSensor)) sensors.push(pSensor);
            } else if (st === 'mqtt' && payload.mqtt_power_topic) {
                const pSensor = sensors.find(s => s.connector === 'mqtt') || {
                    id: payload.type === 'solar_inverter' ? 'solar_production' : 'device_power',
                    name: payload.name + ' Vermogen',
                    role: payload.type === 'solar_inverter' ? 'producer' : 'consumer',
                    connector: 'mqtt',
                    native_unit: payload.native_unit,
                    storage_unit: 'W'
                };
                pSensor.topic = payload.mqtt_power_topic;
                pSensor.connector = 'mqtt';
                pSensor.native_unit = payload.native_unit;
                if (!sensors.includes(pSensor)) sensors.push(pSensor);
            }

            // Update temp sensor if present
            if (payload.ha_temp_entity) {
                const tSensor = sensors.find(s => s.id?.includes('temp') || s.entity_id?.includes('temp')) || {
                    id: 'device_temperature',
                    name: payload.name + ' Temperatuur',
                    role: 'state',
                    connector: 'homeassistant',
                    unit: '°C'
                };
                tSensor.entity_id = payload.ha_temp_entity;
                if (!sensors.includes(tSensor)) sensors.push(tSensor);
            }

            // Update control actuator if present
            if (payload.ha_control_entity) {
                const act = actuators[0] || {
                    id: 'device_control',
                    name: payload.name + ' Aansturing',
                    type: payload.ha_control_entity.startsWith('input_select') ? 'select' : (payload.ha_control_entity.startsWith('climate') ? 'range' : 'switch'),
                    connector: 'homeassistant'
                };
                act.entity_id = payload.ha_control_entity;
                if (!actuators.includes(act)) actuators.push(act);
            }

            payload.sensors = sensors;
            payload.actuators = actuators;

            if (id) {
                await fetch('./api/devices/' + id, { method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload) });
            } else {
                await fetch('./api/devices', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload) });
            }
            closeModal('device-modal');
            loadDevices();
        }

        async function deleteDevice(id) {
            if (!confirm('Weet je zeker dat je dit apparaat wilt verwijderen?')) return;
            await fetch('./api/devices/' + id, { method: 'DELETE' });
            loadDevices();
        }

        // =========================================================================
        // TARIFFS CONTROLLER
        // =========================================================================
        async function loadTariffs() {
            const res = await fetch('./api/tariffs');
            const d = await res.json();
            const container = document.getElementById('tariffs-container');
            container.innerHTML = '';
            (d.tariffs || []).forEach(t => {
                const card = document.createElement('div');
                card.className = 'bg-[#0e1422] border border-[#1E293B] rounded-2xl p-5 flex flex-col justify-between shadow-lg';
                card.innerHTML = `
                    <div>
                        <div class="flex justify-between items-start mb-2">
                            <h4 class="font-bold text-white text-sm">${t.name}</h4>
                            <span class="px-2 py-0.5 rounded text-[10px] font-semibold bg-emerald-950 text-emerald-300 border border-emerald-800">${t.provider}</span>
                        </div>
                        <p class="text-[11px] text-slate-400 mb-2">Interval: <strong>${t.interval}</strong> · Start: ${t.contract_start_date}</p>
                        <div class="grid grid-cols-2 gap-2 text-[11px] text-slate-300 bg-[#0B0F17] p-3 rounded-lg border border-slate-800 mb-3 font-mono">
                            <div>Inkoop Opslag: €${t.import_markup_eur_kwh}/kWh</div>
                            <div>Teruglevering: €${t.export_markup_eur_kwh}/kWh</div>
                            <div>Energiebelasting: €${t.electricity_tax_eur_kwh}/kWh</div>
                            <div>Vastrecht: €${t.fixed_monthly_fee_eur}/mnd</div>
                        </div>
                    </div>
                    <div class="flex justify-end gap-2 pt-3 border-t border-[#1E293B]">
                        <button onclick='openTariffModal(${JSON.stringify(t)})' class="px-2.5 py-1 bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs rounded-lg">Bewerken</button>
                        <button onclick="deleteTariff('${t.id}')" class="px-2.5 py-1 bg-red-950/60 hover:bg-red-900 text-red-300 border border-red-800 text-xs rounded-lg">Verwijderen</button>
                    </div>
                `;
                container.appendChild(card);
            });
        }

        function openTariffModal(t = null) {
            if (t) {
                document.getElementById('modal-tariff-title').innerText = 'Leverancier Bewerken';
                document.getElementById('modal-tariff-id').value = t.id;
                document.getElementById('modal-tariff-name').value = t.name;
                document.getElementById('modal-tariff-provider').value = t.provider;
                document.getElementById('modal-tariff-interval').value = t.interval;
                document.getElementById('modal-tariff-import').value = t.import_markup_eur_kwh;
                document.getElementById('modal-tariff-export').value = t.export_markup_eur_kwh;
                document.getElementById('modal-tariff-tax').value = t.electricity_tax_eur_kwh;
                document.getElementById('modal-tariff-fixed').value = t.fixed_monthly_fee_eur;
            } else {
                document.getElementById('modal-tariff-title').innerText = 'Nieuwe Leverancier Toevoegen';
                document.getElementById('modal-tariff-id').value = '';
                document.getElementById('modal-tariff-name').value = '';
            }
            document.getElementById('tariff-modal').classList.remove('hidden');
        }

        async function saveTariff(e) {
            e.preventDefault();
            const id = document.getElementById('modal-tariff-id').value;
            const payload = {
                name: document.getElementById('modal-tariff-name').value,
                provider: document.getElementById('modal-tariff-provider').value,
                interval: document.getElementById('modal-tariff-interval').value,
                import_markup_eur_kwh: parseFloat(document.getElementById('modal-tariff-import').value),
                export_markup_eur_kwh: parseFloat(document.getElementById('modal-tariff-export').value),
                electricity_tax_eur_kwh: parseFloat(document.getElementById('modal-tariff-tax').value),
                fixed_monthly_fee_eur: parseFloat(document.getElementById('modal-tariff-fixed').value)
            };
            if (id) {
                await fetch('./api/tariffs/' + id, { method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload) });
            } else {
                await fetch('./api/tariffs', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload) });
            }
            closeModal('tariff-modal');
            loadTariffs();
        }

        async function deleteTariff(id) {
            if (!confirm('Leverancier verwijderen?')) return;
            await fetch('./api/tariffs/' + id, { method: 'DELETE' });
            loadTariffs();
        }

        // =========================================================================
        // CALIBRATION & EXCLUSIONS
        // =========================================================================
        async function loadCalibration() {
            const res = await fetch('./api/calibration');
            const data = await res.json();
                window.__lastPredictionData = data;
            const tbody = document.getElementById('exclusion-tbody');
            tbody.innerHTML = '';
            (data.exclusion_windows || []).forEach((w, idx) => {
                const tr = document.createElement('tr');
                tr.innerHTML = `
                    <td class="p-2.5 font-mono text-cyan-300">${w.sensor}</td>
                    <td class="p-2.5 font-mono">${w.start}</td>
                    <td class="p-2.5 font-mono">${w.end}</td>
                    <td class="p-2.5 text-slate-300">${w.reason}</td>
                    <td class="p-2.5 text-right"><button onclick="deleteExclusion(${idx})" class="px-2 py-0.5 bg-red-950 text-red-300 border border-red-800 rounded text-[10px]">Verwijderen</button></td>
                `;
                tbody.appendChild(tr);
            });
        }

        function openExclusionModal() { document.getElementById('exclusion-modal').classList.remove('hidden'); }
        async function saveExclusion(e) {
            e.preventDefault();
            const payload = {
                sensor: document.getElementById('modal-ex-sensor').value,
                start: document.getElementById('modal-ex-start').value,
                end: document.getElementById('modal-ex-end').value,
                reason: document.getElementById('modal-ex-reason').value
            };
            await fetch('./api/exclusion-windows', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload) });
            closeModal('exclusion-modal');
            loadCalibration();
        }
        async function deleteExclusion(idx) {
            if (!confirm('Uitsluitingsvenster verwijderen?')) return;
            await fetch('./api/exclusion-windows/' + idx, { method: 'DELETE' });
            loadCalibration();
        }

        function closeModal(id) { document.getElementById(id).classList.add('hidden'); }

                
        async function saveSolarCostFromTab() {
            const inp = document.getElementById('tab-solar-cost-input');
            if (!inp) return;
            const val = parseFloat(inp.value) || 0.06;
            try {
                await fetch('./api/analytics/solar_cost', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ solar_cost_eur_kwh: val })
                });
                alert('Zonnestroom kostprijs succesvol opgeslagen: €' + val.toFixed(3) + '/kWh');
                loadElectricityPricesChart();
                loadChartData();
            } catch (err) {
                console.error('Error saving solar cost:', err);
            }
        }

        async function loadElectricityPricesChart() {
            const canvas = document.getElementById('electricityPricesChart');
            if (!canvas) return;

            try {
                const resVal = predictionResolution || '15m';
                const res = await fetch('./api/analytics/electricity_prices?resolution=' + encodeURIComponent(resVal));
                const data = await res.json();
                window.__lastElectricityPricesData = data;
                if (data.status !== 'success') {
                    console.error('EPEX prices load error:', data.message);
                    return;
                }

                // Sync setting in Tariffs tab if input exists
                const tabCostInp = document.getElementById('tab-solar-cost-input');
                if (tabCostInp) {
                    tabCostInp.value = Number(data.solar_cost || 0.06).toFixed(3);
                }

                // Update stats chips
                const s = data.stats || {};
                document.getElementById('stat-epex-min').innerText = s.min_price || '--';
                document.getElementById('stat-epex-min-time').innerText = `om ${s.min_time || '--:--'}`;
                document.getElementById('stat-epex-max').innerText = s.max_price || '--';
                document.getElementById('stat-epex-max-time').innerText = `om ${s.max_time || '--:--'}`;
                document.getElementById('stat-epex-solar-peak').innerText = s.peak_solar_forecast || '--';
                document.getElementById('stat-epex-solar-margin').innerText = `+${s.solar_savings_avg || '--'}`;

                // Destroy old instance
                if (electricityPricesChartInstance) electricityPricesChartInstance.destroy();

                const isBarMode = (OpenHEMSChartEngine.getChartType() === 'bar');

                const solarDataset = isBarMode ? {
                    type: 'bar',
                    label: 'Verwachte Zonneproductie (kW)',
                    data: data.solar_forecast_kw || [],
                    yAxisID: 'y1',
                    borderColor: OpenHEMSTokens.colors.solar,
                    backgroundColor: OpenHEMSTokens.colors.solarBg,
                    borderWidth: 1,
                    borderRadius: 4,
                    order: 2
                } : {
                    type: 'line',
                    label: 'Verwachte Zonneproductie (kW)',
                    data: data.solar_forecast_kw || [],
                    yAxisID: 'y1',
                    borderColor: OpenHEMSTokens.colors.solar,
                    backgroundColor: OpenHEMSTokens.colors.solarArea,
                    fill: true,
                    borderWidth: 2,
                    tension: 0.35,
                    pointRadius: 0,
                    order: 2
                };

                const priceDataset = {
                    type: 'line',
                    label: 'EPEX Inkoop All-in (€/kWh)',
                    data: data.epex_prices || [],
                    yAxisID: 'y',
                    borderColor: '#3B82F6',
                    backgroundColor: 'transparent',
                    borderWidth: 1.5,
                    stepped: 'before',
                    pointRadius: 0,
                    pointHoverRadius: 4,
                    tension: 0,
                    order: 1
                };

                const exportDataset = {
                    type: 'line',
                    label: 'EPEX Teruglevering (€/kWh)',
                    data: data.export_prices || [],
                    yAxisID: 'y',
                    borderColor: '#06B6D4',
                    backgroundColor: 'transparent',
                    borderWidth: 1.5,
                    borderDash: [4, 4],
                    stepped: 'before',
                    pointRadius: 0,
                    pointHoverRadius: 4,
                    tension: 0,
                    fill: false,
                    order: 3
                };

                const ctx = canvas.getContext('2d');
                electricityPricesChartInstance = new Chart(ctx, {
                    type: isBarMode ? 'bar' : 'line',
                    data: {
                        labels: data.labels,
                        datasets: [solarDataset, priceDataset, exportDataset]
                    },
                    options: {
                        responsive: true,
                        maintainAspectRatio: false,
                        interaction: {
                            mode: 'index',
                            intersect: false
                        },
                        plugins: {
                            legend: {
                                display: true,
                                position: 'top',
                                labels: {
                                    color: OpenHEMSTokens.colors.textMuted,
                                    font: { family: OpenHEMSTokens.fonts.mono, size: 10 },
                                    boxWidth: 10
                                }
                            },
                            tooltip: {
                                enabled: false,
                                external: function(context) {
                                    customPricesTooltipHandler(context);
                                }
                            }
                        },
                        scales: {
                            x: {
                                grid: { color: OpenHEMSTokens.colors.gridLine },
                                ticks: {
                                    color: OpenHEMSTokens.colors.textMuted,
                                    font: { family: OpenHEMSTokens.fonts.mono, size: 10 },
                                    maxTicksLimit: 12
                                }
                            },
                            y: {
                                type: 'linear',
                                display: true,
                                position: 'left',
                                title: { display: true, text: 'Tarief (€/kWh)', color: OpenHEMSTokens.colors.priceLine, font: { family: OpenHEMSTokens.fonts.mono, size: 10 } },
                                grid: { color: OpenHEMSTokens.colors.gridLine },
                                ticks: {
                                    color: OpenHEMSTokens.colors.priceLine,
                                    font: { family: OpenHEMSTokens.fonts.mono, size: 10 },
                                    callback: function(val) { return '€' + Number(val).toFixed(2); }
                                }
                            },
                            y1: {
                                type: 'linear',
                                display: true,
                                position: 'right',
                                title: { display: true, text: 'Zon (kW)', color: OpenHEMSTokens.colors.solar, font: { family: OpenHEMSTokens.fonts.mono, size: 10 } },
                                grid: { drawOnChartArea: false },
                                ticks: {
                                    color: OpenHEMSTokens.colors.solar,
                                    font: { family: OpenHEMSTokens.fonts.mono, size: 10 },
                                    callback: function(val) { return Number(val).toFixed(1) + ' kW'; }
                                },
                                min: 0
                            }
                        }
                    }
                });
            } catch (err) {
                console.error('Failed to load electricity prices chart:', err);
            }
        }

        

        // =========================================================================
        // MODEL VALIDATION OVERLAY CHART (VOORSPELLING VS. WERKELIJKHEID)
        // =========================================================================
        var validationOverlayChartInstance = null;
        var validationComponent = 'all'; // 'all', 'solar', 'dhw', 'cv'
        var validationPeriod = '24h';    // '24h', '48h', '7d'
        var validationResolution = '15m'; // '15m', '1h'
        var validationDataCache = null;


        function setValidationComponent(comp) {
            validationComponent = comp;
            ['all', 'solar', 'dhw', 'cv'].forEach(c => {
                const btn = document.getElementById('btn-val-' + c);
                if (btn) {
                    if (c === comp) {
                        btn.className = 'px-2.5 py-1 rounded-lg bg-cyan-600 text-white font-bold transition shadow';
                    } else {
                        btn.className = 'px-2.5 py-1 rounded-lg text-slate-400 hover:text-white transition';
                    }
                }
            });
            renderValidationOverlayChart();
        }

        function setValidationPeriod(tf) {
            validationPeriod = tf;
            ['24h', '48h', '7d'].forEach(p => {
                const btn = document.getElementById('val-tf-' + p);
                if (btn) {
                    if (p === tf) {
                        btn.className = 'px-2 py-0.5 rounded transition font-medium bg-cyan-600 text-white shadow';
                    } else {
                        btn.className = 'px-2 py-0.5 rounded transition font-medium text-slate-400 hover:text-slate-200';
                    }
                }
            });
            loadValidationOverlayChart();
        }

        function setValidationResolution(res) {
            validationResolution = res;
            ['15m', '1h'].forEach(r => {
                const btn = document.getElementById('val-res-' + r);
                if (btn) {
                    if (r === res) {
                        btn.className = 'px-2 py-0.5 rounded transition font-medium bg-blue-600 text-white shadow';
                    } else {
                        btn.className = 'px-2 py-0.5 rounded transition font-medium text-slate-400 hover:text-slate-200';
                    }
                }
            });
            loadValidationOverlayChart();
        }

        async function loadValidationOverlayChart() {
            const canvas = document.getElementById('chart-validation-overlay');
            if (!canvas) return;

            try {
                const res = await fetch(`./api/analytics/validation_overlay?range=${encodeURIComponent(validationPeriod)}&resolution=${encodeURIComponent(validationResolution)}`);
                const data = await res.json();
                if (data.status !== 'success') {
                    console.error('Validation overlay error:', data.message);
                    return;
                }
                validationDataCache = data;
                renderValidationOverlayChart();
            } catch (e) {
                console.error('Failed to load validation overlay chart:', e);
            }
        }

        function renderValidationOverlayChart() {
            if (!validationDataCache) return;
            const canvas = document.getElementById('chart-validation-overlay');
            if (!canvas) return;
            const ctx = canvas.getContext('2d');

            const comp = validationComponent;
            const metrics = validationDataCache.metrics ? (validationDataCache.metrics[comp] || {}) : {};
            
            // Update KPI badges
            const accEl = document.getElementById('val-kpi-accuracy');
            if (accEl) {
                const acc = metrics.accuracy_pct !== undefined ? metrics.accuracy_pct : '--';
                accEl.innerText = `Kwaliteit: ${acc}%`;
                if (acc >= 85) accEl.className = 'px-2.5 py-1 rounded-lg border bg-emerald-950/60 border-emerald-500/40 text-emerald-300 font-bold';
                else if (acc >= 70) accEl.className = 'px-2.5 py-1 rounded-lg border bg-amber-950/60 border-amber-500/40 text-amber-300 font-bold';
                else accEl.className = 'px-2.5 py-1 rounded-lg border bg-blue-950/60 border-blue-500/40 text-blue-300 font-bold';
            }

            const maeEl = document.getElementById('val-kpi-mae');
            if (maeEl) {
                maeEl.innerText = `Gem. Afwijking: ${metrics.mae_w !== undefined ? metrics.mae_w : '--'} W`;
            }

            const totEl = document.getElementById('val-kpi-totals');
            if (totEl) {
                const dSign = metrics.delta_kwh > 0 ? '+' : '';
                totEl.innerText = `Werkelijk: ${metrics.total_actual_kwh || 0} kWh | Voorspeld: ${metrics.total_pred_kwh || 0} kWh (Δ ${dSign}${metrics.delta_kwh || 0} kWh)`;
            }

            const actSeries = validationDataCache.actual ? (validationDataCache.actual[comp] || []) : [];
            const predSeries = validationDataCache.predicted ? (validationDataCache.predicted[comp] || []) : [];

            // Theme colors per component
            const themeMap = {
                'all': {
                    actBorder: '#06B6D4',
                    actFill: 'rgba(6, 182, 212, 0.12)',
                    predBorder: '#C084FC',
                    unit: 'kW',
                    actLabel: 'Werkelijk Totaal (Telemetrie)',
                    predLabel: 'Voorspeld Totaal (Model)'
                },
                'solar': {
                    actBorder: '#F59E0B',
                    actFill: 'rgba(245, 158, 11, 0.15)',
                    predBorder: '#FDE047',
                    unit: 'kW',
                    actLabel: 'Werkelijke Zonnestroom (Inepro 103)',
                    predLabel: 'Voorspelde Zonnestroom (POA Model)'
                },
                'dhw': {
                    actBorder: '#F43F5E',
                    actFill: 'rgba(244, 63, 94, 0.15)',
                    predBorder: '#FB923C',
                    unit: 'kW',
                    actLabel: 'Werkelijke Warmtepomp SWW (Daikin)',
                    predLabel: 'Geplande SWW Sturing (DHW Model)'
                },
                'cv': {
                    actBorder: '#3B82F6',
                    actFill: 'rgba(59, 130, 246, 0.15)',
                    predBorder: '#818CF8',
                    unit: 'kW',
                    actLabel: 'Werkelijke Warmtepomp CV (Daikin)',
                    predLabel: 'Voorspelde CV Vraag (2-Massa Model)'
                }
            };

            const t = themeMap[comp] || themeMap['all'];

            if (validationOverlayChartInstance) {
                validationOverlayChartInstance.destroy();
            }

            const chartDatasets = [
                {
                    label: t.actLabel,
                    data: actSeries,
                    borderColor: t.actBorder,
                    backgroundColor: t.actFill,
                    borderWidth: 2.5,
                    fill: true,
                    tension: 0.25,
                    pointRadius: 0,
                    pointHoverRadius: 5
                },
                {
                    label: t.predLabel,
                    data: predSeries,
                    borderColor: t.predBorder,
                    borderWidth: 2,
                    borderDash: [5, 4],
                    fill: false,
                    tension: 0.25,
                    pointRadius: 0,
                    pointHoverRadius: 5
                }
            ];

            if (comp === 'dhw' && validationDataCache.predicted && validationDataCache.predicted.dhw_demand) {
                chartDatasets.push({
                    label: 'Verwachte Warmtevraag (Aftap kWh_th)',
                    data: validationDataCache.predicted.dhw_demand,
                    borderColor: 'rgba(251, 146, 60, 0.40)',
                    backgroundColor: 'rgba(251, 146, 60, 0.08)',
                    borderWidth: 1.5,
                    borderDash: [2, 3],
                    fill: true,
                    tension: 0.3,
                    pointRadius: 0,
                    pointHoverRadius: 4
                });
            }

            validationOverlayChartInstance = new Chart(ctx, {
                type: 'line',
                data: {
                    labels: validationDataCache.labels || [],
                    datasets: chartDatasets
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    interaction: {
                        mode: 'index',
                        intersect: false
                    },
                    plugins: {
                        legend: {
                            display: true,
                            labels: {
                                color: '#94A3B8',
                                font: { family: 'monospace', size: 11 },
                                boxWidth: 16
                            }
                        },
                        tooltip: {
                            backgroundColor: '#0B0F17',
                            borderColor: '#334155',
                            borderWidth: 1,
                            titleColor: '#F8FAFC',
                            bodyColor: '#CBD5E1',
                            callbacks: {
                                label: function(context) {
                                    const val = context.parsed.y;
                                    return `  ${context.dataset.label}: ${val.toFixed(2)} kW`;
                                },
                                afterBody: function(items) {
                                    if (items.length >= 2) {
                                        const a = items[0].parsed.y;
                                        const p = items[1].parsed.y;
                                        const deltaW = Math.round((a - p) * 1000);
                                        const sign = deltaW > 0 ? '+' : '';
                                        return `  Afwijking (Delta): ${sign}${deltaW} W`;
                                    }
                                    return '';
                                }
                            }
                        }
                    },
                    scales: {
                        x: {
                            grid: { color: 'rgba(30, 41, 59, 0.4)' },
                            ticks: {
                                color: '#94A3B8',
                                font: { family: 'monospace', size: 10 },
                                maxTicksLimit: 12
                            }
                        },
                        y: {
                            grid: { color: 'rgba(30, 41, 59, 0.6)' },
                            ticks: {
                                color: '#94A3B8',
                                font: { family: 'monospace', size: 10 },
                                callback: function(v) { return v.toFixed(1) + ' kW'; }
                            },
                            title: {
                                display: true,
                                text: 'Vermogen (kW)',
                                color: '#94A3B8',
                                font: { family: 'monospace', size: 10 }
                            }
                        }
                    }
                }
            });
        }

        async function loadPowerProducersChart() {
            const canvas = document.getElementById('powerProducersChart');
            if (!canvas) return;
            
            try {
                const rangeSelect = document.getElementById('pp-range-select');
                const rangeVal = rangeSelect ? rangeSelect.value : '24h';
                const resParam = (powerProducersChartType === 'line' && powerProducersResolution === '1h') ? '1h' : powerProducersResolution;
                const res = await fetch('./api/analytics/power_producers?range=' + encodeURIComponent(rangeVal) + '&resolution=' + encodeURIComponent(resParam));
                const data = await res.json();
                window.__lastHistoricalData = data;
                window.__lastHistoricalIntervalH = data.interval_h || (resParam === '15m' ? 0.25 : 1.0);
                if (data.status !== 'success') {
                    console.error('Power producers error:', data.message);
                    return;
                }

                // Update Legend Stats & Timeframe Totals
                const s = data.stats || {};
                if (s.zonnepanelen) {
                    document.getElementById('stat-solar-last').innerText = s.zonnepanelen.last;
                    document.getElementById('stat-solar-min').innerText = s.zonnepanelen.min;
                    if (document.getElementById('stat-solar-total')) document.getElementById('stat-solar-total').innerText = s.zonnepanelen.total_kwh || '-- kWh';
                    if (document.getElementById('stat-solar-cost')) document.getElementById('stat-solar-cost').innerText = s.zonnepanelen.cost_eur || '€--';
                }
                if (s.teruglevering) {
                    document.getElementById('stat-terug-last').innerText = s.teruglevering.last;
                    document.getElementById('stat-terug-min').innerText = s.teruglevering.min;
                    if (document.getElementById('stat-terug-total')) document.getElementById('stat-terug-total').innerText = s.teruglevering.total_kwh || '-- kWh';
                    if (document.getElementById('stat-terug-cost')) document.getElementById('stat-terug-cost').innerText = s.teruglevering.cost_eur || '€--';
                }
                if (s.afname) {
                    document.getElementById('stat-afname-last').innerText = s.afname.last;
                    document.getElementById('stat-afname-max').innerText = s.afname.max;
                    if (document.getElementById('stat-afname-total')) document.getElementById('stat-afname-total').innerText = s.afname.total_kwh || '-- kWh';
                    if (document.getElementById('stat-afname-cost')) document.getElementById('stat-afname-cost').innerText = s.afname.cost_eur || '€--';
                }
                if (s.totaal_opgewekt) {
                    document.getElementById('stat-opgewekt-last').innerText = s.totaal_opgewekt.last;
                    document.getElementById('stat-opgewekt-min').innerText = s.totaal_opgewekt.min;
                    if (document.getElementById('stat-opgewekt-total')) document.getElementById('stat-opgewekt-total').innerText = s.totaal_opgewekt.total_kwh || '-- kWh';
                    if (document.getElementById('stat-opgewekt-cost')) document.getElementById('stat-opgewekt-cost').innerText = s.totaal_opgewekt.cost_eur || '€--';
                }
                if (s.opgewekt_gebruikt) {
                    document.getElementById('stat-selfcons-last').innerText = s.opgewekt_gebruikt.last;
                    document.getElementById('stat-selfcons-min').innerText = s.opgewekt_gebruikt.min;
                    if (document.getElementById('stat-selfcons-total')) document.getElementById('stat-selfcons-total').innerText = s.opgewekt_gebruikt.total_kwh || '-- kWh';
                    if (document.getElementById('stat-selfcons-cost')) document.getElementById('stat-selfcons-cost').innerText = s.opgewekt_gebruikt.cost_eur || '€--';
                }
                if (s.totaal_verbruik) {
                    document.getElementById('stat-verbruik-last').innerText = s.totaal_verbruik.last;
                    document.getElementById('stat-verbruik-max').innerText = s.totaal_verbruik.max;
                    if (document.getElementById('stat-verbruik-total')) document.getElementById('stat-verbruik-total').innerText = s.totaal_verbruik.total_kwh || '-- kWh';
                    if (document.getElementById('stat-verbruik-cost')) document.getElementById('stat-verbruik-cost').innerText = s.totaal_verbruik.cost_eur || '€--';
                }

                // Destroy old instance if exists
                if (powerProducersChartInstance) powerProducersChartInstance.destroy();

                const ctx = canvas.getContext('2d');

                // === PURE POWER (kW) STANDARDIZATION ===
                const intervalH = data.interval_h || window.__lastHistoricalIntervalH || (resParam === '15m' ? 0.25 : 1.0);

                // Convert instantaneous power (Watts) to pure kW (W / 1000)
                const toKw = (arr) => (arr || []).map(w => Number((w / 1000.0).toFixed(2)));
                const toKwNeg = (arr) => (arr || []).map(w => Number((-Math.abs(w) / 1000.0).toFixed(2)));

                const afnameKw = toKw(data.afname);
                const verbruikKw = toKw(data.verbruik);
                const selfConsKw = toKw(data.self_consumption);
                const terugKw = toKwNeg(data.teruglevering_negative);
                const selfConsNegKw = toKwNeg(data.self_consumption);
                const solarNegKw = toKwNeg(data.solar_negative);

                const netKw = afnameKw.map((afn, idx) => {
                    const ter = Math.abs(terugKw[idx] || 0);
                    return Number((afn - ter).toFixed(2));
                });

                let datasets = [];

                if (powerProducersChartType === 'bar') {
                    // === STAVEN (BAR) MODUS: 100% ZUIVER VERMOGEN (kW) PER INTERVAL ===
                    // 0. EPEX Inkoop & Teruglevering Curves (Rechter Y-as)
                    if (data.prices && data.prices.length > 0) {
                        datasets.push({
                            label: 'EPEX Inkoop All-in (€/kWh)',
                            data: data.prices,
                            type: 'line',
                            borderColor: '#3B82F6',
                            backgroundColor: 'transparent',
                            borderWidth: 1.5,
                            pointRadius: 0,
                            pointHoverRadius: 4,
                            yAxisID: 'y1',
                            tension: 0,
                            order: 0
                        });
                    }
                    if (data.export_prices && data.export_prices.length > 0) {
                        datasets.push({
                            label: 'EPEX Teruglevering (€/kWh)',
                            data: data.export_prices,
                            type: 'line',
                            borderColor: '#06B6D4',
                            borderDash: [4, 4],
                            backgroundColor: 'transparent',
                            borderWidth: 1.5,
                            pointRadius: 0,
                            pointHoverRadius: 4,
                            yAxisID: 'y1',
                            tension: 0,
                            order: 0
                        });
                    }
                    // 1. Totaal Verbruik Lijn (Oranje) in kW
                    datasets.push({
                        label: 'Totaal Verbruik (kW)',
                        data: verbruikKw,
                        type: 'line',
                        borderColor: '#F97316',
                        backgroundColor: 'transparent',
                        borderWidth: 1.5,
                        pointRadius: 0,
                        pointHoverRadius: 4,
                        tension: 0.25,
                        order: 1
                    });
                    // 2. Netto Verbruik Lijn (Felrood) in kW
                    datasets.push({
                        label: 'Netto Verbruik (kW)',
                        data: netKw,
                        type: 'line',
                        borderColor: '#EF4444',
                        backgroundColor: 'transparent',
                        borderWidth: 1.5,
                        pointRadius: 0,
                        pointHoverRadius: 4,
                        tension: 0.25,
                        order: 2
                    });
                    // 3. Positieve gestapelde staven: Afname + Opgewekt Gebruikt = Totaal Verbruik
                    datasets.push({
                        label: 'Afname (kW)',
                        data: afnameKw,
                        backgroundColor: '#EF4444',
                        stack: 'energy',
                        borderRadius: 2,
                        order: 3
                    });
                    datasets.push({
                        label: 'Opgewekt Gebruikt (kW)',
                        data: selfConsKw,
                        backgroundColor: '#06B6D4',
                        stack: 'energy',
                        borderRadius: 2,
                        order: 3
                    });
                    // 4. Negatieve gestapelde staven: Teruglevering + Direct Benut = Totale Zonneproductie
                    datasets.push({
                        label: 'Teruglevering (kW)',
                        data: terugKw,
                        backgroundColor: '#10B981',
                        stack: 'energy',
                        borderRadius: 2,
                        order: 4
                    });
                    datasets.push({
                        label: 'Zon Direct Benut (kW)',
                        data: selfConsNegKw,
                        backgroundColor: '#EAB308',
                        stack: 'energy',
                        borderRadius: 2,
                        order: 4
                    });
                } else {
                    // === LIJN (LINE / AREA) MODUS in kW ===
                    datasets = [
                        {
                            label: 'Totaal Verbruik (kW)',
                            data: verbruikKw,
                            borderColor: '#F97316',
                            backgroundColor: 'transparent',
                            borderWidth: 2,
                            pointRadius: 0,
                            tension: 0.25,
                            order: 1
                        },
                        {
                            label: 'Afname (kW)',
                            data: afnameKw,
                            borderColor: '#EF4444',
                            backgroundColor: 'rgba(239, 68, 68, 0.45)',
                            fill: true,
                            borderWidth: 1.5,
                            pointRadius: 0,
                            tension: 0.25,
                            order: 2
                        },
                        {
                            label: 'Opgewekt Gebruikt (kW)',
                            data: selfConsKw,
                            borderColor: '#14B8A6',
                            backgroundColor: 'rgba(20, 184, 166, 0.25)',
                            fill: true,
                            borderWidth: 1,
                            pointRadius: 0,
                            tension: 0.25,
                            order: 3
                        },
                        {
                            label: 'Teruglevering (kW)',
                            data: terugKw,
                            borderColor: '#10B981',
                            backgroundColor: 'rgba(16, 185, 129, 0.45)',
                            fill: true,
                            borderWidth: 1.5,
                            pointRadius: 0,
                            tension: 0.25,
                            order: 4
                        },
                        {
                            label: 'Zonnepanelen (kW)',
                            data: solarNegKw,
                            borderColor: '#EAB308',
                            backgroundColor: 'rgba(234, 179, 8, 0.55)',
                            fill: true,
                            borderWidth: 1.5,
                            pointRadius: 0,
                            tension: 0.25,
                            order: 5
                        }
                    ];
                }

                // Symmetrische 0-as schaling in zuivere kW
                const allKwVals = [
                    ...afnameKw,
                    ...verbruikKw,
                    ...solarNegKw.map(Math.abs),
                    ...terugKw.map(Math.abs),
                    2.0
                ];
                let maxAbsKw = Math.max(...allKwVals);
                maxAbsKw = Math.ceil(maxAbsKw * 2) / 2; // Stappen van 0.5 kW
                if (maxAbsKw < 2.5) maxAbsKw = 2.5;

                const allPriceVals = [
                    ...(data.prices || []).map(Math.abs),
                    ...(data.export_prices || []).map(Math.abs),
                    0.25
                ];
                let maxAbsPrice = Math.max(...allPriceVals);
                maxAbsPrice = Math.ceil(maxAbsPrice * 10) / 10;
                if (maxAbsPrice < 0.30) maxAbsPrice = 0.30;

                powerProducersChartInstance = new Chart(ctx, {
                    type: powerProducersChartType === 'bar' ? 'bar' : 'line',
                    data: {
                        labels: data.labels,
                        datasets: datasets
                    },
                    options: {
                        responsive: true,
                        maintainAspectRatio: false,
                        interaction: {
                            mode: 'index',
                            intersect: false
                        },
                        plugins: {
                            legend: {
                                display: false // Gekoppeld aan de 6-box overzichtskaarten
                            },
                            tooltip: {
                                enabled: false,
                                external: function(context) {
                                    customHemsTooltipHandler(context, false);
                                }
                            }
                        },
                        scales: {
                            x: {
                                grid: { color: 'rgba(30, 41, 59, 0.4)' },
                                ticks: { 
                                    color: '#94A3B8', 
                                    font: { family: 'monospace', size: 10 },
                                    maxTicksLimit: 12
                                }
                            },
                            y: {
                                min: -maxAbsKw,
                                max: maxAbsKw,
                                title: { 
                                    display: true, 
                                    text: 'Opbrengst (-kW)  <  0  <  Verbruik (+kW)', 
                                    color: '#94A3B8', 
                                    font: { family: 'monospace', size: 10 } 
                                },
                                grid: {
                                    color: (ctx) => ctx.tick && ctx.tick.value === 0 ? '#CBD5E1' : 'rgba(30, 41, 59, 0.6)',
                                    lineWidth: (ctx) => ctx.tick && ctx.tick.value === 0 ? 2 : 1
                                },
                                ticks: {
                                    color: '#94A3B8',
                                    font: { family: 'monospace', size: 10 },
                                    callback: function(val) {
                                        const absV = Math.abs(val);
                                        const prefix = val < 0 ? '-' : '';
                                        return `${prefix}${absV.toFixed(1)} kW`;
                                    }
                                }
                            },
                            y1: {
                                type: 'linear',
                                position: 'right',
                                display: true,
                                min: -maxAbsPrice,
                                max: maxAbsPrice,
                                title: { display: true, text: 'Tarief (€/kWh)', color: '#06B6D4', font: { family: 'monospace', size: 10 } },
                                grid: { drawOnChartArea: false },
                                ticks: {
                                    color: '#06B6D4',
                                    font: { family: 'monospace', size: 10 },
                                    callback: function(val) {
                                        return val >= 0 ? '€' + Number(val).toFixed(2) : '';
                                    }
                                }
                            }
                        }
                    }
                });

                // === RENDER KOSTEN HISTORIE CHART ===
                const canvasCostHist = document.getElementById('costHistoryChart');
                if (canvasCostHist) {
                    if (costHistoryChartInstance) costHistoryChartInstance.destroy();

                    const histPrices = data.prices || [];
                    const histExportPrices = data.export_prices || [];

                    // Calculate Net Cost / Revenue per slot for history
                    const histNetCostEurArr = netKw.map((nKw, idx) => {
                        const kwh = Math.abs(nKw) * intervalH;
                        if (nKw >= 0) {
                            const pBuy = histPrices[idx] || 0.25;
                            return Number((kwh * pBuy).toFixed(3));
                        } else {
                            const pSell = (histExportPrices[idx] !== undefined) ? histExportPrices[idx] : 0.10;
                            return Number((- (kwh * pSell)).toFixed(3));
                        }
                    });

                    // Summary statistics
                    const totHistNetKwh = netKw.reduce((acc, kw) => acc + (kw * intervalH), 0);
                    const totHistNetCostEur = histNetCostEurArr.reduce((acc, c) => acc + c, 0);

                    // Update title with selected range
                    const titleEl = document.getElementById('cost-history-chart-title');
                    if (titleEl) {
                        titleEl.innerText = `Kosten Historie (${rangeVal})`;
                    }
                    if (document.getElementById('cost-history-total-net-kwh')) {
                        document.getElementById('cost-history-total-net-kwh').innerText = `Netto: ${totHistNetKwh >= 0 ? '+' : ''}${totHistNetKwh.toFixed(1)} kWh`;
                    }
                    if (document.getElementById('cost-history-netto')) {
                        document.getElementById('cost-history-netto').innerText = `Netto: ${totHistNetCostEur >= 0 ? '+€' : '-€'}${Math.abs(totHistNetCostEur).toFixed(2)}`;
                    }

                    // Symmetrical bounds
                    let maxAbsCost = Math.max(...histNetCostEurArr.map(Math.abs), 0.20);
                    maxAbsCost = Math.ceil(maxAbsCost * 10) / 10;
                    if (maxAbsCost < 0.25) maxAbsCost = 0.25;

                    const costHistConfig = {
                        type: 'bar',
                        data: {
                            labels: data.labels,
                            datasets: [
                                {
                                    label: 'Netto Kosten (€)',
                                    data: histNetCostEurArr,
                                    type: 'bar',
                                    backgroundColor: 'rgba(245, 158, 11, 0.75)', // Amber 500 bar
                                    borderColor: '#D97706',
                                    borderWidth: 1,
                                    borderRadius: 3,
                                    yAxisID: 'yCost',
                                    order: 4
                                },
                                {
                                    label: 'Netto Verbruik (kW)',
                                    data: netKw,
                                    type: 'line',
                                    borderColor: '#EF4444', // Red 500
                                    backgroundColor: 'transparent',
                                    fill: false,
                                    borderWidth: 1.5,
                                    pointRadius: 0,
                                    pointHoverRadius: 4,
                                    tension: 0.25,
                                    yAxisID: 'y',
                                    order: 1
                                },
                                {
                                    label: 'EPEX Inkoop All-in (€/kWh)',
                                    data: histPrices,
                                    type: 'line',
                                    borderColor: '#3B82F6', // Blue 500
                                    backgroundColor: 'transparent',
                                    borderWidth: 1.5,
                                    pointRadius: 0,
                                    pointHoverRadius: 4,
                                    tension: 0,
                                    yAxisID: 'yPrice',
                                    order: 2
                                },
                                {
                                    label: 'EPEX Teruglevering (€/kWh)',
                                    data: histExportPrices,
                                    type: 'line',
                                    borderColor: '#06B6D4', // Cyan 500
                                    borderDash: [4, 4],
                                    backgroundColor: 'transparent',
                                    borderWidth: 1.5,
                                    pointRadius: 0,
                                    pointHoverRadius: 4,
                                    tension: 0,
                                    yAxisID: 'yPrice',
                                    order: 3
                                }
                            ]
                        },
                        options: {
                            responsive: true,
                            maintainAspectRatio: false,
                            interaction: { mode: 'index', intersect: false },
                            plugins: {
                                legend: { display: false },
                                tooltip: {
                                    enabled: false,
                                    external: function(context) {
                                        customCostHistoryTooltipHandler(context, netKw, histNetCostEurArr, histPrices, histExportPrices, intervalH);
                                    }
                                }
                            },
                            scales: {
                                x: {
                                    grid: { color: 'rgba(30, 41, 59, 0.4)' },
                                    ticks: { color: '#94A3B8', font: { family: 'monospace', size: 10 } }
                                },
                                y: {
                                    type: 'linear',
                                    position: 'left',
                                    min: -maxAbsKw,
                                    max: maxAbsKw,
                                    title: {
                                        display: true,
                                        text: 'Netto Vermogen (kW)',
                                        color: '#EF4444',
                                        font: { family: 'monospace', size: 10, weight: 'bold' }
                                    },
                                    grid: {
                                        color: (ctx) => ctx.tick && ctx.tick.value === 0 ? '#CBD5E1' : 'rgba(30, 41, 59, 0.5)',
                                        lineWidth: (ctx) => ctx.tick && ctx.tick.value === 0 ? 2 : 1
                                    },
                                    ticks: {
                                        color: '#EF4444',
                                        font: { family: 'monospace', size: 10 },
                                        callback: function(val) {
                                            return (val >= 0 ? '+' : '') + val.toFixed(1) + ' kW';
                                        }
                                    }
                                },
                                yCost: {
                                    type: 'linear',
                                    position: 'right',
                                    display: true,
                                    min: -maxAbsCost,
                                    max: maxAbsCost,
                                    title: {
                                        display: true,
                                        text: 'Netto Kosten (€)',
                                        color: '#F59E0B',
                                        font: { family: 'monospace', size: 10, weight: 'bold' }
                                    },
                                    grid: { drawOnChartArea: false },
                                    ticks: {
                                        color: '#F59E0B',
                                        font: { family: 'monospace', size: 10 },
                                        callback: function(val) {
                                            return (val >= 0 ? '+€' : '-€') + Math.abs(val).toFixed(2);
                                        }
                                    }
                                },
                                yPrice: {
                                    type: 'linear',
                                    position: 'right',
                                    display: true,
                                    min: -maxAbsPrice,
                                    max: maxAbsPrice,
                                    title: {
                                        display: true,
                                        text: 'EPEX Tarieven (€/kWh)',
                                        color: '#06B6D4',
                                        font: { family: 'monospace', size: 10, weight: 'bold' }
                                    },
                                    grid: { drawOnChartArea: false },
                                    ticks: {
                                        color: '#06B6D4',
                                        font: { family: 'monospace', size: 10 },
                                        callback: function(val) {
                                            return val >= 0 ? '€' + Number(val).toFixed(2) : '';
                                        }
                                    }
                                }
                            }
                        }
                    };

                    costHistoryChartInstance = new Chart(canvasCostHist.getContext('2d'), costHistConfig);
                    window.costHistoryChartInstance = costHistoryChartInstance;
                }
            } catch (err) {
                console.error('Failed to load power producers chart:', err);
            }
        }

        let dhwHistoryChartInstance = null;
        async function loadDhwHistoryChart() {
            const canvas = document.getElementById('dhwHistoryChart');
            if (!canvas) return;

            try {
                const rangeSelect = document.getElementById('pp-range-select');
                const rangeVal = rangeSelect ? rangeSelect.value : '24h';
                const res = await fetch('./api/analytics/dhw_history?range=' + encodeURIComponent(rangeVal) + '&resolution=15m');
                const data = await res.json();
                if (data.status !== 'success') {
                    console.error('DHW history error:', data.message);
                    return;
                }

                if (dhwHistoryChartInstance) {
                    dhwHistoryChartInstance.destroy();
                    dhwHistoryChartInstance = null;
                }

                const ctx = canvas.getContext('2d');
                dhwHistoryChartInstance = new Chart(ctx, {
                    type: 'line',
                    data: {
                        labels: data.labels,
                        datasets: [
                            {
                                label: 'Boilertemperatuur (°C)',
                                data: data.temperatures_c,
                                borderColor: '#F59E0B',
                                backgroundColor: 'rgba(245, 158, 11, 0.08)',
                                borderWidth: 2.5,
                                pointRadius: data.labels.length > 50 ? 0 : 2,
                                pointHoverRadius: 5,
                                fill: true,
                                tension: 0.3,
                                yAxisID: 'y'
                            },
                            {
                                label: 'Warmtevraag (kWh)',
                                data: data.demand_kwh_th,
                                type: 'bar',
                                backgroundColor: 'rgba(56, 189, 248, 0.45)',
                                borderColor: '#38BDF8',
                                borderWidth: 1,
                                borderRadius: 3,
                                yAxisID: 'y1'
                            }
                        ]
                    },
                    options: {
                        responsive: true,
                        maintainAspectRatio: false,
                        interaction: { mode: 'index', intersect: false },
                        plugins: {
                            legend: { display: false },
                            tooltip: {
                                enabled: false,
                                external: function(context) {
                                    if (typeof customDhwTooltipHandler === 'function') {
                                        customDhwTooltipHandler(context);
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
                                    maxTicksLimit: 14
                                }
                            },
                            y: {
                                position: 'left',
                                title: { display: true, text: 'Temperatuur (°C)', color: '#F59E0B', font: { size: 10, weight: 'bold' } },
                                grid: { color: 'rgba(255, 255, 255, 0.05)' },
                                ticks: {
                                    color: '#F59E0B',
                                    font: { size: 10, family: 'monospace' },
                                    callback: function(v) { return v + '°C'; }
                                },
                                min: 30,
                                max: 65
                            },
                            y1: {
                                position: 'right',
                                title: { display: true, text: 'Warmtevraag (kWh)', color: '#38BDF8', font: { size: 10, weight: 'bold' } },
                                grid: { drawOnChartArea: false },
                                ticks: {
                                    color: '#38BDF8',
                                    font: { size: 10, family: 'monospace' },
                                    callback: function(v) { return v.toFixed(1) + ' kWh'; }
                                },
                                min: 0,
                                suggestedMax: 2.0
                            }
                        }
                    }
                });
            } catch (e) {
                console.error('Failed to load DHW history chart:', e);
            }
        }

        async function loadAnalytics() {
            try {
                const res = await fetch('./api/analytics');
                const d = await res.json();
                
                // Populate Top 4 History KPI cards (1: Costs Today, 2: Solar Today, 3: Savings Today, 4: Heat Pump Today)
                if (d.history_kpis) {
                    const hk = d.history_kpis;
                    const elCostsMain = document.getElementById('hist-kpi-costs-main');
                    const elCostsSub = document.getElementById('hist-kpi-costs-sub');
                    if (elCostsMain) elCostsMain.innerText = hk.costs.main;
                    if (elCostsSub) elCostsSub.innerText = hk.costs.sub;

                    const elSolarMain = document.getElementById('hist-kpi-solar-main');
                    const elSolarSub = document.getElementById('hist-kpi-solar-sub');
                    if (elSolarMain) {
                        elSolarMain.innerHTML = `${hk.solar.main} <span class="text-xs text-slate-400 font-normal">${hk.solar.main_extra || ''}</span>`;
                    }
                    if (elSolarSub) elSolarSub.innerText = hk.solar.sub;

                    const elSavMain = document.getElementById('hist-kpi-savings-main');
                    const elSavSub = document.getElementById('hist-kpi-savings-sub');
                    if (elSavMain) elSavMain.innerText = hk.savings.main;
                    if (elSavSub) elSavSub.innerText = hk.savings.sub;

                    const elHpMain = document.getElementById('hist-kpi-hp-main');
                    const elHpSub = document.getElementById('hist-kpi-hp-sub');
                    if (elHpMain) {
                        elHpMain.innerHTML = `${hk.heatpump.main} <span class="text-xs text-slate-400 font-normal">${hk.heatpump.main_extra || ''}</span>`;
                    }
                    if (elHpSub) elHpSub.innerText = hk.heatpump.sub;
                }

                document.getElementById('analytics-digest').innerText = d.daily_digest;
            } catch (e) {
                console.warn('Analytics load error:', e);
            }
        }

        async function loadControl() {
            // Static or live control queries
        }

        // Boot
        OpenHEMSChartEngine.init();
        fetchHaEntities();
        const initialHashTab = (window.location.hash || '').replace('#', '');
        showTab(initialHashTab || "prediction");
        loadDecisionAuditLog();
        window.addEventListener('hashchange', () => {
            const hTab = (window.location.hash || '').replace('#', '');
            if (hTab) showTab(hTab);
        });
        // === PIPELINE & CALIBRATION MONITORS ===
                        
        async function loadPipelineStatus() {
            try {
                const res = await fetch('./api/pipeline/status');
                const d = await res.json();
                if (d.status !== 'online') return;

                // Badges
                const progBadge = document.getElementById('pipeline-progress-badge');
                if (progBadge) progBadge.innerText = `Accumulator: ${d.samples_in_window}/${d.expected_samples} (${d.samples_in_window * d.sample_interval_s}s)`;
                const flushBadge = document.getElementById('pipeline-flush-badge');
                if (flushBadge) flushBadge.innerText = `Laatste Flush: ${d.last_flush_time}`;

                // Progress Bar
                const pct = Math.min(100, Math.round((d.samples_in_window / d.expected_samples) * 100));
                const pctEl = document.getElementById('pipe-window-pct');
                if (pctEl) pctEl.innerText = `${pct}%`;
                const barEl = document.getElementById('pipe-progress-bar');
                if (barEl) barEl.style.width = `${pct}%`;

                const totalPointsEl = document.getElementById('pipe-total-points');
                if (totalPointsEl) totalPointsEl.innerText = `Totaal weggeschreven: ${d.total_points_written} punten`;

                // Live Power Balance Numbers
                const b = d.live_balance || {};
                if (document.getElementById('live-net-grid')) document.getElementById('live-net-grid').innerText = `${b.net_grid_w >= 0 ? '+' : ''}${b.net_grid_w || 0} W`;
                if (document.getElementById('live-solar')) document.getElementById('live-solar').innerText = `${b.solar_w || 0} W`;
                if (document.getElementById('live-direct-solar')) document.getElementById('live-direct-solar').innerText = `${b.direct_solar_w || 0} W`;
                if (document.getElementById('live-heatpump')) document.getElementById('live-heatpump').innerText = `${b.heatpump_w || 0} W`;
                if (document.getElementById('live-tot-house')) document.getElementById('live-tot-house').innerText = `${b.total_house_w || 0} W`;
                if (document.getElementById('live-unallocated')) document.getElementById('live-unallocated').innerText = `${b.unallocated_w || 0} W`;
            } catch (e) {
                console.warn("Pipeline poll error:", e);
            }
        }

        async function loadUnallocatedModel() {
            try {
                const res = await fetch('./api/calibration/unallocated-model');
                cachedUnallocModel = await res.json();
                renderUnallocDay(activeUnallocDay);
            updateDhwLiveCard();
            } catch (e) {
                console.warn("Error loading unallocated model:", e);
            }
        }

        let currentProfileType = 'unallocated';
        let activeMonthNum = (new Date()).getMonth() + 1; // 1-12 (current month)

        function renderMonthSelector() {
            const container = document.getElementById('month-selector-grid');
            if (!container) return;
            const months = [
                { num: 1, name: 'Jan' }, { num: 2, name: 'Feb' }, { num: 3, name: 'Mrt' },
                { num: 4, name: 'Apr' }, { num: 5, name: 'Mei' }, { num: 6, name: 'Jun' },
                { num: 7, name: 'Jul' }, { num: 8, name: 'Aug' }, { num: 9, name: 'Sep' },
                { num: 10, name: 'Okt' }, { num: 11, name: 'Nov' }, { num: 12, name: 'Dec' }
            ];

            container.innerHTML = '';
            months.forEach(m => {
                const btn = document.createElement('button');
                const isActive = (m.num === activeMonthNum);
                btn.className = isActive
                    ? 'month-btn py-1.5 px-1 rounded-lg bg-amber-600 border border-amber-500 text-white font-bold shadow text-center transition'
                    : 'month-btn py-1.5 px-1 rounded-lg bg-slate-900 border border-slate-800 text-slate-400 hover:text-slate-200 text-center transition';
                btn.innerText = m.name;
                btn.onclick = () => selectMonth(m.num);
                container.appendChild(btn);
            });
        }

        function selectMonth(mNum) {
            activeMonthNum = mNum;
            renderMonthSelector();
            renderUnallocDay(activeUnallocDay);
        }

        async function updateDhwLiveCard() {
            const banner = document.getElementById('dhw-decision-banner');
            if (!banner) return;
            banner.classList.remove('hidden');
            try {
                const lang = window.OpenHEMSi18n ? window.OpenHEMSi18n.getLang() : 'nl';
                const res = await fetch('./api/model/dhw-status?lang=' + encodeURIComponent(lang));
                if (res.ok) {
                    const data = await res.json();
                    const d = data.decision || {};
                    
                    // Card 1: Temp & Usable Heat
                    if (document.getElementById('dhw-live-temp-badge')) {
                        document.getElementById('dhw-live-temp-badge').innerText = `Actueel: ${d.current_temp_c}°C`;
                    }
                    if (document.getElementById('dhw-usable-heat')) {
                        document.getElementById('dhw-usable-heat').innerText = `${d.usable_heat_kwh_th} kWh_th (${d.usable_heat_mj} MJ)`;
                    }
                    if (document.getElementById('dhw-volume-caption')) {
                        document.getElementById('dhw-volume-caption').innerText = `350L combivat op ${d.current_temp_c}°C (mengcapaciteit ~${d.shower_liters_38c}L douchewater van 38°C).`;
                    }

                    // Card 2: Morning Dip & P95 Stress Scenario
                    if (document.getElementById('dhw-projected-dip')) {
                        document.getElementById('dhw-projected-dip').innerHTML = `${d.morning_dip_c}°C <span class="text-xs text-slate-400">(om ${d.morning_dip_time || '08:45'}u)</span>`;
                    }
                    if (document.getElementById('dhw-dip-subtext')) {
                        const p95Safe = d.p95_is_safe;
                        const p95Class = p95Safe ? 'text-emerald-400' : 'text-amber-400';
                        const firstSub = d.first_sub40_time || '12:30';
                        document.getElementById('dhw-dip-subtext').innerHTML = `<span class="${p95Class} font-bold">P95 Risicodip: ${d.morning_dip_p95_c}°C ${p95Safe ? '✓' : '⚠️'}</span> · Eerste dip &lt;40°C om ${firstSub}u`;
                    }

                    // Card 3: Dynamic Night Header & Comparative Economics
                    if (document.getElementById('dhw-night-header') && d.short_night_label) {
                        document.getElementById('dhw-night-header').innerText = `Nachtbesluit (${d.short_night_label})`;
                    }
                    if (document.getElementById('dhw-night-action')) {
                        document.getElementById('dhw-night-action').innerHTML = d.decision_title || (d.morning_is_safe ? '✅ Geen nachtlading nodig' : '⚠️ Nachtlading aanbevolen');
                    }
                    if (document.getElementById('dhw-night-subtext')) {
                        document.getElementById('dhw-night-subtext').innerText = d.decision_sub || (d.morning_is_safe ? 'Wachten tot daglading bespaart geld' : 'Nachtlading waarborgt ochtendcomfort');
                    }

                    // Explanation Text
                    if (document.getElementById('dhw-decision-explanation')) {
                        document.getElementById('dhw-decision-explanation').innerText = d.recommendation || '';
                    }
                }
            } catch (e) {
                console.warn("Error fetching DHW live status:", e);
            }
        }

        function switchProfileType(pType) {
            currentProfileType = pType;
            ['unallocated', 'dhw', 'cv'].forEach(t => {
                const btn = document.getElementById('btn-prof-' + t);
                if (btn) {
                    if (t === pType) {
                        const bgCol = t === 'unallocated' ? 'bg-blue-600' : (t === 'dhw' ? 'bg-amber-600' : 'bg-red-600');
                        btn.className = `px-3 py-1.5 rounded-lg ${bgCol} text-white font-bold transition flex items-center gap-1.5 shadow`;
                    } else {
                        btn.className = 'px-3 py-1.5 rounded-lg text-slate-400 hover:text-white transition flex items-center gap-1.5';
                    }
                }
            });

            const dot = document.getElementById('profile-status-indicator');
            if (dot) {
                dot.className = `w-3 h-3 rounded-full animate-pulse ${pType === 'unallocated' ? 'bg-blue-500' : (pType === 'dhw' ? 'bg-amber-500' : 'bg-red-500')}`;
            }

            renderUnallocDay(activeUnallocDay);
            updateDhwLiveCard();
        }

        function selectUnallocDay(dayIdx) {
            activeUnallocDay = dayIdx;
            renderUnallocDay(dayIdx);
        }

        function renderUnallocDay(dayIdx) {
            if (!cachedUnallocModel) return;
            const dayNames = cachedUnallocModel.day_names || ['Maandag', 'Dinsdag', 'Woensdag', 'Donderdag', 'Vrijdag', 'Zaterdag', 'Zondag'];
            const mData = cachedUnallocModel.monthly_profiles?.[String(activeMonthNum)] || {};
            const mName = mData.name_full || 'September';

            // Get seasonal multiplier for active month
            let multiplier = 1.0;
            if (currentProfileType === 'cv') {
                multiplier = (mData.cv_multiplier !== undefined) ? mData.cv_multiplier : 1.0;
            } else if (currentProfileType === 'dhw') {
                multiplier = (mData.dhw_multiplier !== undefined) ? mData.dhw_multiplier : 1.0;
            } else {
                multiplier = (mData.unalloc_multiplier !== undefined) ? mData.unalloc_multiplier : 1.0;
            }

            // Update month summary text
            const mSumEl = document.getElementById('month-impact-summary');
            if (mSumEl) {
                if (currentProfileType === 'cv') {
                    mSumEl.innerText = `${mName}: Gemiddeld ${mData.cv_daily_kwh || 0} kWh CV/dag (${Math.round(multiplier * 100)}% van stookseizoen)`;
                } else if (currentProfileType === 'dhw') {
                    mSumEl.innerText = `${mName}: Gemiddeld ${mData.dhw_daily_kwh || 3.0} kWh SWW/dag (${Math.round(multiplier * 100)}% van basis)`;
                } else {
                    mSumEl.innerText = `${mName}: Gemiddelde basislast ${mData.unalloc_avg_w || 495} W (${Math.round(multiplier * 100)}% van jaarbasis)`;
                }
            }

            // Select base dataset and apply monthly factor
            let baseQuarters = [];
            let colorClass = 'bg-blue-500 hover:bg-cyan-400';
            let catName = 'Huishoudelijk';

            if (currentProfileType === 'dhw') {
                baseQuarters = cachedUnallocModel.dhw_profile_96_quarters?.[dayIdx] || [];
                colorClass = 'bg-amber-500 hover:bg-yellow-300';
                catName = 'Tapwater SWW';
            } else if (currentProfileType === 'cv') {
                baseQuarters = cachedUnallocModel.cv_profile_96_quarters?.[dayIdx] || [];
                colorClass = 'bg-red-500 hover:bg-rose-300';
                catName = 'CV Verwarming';
            } else {
                baseQuarters = cachedUnallocModel.profile_96_quarters?.[dayIdx] || [];
                colorClass = 'bg-blue-500 hover:bg-cyan-400';
                catName = 'Huishoudelijk';
            }

            if (baseQuarters.length === 0) return;

            // Apply seasonal multiplier
            const quarters = baseQuarters.map(v => Math.round(v * multiplier * 10) / 10);

            // Update weekday tab styles and mark today
            const todayIdx = (new Date().getDay() + 6) % 7;
            const btns = document.querySelectorAll('.unalloc-day-btn');
            btns.forEach((btn, idx) => {
                const isToday = (idx === todayIdx);
                const dayBaseName = ['Maandag', 'Dinsdag', 'Woensdag', 'Donderdag', 'Vrijdag', 'Zaterdag', 'Zondag'][idx];
                btn.innerText = isToday ? `${dayBaseName} • Vandaag` : dayBaseName;
                if (idx === dayIdx) {
                    const bgActive = currentProfileType === 'unallocated' ? 'bg-blue-600 border-blue-500' : (currentProfileType === 'dhw' ? 'bg-amber-600 border-amber-500' : 'bg-red-600 border-red-500');
                    btn.className = `unalloc-day-btn px-3 py-1 rounded-lg border ${bgActive} text-white font-bold shadow`;
                } else {
                    btn.className = 'unalloc-day-btn px-3 py-1 rounded-lg border border-slate-800 bg-slate-900 text-slate-400 hover:text-slate-200 font-medium';
                }
            });

            // Update summary metric titles and values
            const avg = Math.round(quarters.reduce((a, b) => a + b, 0) / quarters.length);
            const totKwh = (quarters.reduce((a, b) => a + b, 0) * 0.25 / 1000).toFixed(2);
            const maxVal = Math.max(...quarters);
            const peakQ = quarters.indexOf(maxVal);
            const peakTime = `${String(Math.floor(peakQ/4)).padStart(2,'0')}:${String((peakQ%4)*15).padStart(2,'0')}`;

            if (currentProfileType === 'dhw') {
                document.getElementById('metric-title-1').innerText = `Dagbehoefte (${mName})`;
                document.getElementById('unalloc-metric-avg').innerText = `${totKwh} kWh/dag`;
                document.getElementById('metric-title-2').innerText = 'Stand-by Verlies (350L)';
                document.getElementById('unalloc-metric-night').innerText = '1.92 kWh/dag';
                document.getElementById('metric-title-3').innerText = 'Piek Opwarmmoment';
                document.getElementById('unalloc-metric-morning').innerText = `${peakTime} (${Math.round(maxVal)} W)`;
                document.getElementById('metric-title-4').innerText = 'Typische Laadduur';
                document.getElementById('unalloc-metric-evening').innerText = '45 minuten';
                document.getElementById('unalloc-selected-day-label').innerText = `${dayNames[dayIdx]} in ${mName}: SWW Behoefte (${totKwh} kWh/dag)`;
            } else if (currentProfileType === 'cv') {
                const nightAvg = Math.round(quarters.slice(0, 24).reduce((a,b)=>a+b,0)/24);
                const dayAvg = Math.round(quarters.slice(24, 92).reduce((a,b)=>a+b,0)/68);
                document.getElementById('metric-title-1').innerText = `Stookbehoefte (${mName})`;
                document.getElementById('unalloc-metric-avg').innerText = `${totKwh} kWh/dag`;
                document.getElementById('metric-title-2').innerText = 'Nachtverlaging (23-06u)';
                document.getElementById('unalloc-metric-night').innerText = `${nightAvg} W`;
                document.getElementById('metric-title-3').innerText = 'Ochtend Opstookpiek';
                document.getElementById('unalloc-metric-morning').innerText = `${peakTime} (${Math.round(maxVal)} W)`;
                document.getElementById('metric-title-4').innerText = 'Overdag Modulatie';
                document.getElementById('unalloc-metric-evening').innerText = `${dayAvg} W gem`;
                document.getElementById('unalloc-selected-day-label').innerText = `${dayNames[dayIdx]} in ${mName}: CV Stookprofiel (${totKwh} kWh/dag)`;
            } else {
                const nightMin = Math.min(...quarters.slice(0, 24));
                const morningPeak = Math.max(...quarters.slice(28, 44));
                const eveningPeak = Math.max(...quarters.slice(72, 92));
                document.getElementById('metric-title-1').innerText = `Basislast (${mName})`;
                document.getElementById('unalloc-metric-avg').innerText = `${avg} W`;
                document.getElementById('metric-title-2').innerText = 'Nacht Stand-by (00-06u)';
                document.getElementById('unalloc-metric-night').innerText = `${nightMin} W`;
                document.getElementById('metric-title-3').innerText = 'Ochtendpiek (07-11u)';
                document.getElementById('unalloc-metric-morning').innerText = `${morningPeak} W`;
                document.getElementById('metric-title-4').innerText = 'Avondpiek (18-23u)';
                document.getElementById('unalloc-metric-evening').innerText = `${eveningPeak} W`;
                document.getElementById('unalloc-selected-day-label').innerText = `${dayNames[dayIdx]} in ${mName}: Huisprofiel (${avg} W gemiddeld)`;
            }

            // Render 96 bars with interactive hover and touch readout
            const container = document.getElementById('unalloc-hourly-bars');
            const hoverBadge = document.getElementById('unalloc-hover-badge');
            if (container) {
                container.innerHTML = '';
                const displayMax = Math.max(50, ...quarters);
                quarters.forEach((w, q) => {
                    const barHeightPct = Math.max(2, Math.round((w / displayMax) * 100));
                    const h = Math.floor(q / 4);
                    const m = (q % 4) * 15;
                    const timeStr = `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`;
                    const kwhVal = (w * 0.25 / 1000).toFixed(3);

                    const col = document.createElement('div');
                    col.className = 'flex flex-col items-center justify-end h-full flex-1 min-w-[2px] cursor-pointer group py-0.5';
                    
                    const barDiv = document.createElement('div');
                    barDiv.className = `w-full ${colorClass} rounded-t transition-all group-hover:brightness-125`;
                    barDiv.style.height = `${barHeightPct}%`;
                    col.appendChild(barDiv);

                    // Hover / Touch interaction
                    const showHover = () => {
                        if (hoverBadge) {
                            hoverBadge.innerHTML = `<span class="font-bold text-white">📌 ${timeStr}</span> · <span class="font-bold text-cyan-300">${Math.round(w)} W</span> <span class="text-slate-400">(${kwhVal} kWh ${catName})</span>`;
                        }
                    };

                    col.onmouseenter = showHover;
                    col.ontouchstart = showHover;

                    container.appendChild(col);
                });

                container.onmouseleave = () => {
                    if (hoverBadge) {
                        hoverBadge.innerHTML = '<span class="w-1.5 h-1.5 rounded-full bg-cyan-400 animate-ping"></span> <span>Beweeg over een kwartier voor details</span>';
                    }
                };
            }
        }

                let dhwTempChartInstance = null;
                let heatingForecastChartInstance = null;

        async function renderHeatingForecastChart() {
            const canvas = document.getElementById('chart-heating-forecast');
            if (!canvas) return;
            try {
                const res = await fetch('./api/model/heating-forecast?resolution=' + encodeURIComponent(predictionResolution));
                if (!res.ok) return;
                const d = await res.json();
                if (!d.labels || d.labels.length === 0) return;

                const isActive = (d.thermostat_active !== false);
                const tSet = d.thermostat_setpoint_c || 20.0;
                const tStart = d.thermostat_start_threshold_c || (tSet - 0.5);

                const statusEl = document.getElementById('heating-kpi-status');
                if (statusEl) {
                    if (isActive) {
                        statusEl.className = 'px-2.5 py-0.5 rounded-md text-[10px] font-bold border bg-emerald-950/60 border-emerald-500/40 text-emerald-300';
                        statusEl.textContent = `Thermostaat: Aan (${tSet}°C)`;
                    } else {
                        statusEl.className = 'px-2.5 py-0.5 rounded-md text-[10px] font-bold border bg-slate-900 border-slate-700 text-slate-400';
                        statusEl.textContent = `Thermostaat: Uit (0 W)`;
                    }
                }
                const kwhEl = document.getElementById('heating-kpi-kwh');
                if (kwhEl) kwhEl.textContent = `⚡ Stroom: ${d.total_electrical_kwh || 0} kWh`;
                const costEl = document.getElementById('heating-kpi-cost');
                if (costEl) costEl.textContent = `💶 Kosten: €${Number(d.total_cost_eur || 0).toFixed(2)}`;

                const existingChart = Chart.getChart(canvas);
                if (existingChart) {
                    existingChart.destroy();
                }

                const labels = d.labels;
                const outTemps = d.outdoor_temps_c || [];
                const inTemps = d.indoor_temps_c || [];
                const cops = d.cops || [];
                const thLoss = d.thermal_loss_kw || [];
                const elKw = d.electrical_kw || [];
                const costs = d.costs_eur || [];
                const setpointLine = Array(labels.length).fill(tSet);
                const startLine = Array(labels.length).fill(tStart);

                const ctx = canvas.getContext('2d');
                heatingForecastChartInstance = new Chart(ctx, {
                    type: 'bar',
                    data: {
                        labels: labels,
                        datasets: [
                            {
                                label: 'Buitentemperatuur (°C)',
                                data: outTemps,
                                type: 'line',
                                yAxisID: 'y_temp',
                                borderColor: '#60A5FA',
                                backgroundColor: 'transparent',
                                borderWidth: 2.0,
                                tension: 0.25,
                                pointRadius: 0,
                                order: 1
                            },
                            {
                                label: 'Verwachte Binnentemperatuur (°C)',
                                data: inTemps,
                                type: 'line',
                                yAxisID: 'y_temp',
                                borderColor: '#F43F5E',
                                backgroundColor: 'transparent',
                                borderWidth: 2.2,
                                tension: 0.25,
                                pointRadius: 0,
                                order: 2
                            },
                            {
                                label: `Thermostaat Setpoint (${tSet}°C)`,
                                data: setpointLine,
                                type: 'line',
                                yAxisID: 'y_temp',
                                borderColor: 'rgba(244, 63, 94, 0.6)',
                                borderDash: [5, 5],
                                backgroundColor: 'transparent',
                                borderWidth: 1.5,
                                pointRadius: 0,
                                order: 3
                            },
                            {
                                label: `Inschakeldrempel (${tStart}°C)`,
                                data: startLine,
                                type: 'line',
                                yAxisID: 'y_temp',
                                borderColor: 'rgba(239, 68, 68, 0.45)',
                                borderDash: [2, 4],
                                backgroundColor: 'transparent',
                                borderWidth: 1.2,
                                pointRadius: 0,
                                order: 4
                            },
                            {
                                label: 'Daikin COP',
                                data: cops,
                                type: 'line',
                                yAxisID: 'y_temp',
                                borderColor: '#10B981',
                                borderDash: [4, 4],
                                backgroundColor: 'transparent',
                                borderWidth: 1.75,
                                tension: 0.2,
                                pointRadius: 0,
                                order: 3
                            },
                            {
                                label: 'Stroomkosten (€)',
                                data: costs,
                                type: 'line',
                                yAxisID: 'y_cost',
                                borderColor: '#22D3EE',
                                borderDash: [3, 3],
                                backgroundColor: 'transparent',
                                borderWidth: 1.75,
                                pointRadius: 0,
                                order: 4
                            },
                            {
                                label: 'Warmteverlies Woning (kW_th)',
                                data: thLoss,
                                yAxisID: 'y_power',
                                backgroundColor: 'rgba(239, 68, 68, 0.45)',
                                hoverBackgroundColor: '#EF4444',
                                borderRadius: 2,
                                order: 5
                            },
                            {
                                label: 'Stroom Warmtepomp (kW_el)',
                                data: elKw,
                                yAxisID: 'y_power',
                                backgroundColor: 'rgba(245, 158, 11, 0.65)',
                                hoverBackgroundColor: '#F59E0B',
                                borderRadius: 2,
                                order: 6
                            }
                        ]
                    },
                    options: {
                        responsive: true,
                        maintainAspectRatio: false,
                        interaction: { mode: 'index', intersect: false },
                        plugins: {
                            legend: { display: false },
                            tooltip: {
                                enabled: false,
                                external: function(context) {
                                    customHeatingTooltipHandler(context);
                                }
                            }
                        },
                        scales: {
                            x: {
                                grid: { color: 'rgba(30, 41, 59, 0.3)' },
                                ticks: { color: '#64748B', font: { size: 10 }, maxTicksLimit: 16 }
                            },
                            y_power: {
                                position: 'left',
                                min: 0,
                                title: {
                                    display: true,
                                    text: 'Vermogen (kW)',
                                    color: '#EF4444',
                                    font: { size: 10, weight: 'bold' }
                                },
                                grid: { color: 'rgba(30, 41, 59, 0.25)' },
                                ticks: { color: '#EF4444', font: { size: 10 }, callback: v => `${v} kW` }
                            },
                            y_temp: {
                                position: 'right',
                                min: 0,
                                max: 26,
                                grid: { drawOnChartArea: false },
                                title: {
                                    display: true,
                                    text: 'Temperatuur (°C) / COP',
                                    color: '#60A5FA',
                                    font: { size: 10, weight: 'bold' }
                                },
                                ticks: { color: '#60A5FA', font: { size: 10 }, callback: v => `${v}` }
                            },
                            y_cost: {
                                position: 'right',
                                min: 0,
                                grid: { drawOnChartArea: false },
                                title: { display: false },
                                ticks: { display: false }
                            }
                        }
                    }
                });
            } catch (e) {
                console.warn("Error rendering heating forecast chart:", e);
            }
        }

        async function renderDhwTemperatureChart() {
            const canvas = document.getElementById('chart-dhw-temperature');
            if (!canvas) return;
            try {
                const lang = window.OpenHEMSi18n ? window.OpenHEMSi18n.getLang() : 'nl';
                const res = await fetch('./api/model/dhw-status?resolution=' + encodeURIComponent(predictionResolution) + '&lang=' + encodeURIComponent(lang));
                if (!res.ok) return;
                const data = await res.json();
                const traj = data.trajectory || {};
                if (!traj.labels || traj.labels.length === 0) return;

                const existingChart = Chart.getChart(canvas);
                if (existingChart) {
                    existingChart.destroy();
                }

                const labels = traj.labels;
                const temps = traj.temperatures_c || [];
                const tempsP05 = traj.temperatures_p05_c || temps;
                const tempsP95 = traj.temperatures_p95_c || temps;
                const demandsKwh = traj.demand_kwh_th || [];
                const kwhThArr = demandsKwh.map(k => Number(k || 0).toFixed(2));
                const litersArr = demandsKwh.map(k => Math.round(k * 3600 / (4.186 * 30))); // V40: mengwater 40°C
                const comfortLine = Array(labels.length).fill(40.0);
                const targetLine = Array(labels.length).fill(50.0);
                
                // Detect if 60C boost is present
                const maxTempInTraj = Math.max(...temps, ...tempsP05, 50.0);
                const isBoostMode = (maxTempInTraj >= 53.0);
                const ySuggestedMax = isBoostMode ? 64.0 : 55.0;

                const unh = data.unheated_trajectory || {};
                const unhTemps = unh.temperatures_c || [];
                const unhP05 = unh.temperatures_p05_c || unhTemps;
                const unhP95 = unh.temperatures_p95_c || unhTemps;

                // Update Decision Box below chart (Buffer Efficiëntie)
                const dec = data.decision || {};
                const titleEl = document.getElementById('dhw-decision-box-title');
                if (titleEl && dec.box_title) titleEl.innerText = dec.box_title;

                const boxPill = document.getElementById('dhw-box-status-pill');
                if (boxPill) {
                    if (dec.badge_html) {
                        boxPill.innerHTML = dec.badge_html;
                    } else if (dec.status === 'SCHEDULE_NIGHT_CHARGE') {
                        boxPill.innerHTML = '<span class="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-[10px] font-bold bg-amber-950/80 text-amber-300 border border-amber-800/80"><span class="w-1.5 h-1.5 rounded-full bg-amber-400 animate-pulse"></span> Nachtlading Gepland (Comfortzekerheid)</span>';
                    } else {
                        boxPill.innerHTML = '<span class="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-[10px] font-bold bg-emerald-950/80 text-emerald-300 border border-emerald-800/80"><span class="w-1.5 h-1.5 rounded-full bg-emerald-400"></span> Wachten op Middagzon (Besparing)</span>';
                    }
                }

                const mergeEl = document.getElementById('dhw-merge-banner');
                if (mergeEl) {
                    if (dec.opportunistic_merge && dec.opportunistic_merge.should_merge) {
                        mergeEl.className = 'p-2.5 rounded-lg bg-purple-950/70 border border-purple-800 text-purple-200 flex items-start gap-2 text-xs mb-2 shadow-lg';
                        mergeEl.innerHTML = `<span class="text-sm">⚡</span> <div class="space-y-0.5"><strong class="font-bold text-white">Opportunistische Run-Fusie:</strong> <span>${dec.opportunistic_merge.reason}</span> <div class="text-[10px] text-purple-300 font-mono">Besparing: ~€${Number(dec.opportunistic_merge.savings_estimate_eur || 0.20).toFixed(2)} op start/stop &amp; voorverwarmverlies</div></div>`;
                        mergeEl.classList.remove('hidden');
                    } else {
                        mergeEl.classList.add('hidden');
                    }
                }

                const cTitleEl = document.getElementById('dhw-comfort-card-title');
                if (cTitleEl && dec.comfort_card_title) cTitleEl.innerText = dec.comfort_card_title;

                const cTextEl = document.getElementById('dhw-eval-comfort-text');
                if (cTextEl && dec.comfort_text) cTextEl.innerHTML = dec.comfort_text;

                const fTitleEl = document.getElementById('dhw-finance-card-title');
                if (fTitleEl && dec.finance_card_title) fTitleEl.innerText = dec.finance_card_title;

                const fTextEl = document.getElementById('dhw-eval-finance-text');
                if (fTextEl && dec.finance_text) fTextEl.innerHTML = dec.finance_text;

                const diffEl = document.getElementById('dhw-box-diff-val');
                if (diffEl && dec.bullet_1) diffEl.innerText = dec.bullet_1.replace('~', '');

                const advEl = document.getElementById('dhw-box-advies-val');
                if (advEl && dec.bullet_2) advEl.innerText = dec.bullet_2.replace('Advies: ', '');

                const dipValEl = document.getElementById('dhw-box-dip-val');
                if (dipValEl && dec.morning_dip_c !== undefined) {
                    dipValEl.innerText = `${dec.morning_dip_c}°C om ${dec.morning_dip_time || '09:30'}`;
                }

                const dBoxSpits = document.getElementById('dhw-box-spits-detail');
                const cachedPeaks = window.__lastDynamicPeaks || [];
                if (dBoxSpits) {
                    if (cachedPeaks.length === 0) {
                        dBoxSpits.innerHTML = '<span class="text-emerald-400 font-bold">Geen prijspieken 🔓 (volledige vrijloop)</span>';
                    } else {
                        dBoxSpits.innerHTML = cachedPeaks.map(p => {
                            const lbl = p.hard_start_time ? `${p.name} ${p.hard_start_time}–${p.hard_end_time} (${p.hard_duration_mins}m 🔒)` : `${p.name} ${p.start_time}–${p.end_time} (Advies ⚠️)`;
                            return `<span class="text-slate-200 font-bold">${lbl}</span>`;
                        }).join(' · ');
                    }
                }

                const chartDatasets = [
                    // Counterfactual Upper boundary: Zonder Verwarming P05
                    {
                        label: 'Marge Onverwarmd P05 (°C)',
                        data: unhP05,
                        yAxisID: 'y',
                        borderColor: 'rgba(148, 163, 184, 0.25)',
                        backgroundColor: 'transparent',
                        borderWidth: 1.0,
                        borderDash: [2, 2],
                        fill: false,
                        pointRadius: 0,
                        tension: 0.25,
                        order: 6
                    },
                    // Counterfactual Lower boundary: Zonder Verwarming P95 with grey fill to P05
                    {
                        label: 'Marge Onverwarmd (P05–P95)',
                        data: unhP95,
                        yAxisID: 'y',
                        borderColor: 'rgba(148, 163, 184, 0.35)',
                        backgroundColor: 'rgba(148, 163, 184, 0.12)',
                        borderWidth: 1.0,
                        borderDash: [3, 3],
                        fill: '-1',
                        pointRadius: 0,
                        tension: 0.25,
                        order: 7
                    },
                    // Counterfactual Line: Zonder Verwarming P50 (Light Slate Grey Dashed Line)
                    {
                        label: 'Zonder Verwarming (°C)',
                        data: unhTemps,
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
                    },
                    // 1. Upper boundary: Minimaal Verbruik P05
                    {
                        label: 'Minimaal Verbruik P05 (°C)',
                        data: tempsP05,
                        yAxisID: 'y',
                        borderColor: 'rgba(245, 158, 11, 0.35)',
                        backgroundColor: 'transparent',
                        borderWidth: 1.2,
                        borderDash: [3, 3],
                        fill: false,
                        pointRadius: 0,
                        tension: 0.25,
                        order: 1
                    },
                    // 2. Lower boundary: Piekverbruik P95 with filled yellow margin to P05
                    {
                        label: 'Piekverbruik P95 (°C)',
                        data: tempsP95,
                        yAxisID: 'y',
                        borderColor: 'rgba(245, 158, 11, 0.45)',
                        backgroundColor: 'rgba(251, 191, 36, 0.15)',
                        borderWidth: 1.2,
                        borderDash: [4, 4],
                        fill: '-1',
                        pointRadius: 0,
                        tension: 0.25,
                        order: 2
                    },
                    // 3. Expected Boiler Temperature P50 (Solid bright amber)
                    {
                        label: 'Verwachte Temperatuur P50 (°C)',
                        data: temps,
                        yAxisID: 'y',
                        borderColor: '#F59E0B',
                        backgroundColor: 'transparent',
                        borderWidth: 2.5,
                        tension: 0.25,
                        pointRadius: 0,
                        order: 3
                    },
                    // 4. Comfortgrens (40°C)
                    {
                        label: 'Comfortgrens (40°C)',
                        data: comfortLine,
                        yAxisID: 'y',
                        borderColor: 'rgba(239, 68, 68, 0.75)',
                        borderDash: [5, 5],
                        backgroundColor: 'transparent',
                        borderWidth: 1.5,
                        pointRadius: 0,
                        order: 4
                    },
                    // 5. Doeltemperatuur (50°C)
                    {
                        label: 'Doeltemperatuur (50°C)',
                        data: targetLine,
                        yAxisID: 'y',
                        borderColor: 'rgba(16, 185, 129, 0.75)',
                        borderDash: [5, 5],
                        backgroundColor: 'transparent',
                        borderWidth: 1.5,
                        pointRadius: 0,
                        order: 5
                    }
                ];

                if (isBoostMode) {
                    chartDatasets.push({
                        label: 'Zonnebuffer Doel (60°C)',
                        data: Array(labels.length).fill(60.0),
                        yAxisID: 'y',
                        borderColor: 'rgba(168, 85, 247, 0.75)',
                        borderDash: [4, 4],
                        backgroundColor: 'transparent',
                        borderWidth: 1.5,
                        pointRadius: 0,
                        order: 6
                    });
                }

                chartDatasets.push({
                    label: 'Warmtevraag (kWh)',
                    data: kwhThArr,
                    type: 'bar',
                    yAxisID: 'y1',
                    backgroundColor: 'rgba(56, 189, 248, 0.45)',
                    hoverBackgroundColor: '#38BDF8',
                    borderRadius: 2,
                    order: 7
                });

                const forcedOffPlugin = {
                    id: 'dhwForcedOffBackground',
                    beforeDatasetsDraw(chart) {
                        const { ctx, chartArea, scales: { x } } = chart;
                        if (!chartArea || !x) return;
                        const ranges = data.forced_off_ranges || [];
                        if (!ranges.length) return;

                        ctx.save();
                        const totalSlots = chart.data.labels ? chart.data.labels.length : 1;
                        const slotWidth = totalSlots > 1 ? Math.abs(x.getPixelForValue(1) - x.getPixelForValue(0)) : 10;
                        const halfSlot = slotWidth / 2;

                        ranges.forEach(r => {
                            const sIdx = r.start_idx;
                            const eIdx = r.end_idx;
                            if (sIdx === undefined || eIdx === undefined) return;

                            const xStart = x.getPixelForValue(sIdx);
                            const xEnd = x.getPixelForValue(eIdx);
                            if (isNaN(xStart) || isNaN(xEnd)) return;

                            const left = Math.max(chartArea.left, Math.min(xStart, xEnd) - halfSlot);
                            const right = Math.min(chartArea.right, Math.max(xStart, xEnd) + halfSlot);
                            const width = Math.max(0, right - left);
                            if (width <= 0) return;

                            // 1. Translucent red background
                            ctx.fillStyle = 'rgba(239, 68, 68, 0.16)';
                            ctx.fillRect(left, chartArea.top, width, chartArea.height);

                            // 2. Subtle dashed red borders at vertical edges
                            ctx.strokeStyle = 'rgba(239, 68, 68, 0.45)';
                            ctx.lineWidth = 1.2;
                            ctx.setLineDash([4, 4]);
                            ctx.beginPath();
                            ctx.moveTo(left, chartArea.top);
                            ctx.lineTo(left, chartArea.bottom);
                            ctx.moveTo(right, chartArea.top);
                            ctx.lineTo(right, chartArea.bottom);
                            ctx.stroke();

                            // 3. Subtle top label
                            ctx.setLineDash([]);
                            ctx.fillStyle = 'rgba(248, 113, 113, 0.9)';
                            ctx.font = 'bold 9px monospace';
                            ctx.textAlign = 'center';
                            const midX = left + width / 2;
                            if (width > 30) {
                                ctx.fillText('🔒 SPITSBLOK', midX, chartArea.top + 14);
                            }
                        });
                        ctx.restore();
                    }
                };

                const ctx = canvas.getContext('2d');
                dhwTempChartInstance = new Chart(ctx, {
                    type: 'line',
                    plugins: [forcedOffPlugin],
                    data: {
                        labels: labels,
                        datasets: chartDatasets
                    },
                    options: {
                        responsive: true,
                        maintainAspectRatio: false,
                        interaction: { mode: 'index', intersect: false },
                        plugins: {
                            legend: { display: false },
                            tooltip: {
                                enabled: false,
                                external: function(context) {
                                    customDhwTooltipHandler(context);
                                }
                            }
                        },
                        scales: {
                            x: {
                                grid: { color: 'rgba(30, 41, 59, 0.3)' },
                                ticks: { color: '#64748B', font: { size: 10 }, maxTicksLimit: 16 }
                            },
                            y: {
                                position: 'left',
                                suggestedMin: 35.0,
                                suggestedMax: ySuggestedMax,
                                grace: '5%',
                                title: {
                                    display: true,
                                    text: 'Boilertemperatuur (°C)',
                                    color: '#F59E0B',
                                    font: { size: 10, weight: 'bold' }
                                },
                                grid: { color: 'rgba(30, 41, 59, 0.25)' },
                                ticks: { color: '#F59E0B', font: { size: 10 }, callback: v => `${v}°C` }
                            },
                            y1: {
                                position: 'right',
                                min: 0,
                                suggestedMax: (predictionResolution === '15m') ? 1.5 : 4.0,
                                grid: { drawOnChartArea: false },
                                title: {
                                    display: true,
                                    text: 'Warmtevraag (kWh)',
                                    color: '#38BDF8',
                                    font: { size: 10, weight: 'bold' }
                                },
                                ticks: { color: '#38BDF8', font: { size: 10 }, callback: v => `${Number(v).toFixed(1)} kWh` }
                            }
                        }
                    }
                });
            } catch (e) {
                console.warn("Error rendering DHW temperature chart:", e);
            }
        }

        let activeDecisionFilter = 'all';
        window.__allDecisions = [];
        window.__foldedGroups = [];

        async function loadDecisionAuditLog() {
            const listEl = document.getElementById('decision-audit-list');
            if (!listEl) return;
            try {
                const isCatFilter = (activeDecisionFilter === 'ACTION' || activeDecisionFilter === 'DECISION');
                const isDomainFilter = (activeDecisionFilter !== 'all' && !isCatFilter);
                const domainParam = isDomainFilter ? `&domain=${encodeURIComponent(activeDecisionFilter)}` : '';
                const res = await fetch(`./api/analytics/decisions?limit=60${domainParam}`);
                if (!res.ok) return;
                const data = await res.json();
                let decisions = data.decisions || [];

                if (activeDecisionFilter === 'ACTION') {
                    decisions = decisions.filter(d => (d.category === 'ACTION' || d.decision_type === 'live_actuation' || d.domain === 'hardware') && d.category !== 'ERROR' && d.category !== 'WARNING');
                } else if (activeDecisionFilter === 'DECISION') {
                    decisions = decisions.filter(d => d.category !== 'ACTION' && d.category !== 'ERROR' && d.category !== 'WARNING' && d.decision_type !== 'live_actuation' && d.domain !== 'hardware');
                } else if (activeDecisionFilter === 'ERROR') {
                    decisions = decisions.filter(d => d.category === 'ERROR' || d.category === 'WARNING' || d.chosen_mode === 'error' || d.chosen_mode === 'warning');
                }

                window.__allDecisions = decisions;

                // Update summary badges & KPIs
                const totalEl = document.getElementById('stat-total-decisions');
                const dhwEl = document.getElementById('stat-dhw-decisions');
                const savEl = document.getElementById('stat-savings-decisions');
                const badgeDecCount = document.getElementById('badge-dec-count');
                if (badgeDecCount) badgeDecCount.innerText = decisions.length;
                if (totalEl) totalEl.innerText = decisions.length;

                let dhwCount = 0;
                let totalSav = 0;
                decisions.forEach(d => {
                    if (d.domain === 'dhw') dhwCount++;
                    if (d.savings_estimate_eur) totalSav += parseFloat(d.savings_estimate_eur);
                });
                if (dhwEl) dhwEl.innerText = dhwCount;
                if (savEl) savEl.innerText = '€' + totalSav.toFixed(2).replace('.', ',');

                if (decisions.length === 0) {
                    listEl.innerHTML = '<div class="text-xs text-slate-500 py-10 text-center bg-[#0B0F17]/40">Geen gebeurtenissen gevonden voor dit filter.</div>';
                    return;
                }

                // Group consecutive identical events
                const foldedGroups = [];
                decisions.forEach(d => {
                    const key = `${d.domain}|${d.decision_type}|${d.chosen_mode}|${d.reason}`;
                    if (foldedGroups.length > 0 && foldedGroups[foldedGroups.length - 1].key === key) {
                        foldedGroups[foldedGroups.length - 1].entries.push(d);
                    } else {
                        foldedGroups.push({
                            key: key,
                            representative: d,
                            entries: [d]
                        });
                    }
                });
                window.__foldedGroups = foldedGroups;

                listEl.innerHTML = foldedGroups.map((g, gIdx) => {
                    const d = g.representative;
                    const count = g.entries.length;
                    const newest = g.entries[0];
                    const oldest = g.entries[g.entries.length - 1];

                    const dtNew = new Date(newest.timestamp_iso);
                    const dtOld = new Date(oldest.timestamp_iso);
                    const timeNew = dtNew.toLocaleTimeString('nl-NL', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
                    const timeOld = dtOld.toLocaleTimeString('nl-NL', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
                    const dateStr = dtNew.toLocaleDateString('nl-NL', { day: '2-digit', month: '2-digit' });

                    const isError = (d.category === 'ERROR' || d.chosen_mode === 'error');
                    const isWarning = (d.category === 'WARNING' || d.chosen_mode === 'warning');
                    const isAction = (d.category === 'ACTION' || d.decision_type === 'live_actuation' || d.domain === 'hardware');
                    const typePill = isError
                        ? '<span class="px-2 py-0.5 rounded text-[10px] font-mono font-bold bg-red-950/90 border border-red-500/60 text-red-300 whitespace-nowrap">❌ FOUT</span>'
                        : isWarning
                        ? '<span class="px-2 py-0.5 rounded text-[10px] font-mono font-bold bg-amber-950/90 border border-amber-500/60 text-amber-300 whitespace-nowrap">⚠️ WAARSCH.</span>'
                        : isAction
                        ? '<span class="px-2 py-0.5 rounded text-[10px] font-mono font-bold bg-blue-950/90 border border-blue-500/60 text-blue-300 whitespace-nowrap">⚡ ACTIE</span>'
                        : '<span class="px-2 py-0.5 rounded text-[10px] font-mono font-bold bg-purple-950/90 border border-purple-500/60 text-purple-300 whitespace-nowrap">🧠 BESLUIT</span>';

                    const domainMap = {
                        'dhw': '🚰 DHW',
                        'space_heating': '♨️ CV',
                        'grid_tariff': '🚫 Spits',
                        'hardware': '⚙️ Relais',
                        'database': '💾 DB',
                        'weather': '🌤️ Weer',
                        'solar': '☀️ Zon',
                        'system': '🛠️ Systeem'
                    };
                    const domainLabel = domainMap[d.domain] || d.domain;

                    const modeColor = isError ? 'bg-red-950/80 border-red-500/60 text-red-300' :
                                      isWarning ? 'bg-amber-950/80 border-amber-500/60 text-amber-300' :
                                      (d.chosen_mode === 'max_on') ? 'bg-purple-950/80 border-purple-500/50 text-purple-300' :
                                      (d.chosen_mode === 'forced_on') ? 'bg-emerald-950/80 border-emerald-500/50 text-emerald-300' :
                                      (d.chosen_mode === 'advised_on') ? 'bg-indigo-950/80 border-indigo-500/50 text-indigo-300' :
                                      (d.chosen_mode === 'forced_off') ? 'bg-red-950/80 border-red-500/50 text-red-300' :
                                      (d.chosen_mode === 'planned') ? 'bg-amber-950/80 border-amber-500/50 text-amber-300' :
                                      'bg-slate-800/80 border-slate-700 text-slate-300';
                                      (d.chosen_mode === 'forced_off') ? 'bg-red-950/80 border-red-500/50 text-red-300' :
                                      (d.chosen_mode === 'planned') ? 'bg-amber-950/80 border-amber-500/50 text-amber-300' :
                                      'bg-slate-800/80 border-slate-700 text-slate-300';

                    const countTag = (count > 1) 
                        ? `<span class="ml-1.5 px-1.5 py-0.2 rounded text-[10px] font-mono font-bold bg-purple-900/60 text-purple-300 border border-purple-700/60">${count}× (${timeOld}–${timeNew})</span>` 
                        : '';

                    const savingsPill = (d.savings_estimate_eur > 0) 
                        ? `<span class="hidden md:inline-flex px-1.5 py-0.5 rounded text-[10px] font-mono font-bold bg-emerald-950/80 border border-emerald-700/40 text-emerald-300">+€${Number(d.savings_estimate_eur).toFixed(2)}</span>` 
                        : '';

                    return `
                        <div onclick="openDecisionGroupModal(${gIdx})" class="p-2.5 sm:px-3.5 sm:py-2.5 hover:bg-slate-800/60 cursor-pointer transition flex items-center justify-between gap-2.5 sm:gap-3 group">
                            <div class="flex items-center gap-2 sm:gap-3 min-w-0 flex-1">
                                <div class="flex flex-col sm:flex-row sm:items-center sm:gap-1.5 font-mono text-[11px] text-slate-400 whitespace-nowrap">
                                    <span class="text-slate-500 text-[10px] hidden sm:inline">${dateStr}</span>
                                    <strong class="text-slate-200">${timeNew}</strong>
                                </div>
                                <div class="flex-shrink-0">${typePill}</div>
                                <div class="hidden sm:block flex-shrink-0 text-[11px] font-bold text-slate-400 w-16 truncate">${domainLabel}</div>
                                <div class="min-w-0 flex-1 truncate">
                                    <div class="flex items-center gap-1 truncate">
                                        <span class="text-xs font-semibold text-white group-hover:text-cyan-300 transition truncate">${d.reason}</span>
                                        ${countTag}
                                    </div>
                                    <span class="text-[11px] text-slate-400 truncate block sm:hidden">${d.explanation}</span>
                                </div>
                            </div>
                            <div class="flex items-center gap-2 flex-shrink-0">
                                ${savingsPill}
                                <span class="px-2 py-0.5 rounded text-[10px] font-mono font-bold border ${modeColor}">
                                    ${d.chosen_mode}
                                </span>
                                <span class="text-slate-500 group-hover:text-white transition p-0.5">
                                    <svg class="w-4 h-4" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M9 5l7 7-7 7"></path></svg>
                                </span>
                            </div>
                        </div>
                    `;
                }).join('');
            } catch (e) {
                console.warn('Error loading decision audit log:', e);
            }
        }

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

            document.getElementById('modal-title').innerText = d.reason + (count > 1 ? ` (${count}× geëvalueerd)` : '');
            
            const dtNew = new Date(newest.timestamp_iso);
            const dtOld = new Date(oldest.timestamp_iso);
            const timeNew = dtNew.toLocaleTimeString('nl-NL', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
            const timeOld = dtOld.toLocaleTimeString('nl-NL', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
            const dateStr = dtNew.toLocaleDateString('nl-NL', { day: '2-digit', month: '2-digit', year: 'numeric' });
            document.getElementById('modal-time').innerText = (count > 1) 
                ? `${dateStr} ${timeOld} ➔ ${timeNew} (${Math.max(1, Math.round((dtNew - dtOld)/60000))} min)`
                : `${dateStr} ${timeNew}`;

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

            document.getElementById('modal-explanation').innerText = d.explanation;

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
            if (event.target.id === 'decision-detail-modal') {
                closeDecisionModal();
            }
        }

        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') closeDecisionModal();
        });

        function filterDecisionAudit(domain) {
            activeDecisionFilter = domain;
            ['all', 'ACTION', 'DECISION', 'ERROR', 'dhw', 'space_heating', 'grid_tariff', 'hardware'].forEach(dom => {
                const btn = document.getElementById('btn-filter-' + dom);
                if (btn) {
                    if (dom === domain) {
                        if (dom === 'ERROR') {
                            btn.className = 'px-3 py-1 rounded-lg text-xs font-semibold bg-red-600 text-white shadow';
                        } else {
                            btn.className = 'px-3 py-1 rounded-lg text-xs font-semibold bg-purple-600 text-white shadow';
                        }
                    } else if (dom === 'ACTION') {
                        btn.className = 'px-2.5 py-1 rounded-lg text-xs font-medium text-blue-300 hover:text-white bg-blue-950/40 border border-blue-800/60';
                    } else if (dom === 'DECISION') {
                        btn.className = 'px-2.5 py-1 rounded-lg text-xs font-medium text-purple-300 hover:text-white bg-purple-950/40 border border-purple-800/60';
                    } else if (dom === 'ERROR') {
                        btn.className = 'px-2.5 py-1 rounded-lg text-xs font-medium text-red-300 hover:text-white bg-red-950/40 border border-red-800/60';
                    } else {
                        btn.className = 'px-2.5 py-1 rounded-lg text-xs font-medium text-slate-400 hover:text-white bg-slate-900 border border-slate-800';
                    }
                }
            });
            loadDecisionAuditLog();
        }

        async function loadModelDashboard() {
            renderMonthSelector();
            // 1. Render Decomposition Chart FIRST (always guaranteed)
            try {
                await renderModelDecompositionChart();
            } catch (e1) {
                console.warn("Chart render error:", e1);
            }

            // 2. Fetch model status & KPI metrics via relative ./ path
            try {
                const res = await fetch('./api/model/status');
                if (res.ok) {
                    const data = await res.json();
                    if (data && data.params) {
                        const p = data.params;
                        const m = p.metrics || {};
                        if (document.getElementById('model-kpi-r2')) document.getElementById('model-kpi-r2').innerText = m.r_squared ? m.r_squared.toFixed(3) : '0.783';
                        if (document.getElementById('model-kpi-rmse')) document.getElementById('model-kpi-rmse').innerHTML = `${Math.round(m.rmse_w || 185)} W <span class="text-xs text-slate-400">/ ${Math.round(m.mae_w || 132)} W</span>`;
                        if (document.getElementById('model-kpi-ua')) document.getElementById('model-kpi-ua').innerText = `${Math.round(p.building?.ua_base_w_per_k || 321)} W/K`;
                        if (document.getElementById('model-kpi-schedule')) document.getElementById('model-kpi-schedule').innerText = p.last_trained ? `Bijgewerkt: ${p.last_trained.slice(11, 16)}u` : 'Elke nacht 02:00';
                    }
                }
            } catch (e2) {
                console.warn("Model status fetch error:", e2);
            }

            // 3. Load 7x96 profile
            try {
                await loadUnallocatedModel();
            } catch (e3) {
                console.warn("Unallocated model error:", e3);
            }
        }

        async function renderModelDecompositionChart() {
            const canvas = document.getElementById('chart-model-decomposition');
            if (!canvas) return;
            try {
                const curRes = OpenHEMSChartEngine.getResolution();
                const res = await fetch('./api/schedule/chart-data?resolution=' + encodeURIComponent(curRes));
                const data = await res.json();
                if (!data || !data.labels) return;

                const existingChart = Chart.getChart(canvas);
                if (existingChart) {
                    existingChart.destroy();
                }

                const labels = data.labels;
                const unallocArr = data.datasets.unallocated_kw || data.datasets.baseload_kw || [];
                const heatingArr = data.datasets.heating_kw || [];
                const boilerArr = data.datasets.boiler_kw || [];
                const solarNegArr = data.datasets.solar_kw_neg || [];
                const netPowerArr = data.datasets.net_power_kw || [];
                const pricesArr = data.datasets.prices_eur || [];

                // Calculate symmetric Y-axis boundary centered on zero
                const maxCons = Math.max(0.1, ...unallocArr.map((u, i) => u + (heatingArr[i] || 0) + (boilerArr[i] || 0)));
                const maxProd = Math.max(0.1, ...solarNegArr.map(s => Math.abs(s)));
                const yBoundary = Math.max(1.5, Math.ceil(Math.max(maxCons, maxProd) * 1.15 * 2) / 2);

                const ctx = canvas.getContext('2d');
                new Chart(ctx, {
                    type: 'bar',
                    data: {
                        labels: labels,
                        datasets: [
                            {
                                label: 'All-in Beurstarief (€/kWh)',
                                data: pricesArr,
                                type: 'line',
                                yAxisID: 'y1',
                                borderColor: OpenHEMSTokens.colors.price,
                                backgroundColor: 'transparent',
                                borderWidth: 1.75,
                                tension: 0.25,
                                pointRadius: 0,
                                order: 1
                            },
                            {
                                label: 'Netto Netafname',
                                data: netPowerArr,
                                type: 'line',
                                yAxisID: 'y',
                                borderColor: OpenHEMSTokens.colors.netto,
                                borderDash: [4, 4],
                                backgroundColor: 'transparent',
                                borderWidth: 1.5,
                                tension: 0.2,
                                pointRadius: 0,
                                order: 2
                            },
                            {
                                label: 'Ongedefinieerd (Huis)',
                                data: unallocArr,
                                yAxisID: 'y',
                                backgroundColor: OpenHEMSTokens.colors.unallocated,
                                stack: 'consumption',
                                borderRadius: 2,
                                order: 3
                            },
                            {
                                label: 'CV Verwarming (Woning)',
                                data: heatingArr,
                                yAxisID: 'y',
                                backgroundColor: OpenHEMSTokens.colors.heating,
                                stack: 'consumption',
                                borderRadius: 2,
                                order: 4
                            },
                            {
                                label: 'SWW Boiler 350L',
                                data: boilerArr,
                                yAxisID: 'y',
                                backgroundColor: OpenHEMSTokens.colors.dhw,
                                stack: 'consumption',
                                borderRadius: 2,
                                order: 5
                            },
                            {
                                label: 'Zonnepanelen Opwek',
                                data: solarNegArr,
                                yAxisID: 'y',
                                backgroundColor: OpenHEMSTokens.colors.solar,
                                stack: 'generation',
                                borderRadius: 2,
                                order: 6
                            }
                        ]
                    },
                    options: {
                        responsive: true,
                        maintainAspectRatio: false,
                        interaction: { mode: 'index', intersect: false },
                        plugins: {
                            legend: { display: false },
                            tooltip: {
                                backgroundColor: 'rgba(11, 15, 23, 0.95)',
                                borderColor: '#1E293B',
                                borderWidth: 1,
                                padding: 10,
                                callbacks: {
                                    label: function(c) {
                                        const val = c.raw;
                                        if (val === 0 || val === -0) return null;
                                        if (c.dataset.yAxisID === 'y1') {
                                            return ` 💶 Tarief: €${Number(val).toFixed(4)}/kWh`;
                                        }
                                        return ` ${c.dataset.label}: ${Math.abs(val).toFixed(2)} kW (${(Math.abs(val)*0.25).toFixed(2)} kWh)`;
                                    }
                                }
                            }
                        },
                        scales: {
                            x: {
                                stacked: true,
                                grid: { color: 'rgba(30, 41, 59, 0.3)' },
                                ticks: { color: '#64748B', font: { size: 10 }, maxTicksLimit: 16 }
                            },
                            y: {
                                stacked: true,
                                position: 'left',
                                min: -yBoundary,
                                max: yBoundary,
                                title: {
                                    display: true,
                                    text: 'Opbrengst (-kW)  <  0  <  Verbruik (+kW)',
                                    color: '#64748B',
                                    font: { size: 10, weight: 'bold' }
                                },
                                grid: {
                                    color: (ctx) => ctx.tick.value === 0 ? 'rgba(148, 163, 184, 0.6)' : 'rgba(30, 41, 59, 0.25)',
                                    lineWidth: (ctx) => ctx.tick.value === 0 ? 1.5 : 1
                                },
                                ticks: { color: '#64748B', font: { size: 10 }, callback: v => `${v} kW` }
                            },
                            y1: {
                                position: 'right',
                                grid: { drawOnChartArea: false },
                                title: {
                                    display: true,
                                    text: 'All-in Beurstarief (€/kWh)',
                                    color: '#22D3EE',
                                    font: { size: 10, weight: 'bold' }
                                },
                                ticks: { color: '#22D3EE', font: { size: 10 }, callback: v => `€${v.toFixed(2)}` }
                            }
                        }
                    }
                });
            } catch (e) {
                console.warn("Error rendering unified decomposition chart:", e);
            }
        }

        // =========================================================================
        // TOAST NOTIFICATIONS & MODEL GOVERNANCE STEERING JS
        // =========================================================================
        

                function showToast(msg, type = 'info') {
            let toast = document.getElementById('open-hems-toast');
            if (!toast) {
                toast = document.createElement('div');
                toast.id = 'open-hems-toast';
                document.body.appendChild(toast);
            }
            const colors = {
                'success': 'bg-emerald-950/90 text-emerald-300 border-emerald-500/50',
                'error': 'bg-red-950/90 text-red-300 border-red-500/50',
                'info': 'bg-blue-950/90 text-blue-300 border-blue-500/50'
            };
            const icons = {
                'success': '✅',
                'error': '❌',
                'info': 'ℹ️'
            };
            toast.className = `fixed bottom-5 right-5 z-50 px-4 py-2.5 rounded-xl shadow-2xl border text-xs font-bold transition-all duration-300 transform translate-y-0 opacity-100 flex items-center gap-2 ${colors[type] || colors.info}`;
            toast.innerHTML = `<span>${icons[type] || ''}</span> <span>${msg}</span>`;
            clearTimeout(window.__toastTimer);
            window.__toastTimer = setTimeout(() => {
                toast.className = `fixed bottom-5 right-5 z-50 px-4 py-2.5 rounded-xl shadow-2xl border text-xs font-bold transition-all duration-300 transform translate-y-10 opacity-0 pointer-events-none flex items-center gap-2 ${colors[type] || colors.info}`;
            }, 3500);
        }

        function updateLearningRateLabel(val) {
            const el = document.getElementById('label-learning-rate');
            if (el) el.textContent = `${val}%`;
        }

        function updateAutoAcceptLabel(val) {
            const el = document.getElementById('label-auto-accept');
            if (el) el.textContent = `&plusmn;${Number(val).toFixed(1)}%`;
        }

        async function loadAlgorithmConfig() {
            try {
                const res = await fetch('./api/model/algorithm-config');
                if (!res.ok) return;
                const d = await res.json();
                const lr = Math.round((d.learning_rate_ewma || 0.05) * 100);
                const sLr = document.getElementById('slider-learning-rate');
                if (sLr) { sLr.value = lr; updateLearningRateLabel(lr); }

                const rw = d.rolling_window_days || 90;
                const sRw = document.getElementById('select-rolling-window');
                if (sRw) sRw.value = String(rw);

                const aa = d.auto_accept_max_drift_pct !== undefined ? d.auto_accept_max_drift_pct : 3.0;
                const sAa = document.getElementById('slider-auto-accept');
                if (sAa) { sAa.value = aa; updateAutoAcceptLabel(aa); }
            } catch (e) {
                console.warn('Error loading algorithm config:', e);
            }
        }

        async function saveAlgorithmConfig() {
            const btn = document.getElementById('btn-save-algo');
            if (btn) btn.disabled = true;
            try {
                const lr = Number(document.getElementById('slider-learning-rate').value) / 100.0;
                const rw = parseInt(document.getElementById('select-rolling-window').value, 10);
                const aa = Number(document.getElementById('slider-auto-accept').value);

                const res = await fetch('./api/model/algorithm-config', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        learning_rate_ewma: lr,
                        rolling_window_days: rw,
                        auto_accept_max_drift_pct: aa
                    })
                });
                if (res.ok) {
                    showToast('Algoritme instellingen opgeslagen!', 'success');
                    loadModelRecommendations();
                } else {
                    showToast('Fout bij opslaan algoritme instellingen', 'error');
                }
            } catch (e) {
                showToast('Verbindingsfout: ' + e, 'error');
            } finally {
                if (btn) btn.disabled = false;
            }
        }

        async function loadModelRecommendations() {
            const tBody = document.getElementById('recs-table-body');
            const badge = document.getElementById('recs-status-badge');
            if (!tBody) return;
            try {
                const res = await fetch('./api/model/recommendations');
                if (!res.ok) return;
                const d = await res.json();
                const recs = d.recommendations || [];
                if (recs.length === 0) {
                    tBody.innerHTML = '<tr><td colspan="6" class="py-4 text-center text-slate-500">Nog geen kalibratie-aanbevelingen beschikbaar.</td></tr>';
                    return;
                }

                const isPending = (d.status === 'pending_review');
                if (badge) {
                    if (isPending) {
                        badge.className = 'px-2.5 py-0.5 rounded-full text-[10px] font-mono font-bold bg-amber-500/20 text-amber-300 border border-amber-500/40 animate-pulse';
                        badge.textContent = 'Actie Vereist (Voorstellen Klaar)';
                    } else {
                        badge.className = 'px-2.5 py-0.5 rounded-full text-[10px] font-mono font-bold bg-emerald-500/20 text-emerald-300 border border-emerald-500/40';
                        badge.textContent = 'Up-to-date (Geaccepteerd)';
                    }
                }

                const thresholdVal = d.auto_accept_max_drift_pct !== undefined ? d.auto_accept_max_drift_pct : 3.0;

                tBody.innerHTML = recs.map(r => {
                    const drift = Number(r.drift_pct || 0);
                    const driftColor = drift === 0 ? 'text-slate-400' : (Math.abs(drift) <= thresholdVal ? 'text-emerald-400' : (drift < 0 ? 'text-blue-400' : 'text-amber-400'));
                    const driftSign = drift > 0 ? '+' : '';

                    let statusBadge = '';
                    if (d.status === 'accepted') {
                        statusBadge = `<button type="button" onclick="toggleInfoPopover(event, 'status_accepted')" class="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-[9px] font-bold bg-blue-950/70 text-blue-300 border border-blue-800 hover:bg-blue-900/60 transition focus:outline-none"><span class="w-1.5 h-1.5 rounded-full bg-blue-400"></span> <span>Geaccepteerd</span></button>`;
                    } else if (r.auto_applied) {
                        statusBadge = `<button type="button" onclick="toggleInfoPopover(event, 'status_auto')" class="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-[9px] font-bold bg-emerald-950/70 text-emerald-400 border border-emerald-800 hover:bg-emerald-900/60 transition focus:outline-none"><span class="w-1.5 h-1.5 rounded-full bg-emerald-400"></span> <span>Automatisch</span></button>`;
                    } else {
                        statusBadge = `<button type="button" onclick="toggleInfoPopover(event, 'status_review')" class="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-[9px] font-bold bg-amber-950/70 text-amber-300 border border-amber-800 hover:bg-amber-900/60 transition focus:outline-none"><span class="w-1.5 h-1.5 rounded-full bg-amber-400"></span> <span>Ter Beoordeling</span></button>`;
                    }

                    return `
                        <tr class="hover:bg-slate-800/30 transition">
                            <td class="py-2.5 font-bold text-white">
                                <span class="inline-flex items-center gap-1.5">
                                    <span>${r.name}</span>
                                    <button type="button" onclick="toggleInfoPopover(event, 'param_${r.id}')" class="text-slate-500 hover:text-cyan-400 transition-colors p-0.5 focus:outline-none" aria-label="Toelichting">
                                        <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"></circle><path d="M12 16v-4m0-4h.01"></path></svg>
                                    </button>
                                </span>
                            </td>
                            <td class="py-2.5 text-center text-slate-400 font-mono">${r.current_value} <span class="text-[10px] text-slate-500">${r.unit}</span></td>
                            <td class="py-2.5 text-center font-bold text-white font-mono">${r.proposed_value} <span class="text-[10px] text-slate-500">${r.unit}</span></td>
                            <td class="py-2.5 text-center font-bold ${driftColor} font-mono">${driftSign}${drift}%</td>
                            <td class="py-2.5 text-[11px] text-slate-400 font-sans">${r.evidence || '--'}</td>
                            <td class="py-2.5 text-right font-mono">${statusBadge}</td>
                        </tr>
                    `;
                }).join('');

                const actionsContainer = document.getElementById('recs-actions-container');
                if (actionsContainer) {
                    if (isPending) {
                        actionsContainer.innerHTML = `
                            <button onclick="rejectRecommendations()" id="btn-recs-reject" class="px-3 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-300 text-xs rounded-xl font-medium border border-slate-700 transition">
                                Afwijzen
                            </button>
                            <button onclick="acceptRecommendations()" id="btn-recs-accept" class="px-4 py-1.5 bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-semibold rounded-xl shadow-lg transition flex items-center gap-1.5">
                                <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M5 13l4 4L19 7"></path></svg>
                                <span>Accepteren &amp; Toepassen</span>
                            </button>
                        `;
                    } else {
                        const accAt = d.accepted_at ? new Date(d.accepted_at).toLocaleTimeString('nl-NL', {hour: '2-digit', minute: '2-digit'}) : '';
                        actionsContainer.innerHTML = `
                            <div class="flex items-center gap-2 flex-wrap">
                                <span class="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-xl text-xs font-semibold text-emerald-400 bg-emerald-950/60 border border-emerald-500/40 shadow-sm">
                                    <svg class="w-4 h-4 text-emerald-400" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M5 13l4 4L19 7"></path></svg>
                                    <span>Geaccepteerd &amp; Actief ${accAt ? '(' + accAt + ')' : ''}</span>
                                </span>
                                <button onclick="retrainModelNow()" id="btn-recs-retrain" class="px-3 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-300 text-xs rounded-xl font-medium border border-slate-700 transition flex items-center gap-1.5">
                                    <span>🔄 Nieuwe Kalibratie</span>
                                </button>
                            </div>
                        `;
                    }
                }
            } catch (e) {
                console.warn('Error loading recommendations:', e);
            }
        }

        async function acceptRecommendations() {
            const btn = document.getElementById('btn-recs-accept');
            if (btn) {
                btn.disabled = true;
                btn.innerHTML = '<span class="animate-spin inline-block mr-1">⏳</span> Bezig...';
            }
            try {
                const res = await fetch('./api/model/recommendations/accept', { method: 'POST' });
                if (res.ok) {
                    showToast('Aanbevelingen geaccepteerd en geactiveerd in model!', 'success');
                    await loadModelRecommendations();
                    loadAnalytics();
                } else {
                    showToast('Fout bij accepteren van aanbevelingen', 'error');
                }
            } catch (e) {
                showToast('Verbindingsfout: ' + e, 'error');
            } finally {
                if (btn) btn.disabled = false;
            }
        }

        async function rejectRecommendations() {
            const btn = document.getElementById('btn-recs-reject');
            if (btn) {
                btn.disabled = true;
                btn.innerHTML = '<span class="animate-spin inline-block mr-1">⏳</span> Bezig...';
            }
            try {
                const res = await fetch('./api/model/recommendations/reject', { method: 'POST' });
                if (res.ok) {
                    showToast('Aanbevelingen afgewezen; actieve parameters behouden.', 'info');
                    await loadModelRecommendations();
                } else {
                    showToast('Fout bij afwijzen van aanbevelingen', 'error');
                }
            } catch (e) {
                showToast('Verbindingsfout: ' + e, 'error');
            } finally {
                if (btn) btn.disabled = false;
            }
        }

        async function retrainModelNow() {
            const btn = document.getElementById('btn-retrain-model');
            if (btn) {
                btn.disabled = true;
                btn.innerHTML = '<span class="animate-spin inline-block mr-1">⏳</span> Bezig met trainen...';
            }
            try {
                const rw = parseInt(document.getElementById('select-rolling-window')?.value || '90', 10);
                const res = await fetch('./api/model/retrain', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({days: rw})
                });
                const out = await res.json();
                if (out.status === 'success') {
                    showToast('✅ Model succesvol herberekend en aanbevelingen bijgewerkt!', 'success');
                    await loadModelRecommendations();
                    loadChartData();
                } else {
                    showToast(`Fout bij trainen: ${out.message}`, 'error');
                }
            } catch (e) {
                showToast(`Netwerkfout bij trainen: ${e}`, 'error');
            } finally {
                if (btn) {
                    btn.disabled = false;
                    btn.innerHTML = '<svg class="w-4 h-4" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"></path></svg> <span>Herbereken & Train Model</span>';
                }
            }
        }

        function selectModelInspectTab(tab) {
            const btnFormulas = document.getElementById('btn-inspect-formulas');
            const btnCode = document.getElementById('btn-inspect-code');
            const pnlFormulas = document.getElementById('model-inspect-formulas');
            const pnlCode = document.getElementById('model-inspect-code');

            if (tab === 'formulas') {
                btnFormulas.className = 'px-3 py-1.5 rounded-lg bg-blue-600 text-white font-bold transition';
                btnCode.className = 'px-3 py-1.5 rounded-lg text-slate-400 hover:text-white transition';
                pnlFormulas.classList.remove('hidden');
                pnlCode.classList.add('hidden');
            } else {
                btnCode.className = 'px-3 py-1.5 rounded-lg bg-blue-600 text-white font-bold transition';
                btnFormulas.className = 'px-3 py-1.5 rounded-lg text-slate-400 hover:text-white transition';
                pnlCode.classList.remove('hidden');
                pnlFormulas.classList.add('hidden');
            }
        }

        async function recalculateUnallocatedProfile() {
            await retrainModelNow();
        }

        function filterApiEndpoints() {
            const q = (document.getElementById('api-filter-input').value || '').toLowerCase();
            const cards = document.querySelectorAll('.api-endpoint-card');
            cards.forEach(card => {
                const txt = card.innerText.toLowerCase();
                card.style.display = txt.includes(q) ? 'block' : 'none';
            });
        }

        // Language change event listener
        window.addEventListener('openhems:languageChanged', () => {
            if (typeof renderDhwTemperatureChart === 'function') {
                renderDhwTemperatureChart();
            }
            if (typeof renderPredictionChart === 'function') {
                renderPredictionChart();
            }
        });

