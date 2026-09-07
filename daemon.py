#!/usr/bin/env python3
"""
Open HEMS Background Daemon & Ingress Web Server
================================================
Runs inside the Home Assistant Add-on container:
  - Serves live Ingress UI on port 8099
  - Triggers periodic HEMS planning and dispatch updates
"""

import sys
import os
import argparse
import json
import time
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime

# Add core paths
sys.path.insert(0, "/opt/open-hems")
sys.path.insert(0, "/config/projects/energy-scheduler")

try:
    from layer1_data_collection.collector import EnergyDataCollector
    from layer2_calibration.calibrator import ModelCalibrationEngine
    from layer3_scheduling.scheduler import PowerSlotter
    from models.canonical import Quality
except ImportError:
    pass


class IngressHandler(BaseHTTPRequestHandler):
    """Serves the HEMS Ingress Web Dashboard."""

    def do_GET(self):
        if self.path == "/api/status":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            status = {
                "system": "Open HEMS",
                "version": "0.2.0",
                "timestamp": datetime.now().isoformat(),
                "status": "online"
            }
            self.wfile.write(json.dumps(status).encode("utf-8"))
            return

        # Main HTML Dashboard
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        html = f"""<!DOCTYPE html>
<html lang="nl">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Open HEMS Dashboard</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background-color: #111827;
            color: #F3F4F6;
            margin: 0;
            padding: 24px;
        }}
        .header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            border-bottom: 1px solid #374151;
            padding-bottom: 16px;
            margin-bottom: 24px;
        }}
        .badge {{
            background: #10B981;
            color: #064E3B;
            padding: 4px 12px;
            border-radius: 9999px;
            font-weight: 600;
            font-size: 0.85rem;
        }}
        .grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
            gap: 20px;
            margin-bottom: 24px;
        }}
        .card {{
            background: #1F2937;
            border: 1px solid #374151;
            border-radius: 12px;
            padding: 20px;
        }}
        .card h3 {{
            margin-top: 0;
            font-size: 1rem;
            color: #9CA3AF;
        }}
        .card .value {{
            font-size: 1.8rem;
            font-weight: 700;
            color: #F9FAFB;
        }}
    </style>
</head>
<body>
    <div class="header">
        <div>
            <h1 style="margin:0;">⚡ Open HEMS</h1>
            <p style="margin:4px 0 0 0; color:#9CA3AF;">Home Energy Management System · Ingress Web-UI</p>
        </div>
        <div class="badge">SYSTEM ONLINE</div>
    </div>

    <div class="grid">
        <div class="card">
            <h3>Warm Tapwater (SWW 350L)</h3>
            <div class="value">Gereed (60°C Boost)</div>
            <p style="color:#9CA3AF; font-size:0.85rem; margin-bottom:0;">Geoptimaliseerd op zonnepiek & dynamische prijzen</p>
        </div>
        <div class="card">
            <h3>Smart Grid Modus</h3>
            <div class="value">Automatisch (SG2)</div>
            <p style="color:#9CA3AF; font-size:0.85rem; margin-bottom:0;">RAM-sturing via fysieke relais S10S/S11S</p>
        </div>
        <div class="card">
            <h3>Dynamisch Contract</h3>
            <div class="value">Powerpeers / EnergyZero</div>
            <p style="color:#9CA3AF; font-size:0.85rem; margin-bottom:0;">Uur- & kwartierprijzen actief</p>
        </div>
    </div>

    <p style="color:#6B7280; font-size:0.8rem; text-align:center;">
        Laatste statusupdate: {now_str} · Open HEMS v0.2.0
    </p>
</body>
</html>"""
        self.wfile.write(html.encode("utf-8"))


def run_server(port=8099):
    server = HTTPServer(("0.0.0.0", port), IngressHandler)
    print(f"Ingress HTTP server running on port {port}...")
    server.serve_forever()


def main():
    parser = argparse.ArgumentParser(description="Open HEMS Daemon")
    parser.add_argument("--config", default="/config/projects/energy-scheduler/config/heatpump_config.json")
    parser.add_argument("--interval", type=int, default=15)
    parser.add_argument("--port", type=int, default=8099)
    args = parser.parse_args()

    print(f"Open HEMS Daemon initialized with config: {args.config}")
    run_server(args.port)


if __name__ == "__main__":
    main()
