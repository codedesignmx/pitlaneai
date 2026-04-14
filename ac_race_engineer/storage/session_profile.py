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
    vehicle_name: str
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
        self.session_start_stamp: str | None = None
        self.session_type: str = "unknown"
        self.track_name: str = "unknown"
        self.vehicle_name: str = "unknown"
        self.setup_notes: str = "default"  # notas de setup usado en esta sesión
        self.setup_iterations: list[dict[str, object]] = []

    def begin_session(self, snapshot: TelemetrySnapshot | None = None) -> None:
        """Inicializa metadatos estables para la sesión actual."""
        if self.session_start_time is None:
            now = datetime.now()
            self.session_start_time = now
            self.session_start_stamp = now.strftime("%Y%m%d_%H%M%S")

        if snapshot is not None:
            if snapshot.session_type and snapshot.session_type != "unknown":
                self.session_type = snapshot.session_type
            if snapshot.track_name and snapshot.track_name != "unknown":
                self.track_name = snapshot.track_name
            if snapshot.vehicle_name and snapshot.vehicle_name != "unknown":
                self.vehicle_name = snapshot.vehicle_name

    def reset(self) -> None:
        """Limpia el acumulado para empezar una nueva sesión."""
        self.snapshots = []
        self.session_start_time = None
        self.session_start_stamp = None
        self.session_type = "unknown"
        self.track_name = "unknown"
        self.vehicle_name = "unknown"
        self.setup_notes = "default"
        self.setup_iterations = []

    def has_data(self) -> bool:
        return bool(self.snapshots)

    def set_setup_iterations(self, iterations: list[dict[str, object]]) -> None:
        self.setup_iterations = list(iterations or [])

    def record_lap(
        self,
        lap: LapRecord,
        snapshot: TelemetrySnapshot,
        setup_hash: str = "unknown",
    ) -> None:
        """Registra una vuelta con contexto."""
        self.begin_session(snapshot)
        snap = SessionSnapshot(
            lap_number=lap.lap_number,
            lap_time=lap.lap_time_seconds,
            fuel_used=lap.fuel_used,
            fuel_remaining=snapshot.fuel,
            track_name=snapshot.track_name,
            vehicle_name=snapshot.vehicle_name,
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
        lines.append(f"Vehículo: {self.vehicle_name}")
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
            grip_line = self._format_condition_range(first.track_grip, last.track_grip, "%")
            if grip_line is not None:
                lines.append(f"  Grip pista: {grip_line}")

            air_line = self._format_condition_range(first.air_temp, last.air_temp, "°C")
            if air_line is not None:
                lines.append(f"  Aire: {air_line}")

            asphalt_line = self._format_condition_range(first.asphalt_temp, last.asphalt_temp, "°C")
            if asphalt_line is not None:
                lines.append(f"  Asfalto: {asphalt_line}")

            lines.append("")

        # Setup tracking
        setups = set(s.setup_hash for s in self.snapshots)
        if len(setups) > 1:
            lines.append("SETUPS TESTEADOS:")
            for setup in setups:
                count = sum(1 for s in self.snapshots if s.setup_hash == setup)
                lines.append(f"  {setup}: {count} vueltas")
            lines.append("")

        if self.setup_iterations:
            lines.append("ITERACIONES SETUP COACH:")
            for idx, item in enumerate(self.setup_iterations, start=1):
                issue = str(item.get("issue") or "unknown")
                parameter = str(item.get("parameter") or "unknown")
                direction = str(item.get("direction") or "unknown")
                step = item.get("step")
                outcome = str(item.get("outcome") or "pending")
                reason = str(item.get("reason") or "")
                step_txt = f"{float(step):.0f}" if isinstance(step, (int, float)) else "?"
                lines.append(
                    f"  {idx}. {issue} -> {direction} {parameter} ({step_txt} clic), resultado: {outcome}."
                )
                if reason:
                    lines.append(f"     motivo: {reason}")
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

    @staticmethod
    def _format_condition_value(value: float | None, unit: str) -> str | None:
        if value is None:
            return None
        return f"{value:.0f}{unit}"

    @classmethod
    def _format_condition_range(
        cls,
        start: float | None,
        end: float | None,
        unit: str,
    ) -> str | None:
        start_value = cls._format_condition_value(start, unit)
        end_value = cls._format_condition_value(end, unit)
        if start_value is None and end_value is None:
            return None
        if start_value is None:
            return f"N/D → {end_value}"
        if end_value is None:
            return f"{start_value} → N/D"
        return f"{start_value} → {end_value}"

    def save_to_file(self, filename: str | None = None) -> str:
        """Guarda resumen en archivo .txt."""
        if filename is None:
            timestamp = self.session_start_stamp or datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"session_{self.track_name}_{self.session_type}_{timestamp}.txt"

        filepath = self.output_dir / filename
        content = self.build_summary_txt()

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)

        return str(filepath)

    def save_json_archive(self, filename: str | None = None) -> str:
        """Guarda datos raw en JSON para análisis futuro."""
        if filename is None:
            timestamp = self.session_start_stamp or datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"session_{self.track_name}_{self.session_type}_{timestamp}.json"

        filepath = self.output_dir / filename
        data = {
            "timestamp": datetime.now().isoformat(),
            "track": self.track_name,
            "vehicle": self.vehicle_name,
            "session_type": self.session_type,
            "phase": self.compute_phase(),
            "setup_notes": self.setup_notes,
            "setup_iterations": self.setup_iterations,
            "snapshots": [asdict(s) for s in self.snapshots],
        }

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

        return str(filepath)
