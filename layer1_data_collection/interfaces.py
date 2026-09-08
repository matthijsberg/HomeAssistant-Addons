"""
Layer 1: Ingestion & Connectivity Interfaces
============================================
Defines the abstract contracts for external I/O, streaming brokers,
and time-series persistence.
"""

from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
from models.canonical import Measurement, DeviceState


class ITimeSeriesStorage(ABC):
    """Abstract interface for time-series persistence (InfluxDB 1.8 / 2.x)."""

    @abstractmethod
    def ping(self) -> bool:
        """Returns True if database endpoint is reachable."""
        pass

    @abstractmethod
    def write_measurement(self, measurement: Measurement) -> bool:
        """Writes a single canonical measurement via line protocol."""
        pass

    @abstractmethod
    def write_batch(self, measurements: List[Measurement]) -> bool:
        """Writes a batch of canonical measurements atomically."""
        pass

    @abstractmethod
    def query_recent(self, device_id: str, field: str, hours: int = 24) -> List[Dict[str, Any]]:
        """Queries recent historical measurements for a given device."""
        pass


class IMqttBroker(ABC):
    """Abstract interface for streaming message broker connectivity."""

    @abstractmethod
    def connect(self) -> bool:
        """Establishes connection and authentication with the broker."""
        pass

    @abstractmethod
    def publish_state(self, device_id: str, state: DeviceState) -> bool:
        """Publishes canonical device state onto the topic bus."""
        pass

    @abstractmethod
    def subscribe(self, topic: str, callback: Any) -> bool:
        """Subscribes to an incoming streaming telemetry topic."""
        pass


class IDataCollector(ABC):
    """Abstract interface for pulling and normalizing external data feeds."""

    @abstractmethod
    def get_market_prices(self, horizon_hours: int = 24) -> Dict[str, Any]:
        """Fetches EPEX Spot Day-Ahead hourly/quarter-hourly market prices."""
        pass

    @abstractmethod
    def get_weather_forecast(self, horizon_hours: int = 48) -> Dict[str, Any]:
        """Fetches global solar irradiance (W/m2), ambient temperature, and wind."""
        pass

    @abstractmethod
    def poll_hardware_telemetry(self) -> List[Measurement]:
        """Polls connected hardware and returns normalized canonical measurements."""
        pass
