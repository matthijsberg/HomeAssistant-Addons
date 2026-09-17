"""
Unit / Headless Test: Chart Tooltip Value Extraction
===================================================
Simulates Chart.js tooltip generation and verifies that values are accurately extracted
and never silently default to 0.00 when underlying data is non-zero.
"""

import subprocess
import json
import pytest
from pathlib import Path

APP_JS_PATH = Path(__file__).resolve().parent.parent.parent / "web" / "js" / "app.js"


def test_electricity_prices_tooltip_extraction():
    """Simulates customPricesTooltipHandler and asserts exact value extraction."""
    js_runner = f"""
    // Mock minimal DOM
    global.window = {{}};
    global.predictionResolution = '15m';
    let tooltipHtml = '';
    global.createOrGetTooltipEl = () => ({{
        style: {{}},
        set innerHTML(val) {{ tooltipHtml = val; }}
    }});
    global.positionTooltipCustom = () => {{}};

    // Load customPricesTooltipHandler from app.js
    const fs = require('fs');
    const code = fs.readFileSync('{APP_JS_PATH}', 'utf8');
    
    // Extract customPricesTooltipHandler function
    eval(code.match(/function customPricesTooltipHandler\\(context\\) \\{{[\\s\\S]*?\\n        \\}}/)[0]);

    // Test Case: Peak Solar at 12:00
    const mockChart = {{
        data: {{
            datasets: [
                {{ id: 'solar_forecast', label: 'Expected Solar (kW)', data: [0.0, 1.25, 0.0] }},
                {{ id: 'epex_import', label: 'Import All-in (€/kWh)', data: [0.25, 0.1574, 0.30] }},
                {{ id: 'epex_export', label: 'Export (€/kWh)', data: [0.05, 0.0323, 0.08] }}
            ]
        }}
    }};

    const context = {{
        chart: mockChart,
        tooltip: {{
            opacity: 1,
            body: true,
            dataPoints: [{{ dataIndex: 1 }}],
            title: ['12:00']
        }}
    }};

    customPricesTooltipHandler(context);

    console.log(JSON.stringify({{
        html: tooltipHtml,
        hasSolar: tooltipHtml.includes('1.25 kW'),
        hasImport: tooltipHtml.includes('0.1574'),
        hasExport: tooltipHtml.includes('0.0323'),
        hasZeroBug: tooltipHtml.includes('0.00 kW')
    }}));
    """

    res = subprocess.run(["node", "-e", js_runner], capture_output=True, text=True)
    assert res.returncode == 0, f"Node test failed: {res.stderr}"
    
    out = json.loads(res.stdout.strip())
    assert out["hasSolar"], f"Tooltip failed to extract 1.25 kW solar production: {out['html']}"
    assert out["hasImport"], f"Tooltip failed to extract 0.1574 import price: {out['html']}"
    assert out["hasExport"], f"Tooltip failed to extract 0.0323 export price: {out['html']}"
    assert not out["hasZeroBug"], f"Tooltip suffered from 0.00 kW default bug: {out['html']}"
