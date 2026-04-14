from __future__ import annotations

import json
import os
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path

from ac_race_engineer.telemetry.models import LapRecord, TelemetrySnapshot


@dataclass(slots=True)
class SessionSnapshot:
    """Captura de estado de sesión en un momento."""

    lap_number: int
    lap_time: float
    fuel_used: float | None
    fuel_remaining: float
    track_name: str
    session_type: str
    setup_hash: str  # hash simple del setup para A/B tracking
    air_temp: float | None
    asphalt_temp: float | None
    track_grip: float | None
    timestamp_iso: str


class SessionProfile:
    """Guarda y resume datos de una sesión de práctica."""

    def __init__(self, output_dir: str = "session_logs"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(exist_ok=True)
        self.snapshots: list[SessionSnapshot] = []
        self.session_start_time: datetime | None = None
        self.session_type: str = "unknown"
        self.track_name: str = "unknown"
        self.setup_notes: str = "default"  # notas de setup usado en esta sesión

    def record_lap(
        self,
        lap: LapRecord,
        snapshot: TelemetrySnapshot,
        setup_hash: str = "unknown",
    ) -> None:
        """Registra una vuelta con contexto."""
        snap = SessionSnapshot(
            lap_number=lap.lap_number,
            lap_time=lap.lap_time_seconds,
            fuel_used=lap.fuel_used,
            fuel_remaining=snapshot.fuel,
            track_name=snapshot.track_name,
            session_type=snapshot.session_type,
            setup_hash=setup_hash,
            air_temp=snapshot.air_temp_c,
            asphalt_temp=snapshot.asphalt_temp_c,
            track_grip=snapshot.track_grip_percent,
            timestamp_iso=datetime.now().isoformat(),
        )
        self.snapshots.append(snap)
        self.session_type = snapshot.session_type
        self.track_name = snapshot.track_name

        if self.session_start_time is None:
            self.session_start_time = datetime.now()

    def compute_phase(self) -> str:
        """Detecta qué fase de sesión estamos (por número de vueltas)."""
        if len(self.snapshots) <= 2:
            return "warm_up"
        elif len(self.snapshots) <= 8:
            return "setup_test"
        elif len(self.snapshots) <= 20:
            return "qualy_simulation"
        else:
            return "long_run"

    def build_summary_txt(self) -> str:
        """Genera resumen en formato legible para guardar en .txt."""
        if not self.snapshots:
            return "Sin datos de sesión."

        lines: list[str] = []
        lines.append("=" * 80)
        lines.append("PITRADIO SESSION REPORT")
        lines.append("=" * 80)
        lines.append("")

        # Metadatos
        lines.append(f"Fecha: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append(f"Track: {self.track_name}")
        lines.append(f"Sesión: {self.session_type}")
        lines.append(f"Fase detectada: {self.compute_phase()}")
        lines.append(f"Vueltas totales: {len(self.snapshots)}")
        lines.append(f"Setup: {self.setup_notes}")
        lines.append("")

        # Resumen de tiempos
        times = [s.lap_time for s in self.snapshots if s.lap_time > 30.0]
        if times:
            best_time = min(times)
            avg_time = sum(times) / len(times)
            lines.append("TIEMPOS:")
            lines.append(f"  Mejor vuelta: {best_time:.3f}s")
            lines.append(f"  Promedio: {avg_time:.3f}s")
            lines.append(f"  Muestras válidas: {len(times)}")
            lines.append("")

        # Consumo de fuel
        fuel_used = [s.fuel_used for s in self.snapshots if s.fuel_used is not None]
        if fuel_used:
            avg_fuel = sum(fuel_used) / len(fuel_used)
            lines.append("COMBUSTIBLE:")
            lines.append(f"  Consumo promedio: {avg_fuel:.3f} L/vuelta")
            lines.append(f"  Muestras: {len(fuel_used)}")
            lines.append("")

        # Condiciones
        if self.snapshots:
            first = self.snapshots[0]
            last = self.snapshots[-1]
            lines.append("CONDICIONES (inicio → fin):")
            lines.append(f"  Grip pista: {first.track_grip:.0f}% → {last.track_grip:.0f}%")
            if first.air_temp is not None:
                lines.append(
                    f"  Aire: {first.air_temp:.0f}°C → {last.air_temp:.0f}°C"
                )
            if first.asphalt_temp is not None:
                lines.append(
                    f"  Asfalto: {first.asphalt_temp:.0f}°C → {last.asphalt_temp:.0f}°C"
                )
            lines.append("")

        # Setup tracking
        setups = set(s.setup_hash for s in self.snapshots)
        if len(setups) > 1:
            lines.append("SETUPS TESTEADOS:")
            for setup in setups:
                count = sum(1 for s in self.snapshots if s.setup_hash == setup)
                lines.append(f"  {setup}: {count} vueltas")
            lines.append("")

        # Detalle por vuelta (últimas 20 para brevedad)
        lines.append("ÚLTIMAS VUELTAS (detalle):")
        lines.append(f"{'Lap':<5} {'Tiempo':<10} {'Fuel Used':<10} {'Setup':<12}")
        lines.append("-" * 40)
        for snap in self.snapshots[-20:]:
            time_str = f"{snap.lap_time:.3f}s"
            fuel_str = f"{snap.fuel_used:.2f}L" if snap.fuel_used else "—"
            lines.append(f"{snap.lap_number:<5} {time_str:<10} {fuel_str:<10} {snap.setup_hash:<12}")

        lines.append("")
        lines.append("=" * 80)

        return "\n".join(lines)

    def save_to_file(self, filename: str | None = None) -> str:
        """Guarda resumen en archivo .txt."""
        if filename is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"session_{self.track_name}_{self.session_type}_{timestamp}.txt"

        filepath = self.output_dir / filename
        content = self.build_summary_txt()

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)

        return str(filepath)

    def save_json_archive(self, filename: str | None = None) -> str:
        """Guarda datos raw en JSON para análisis futuro."""
        if filename is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"session_{self.track_name}_{self.session_type}_{timestamp}.json"

        filepath = self.output_dir / filename
        data = {
            "timestamp": datetime.now().isoformat(),
            "track": self.track_name,
            "session_type": self.session_type,
            "phase": self.compute_phase(),
            "snapshots": [asdict(s) for s in self.snapshots],
        }

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

        return str(filepath)
