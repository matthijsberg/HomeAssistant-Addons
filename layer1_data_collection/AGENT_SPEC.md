# Layer 1: Ingestion & Connectivity — Agent Specification & Guidelines

## 🎯 Layer Scope & Responsibilities
Layer 1 is the **only layer permitted to perform network I/O and external protocol communication**.
Its singular purpose is to ingest, normalize, and store raw telemetry and feed data into canonical schema representations.

### What Layer 1 DOES:
1. Connect to InfluxDB (1.8 & 2.x) and write time-series points via nanosecond Line Protocol.
2. Connect to MQTT brokers (Mosquitto, cloud brokers) and handle publish/subscribe streams.
3. Fetch public API market feeds (EPEX Spot via EnergyZero) and weather/solar forecasts (Open-Meteo).
4. Poll Home Assistant sensors via REST/WebSocket and map raw attributes into `models.canonical.Measurement`.
5. Maintain local atomic hot caches (`energy_feed_cache.json`) with freshness TTL verification.

### What Layer 1 MUST NEVER DO:
- **NO Optimization:** Never decide when to run the heat pump or charge the battery.
- **NO Actuation:** Never switch hardware relays or change thermostat setpoints.
- **NO Thermodynamic Calculations:** Never calculate $UA$-values, COP, or solar matrices (that is Layer 2).
- **NO Synthetic/Mock Data:** Never fabricate fake wattages, synthetic curves, or placeholder temperatures. If a sensor is offline, raise `DataUnavailableError` or flag as `Quality.STALE`.

---

## 🔌 Defined Interfaces
See `interfaces.py`:
- `ITimeSeriesStorage`: Line protocol writer and history reader (`write_measurement`, `query_recent`).
- `IMqttBroker`: Streaming event handler (`publish_state`, `subscribe`).
- `IDataCollector`: Feed collector (`get_market_prices`, `get_weather_forecast`, `poll_hardware_telemetry`).

---

## 🧪 Unit Testing Directives
- Tests must live in `tests/unit/test_collector.py`.
- Mock network calls only with standard responses matching genuine API schemas.
- Verify retry behavior, exponential backoff, and cache atomic fallback.
