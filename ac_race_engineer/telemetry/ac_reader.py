from __future__ import annotations

import ctypes
import math
import time
from dataclasses import dataclass

from ac_race_engineer.telemetry.models import TelemetrySnapshot


SESSION_MAP = {
    0: "practice",
    1: "qualifying",
    2: "race",
    3: "hotlap",
    4: "time_attack",
    5: "drift",
    6: "drag",
}

STATUS_MAP = {
    0: "off",
    1: "replay",
    2: "live",
    3: "paused",
}


class _SPageFilePhysics(ctypes.Structure):
    _pack_ = 4
    _fields_ = [
        ("packet_id", ctypes.c_int),
        ("gas", ctypes.c_float),
        ("brake", ctypes.c_float),
        ("fuel", ctypes.c_float),
        ("gear", ctypes.c_int),
        ("rpm", ctypes.c_int),
        ("steer_angle", ctypes.c_float),
        ("speed_kmh", ctypes.c_float),
    ]


class _SPageFileStatic(ctypes.Structure):
    _pack_ = 4
    _fields_ = [
        ("sm_version", ctypes.c_wchar * 15),
        ("ac_version", ctypes.c_wchar * 15),
        ("number_of_sessions", ctypes.c_int),
        ("num_cars", ctypes.c_int),
        ("car_model", ctypes.c_wchar * 33),
        ("track", ctypes.c_wchar * 33),
    ]


class _SPageFileGraphics(ctypes.Structure):
    _pack_ = 4
    _fields_ = [
        ("packet_id", ctypes.c_int),
        ("status", ctypes.c_int),
        ("session", ctypes.c_int),
        ("current_time", ctypes.c_wchar * 15),
        ("last_time", ctypes.c_wchar * 15),
        ("best_time", ctypes.c_wchar * 15),
        ("split", ctypes.c_wchar * 15),
        ("completed_laps", ctypes.c_int),
        ("position", ctypes.c_int),
        ("i_current_time", ctypes.c_int),
        ("i_last_time", ctypes.c_int),
        ("i_best_time", ctypes.c_int),
        ("session_time_left", ctypes.c_float),
        ("distance_traveled", ctypes.c_float),
        ("is_in_pit", ctypes.c_int),
        ("current_sector_index", ctypes.c_int),
        ("last_sector_time", ctypes.c_int),
        ("number_of_laps", ctypes.c_int),
        ("tyre_compound", ctypes.c_wchar * 33),
        ("replay_time_multiplier", ctypes.c_float),
        ("normalized_car_position", ctypes.c_float),
        ("car_coordinates", ctypes.c_float * (60 * 3)),
        ("car_id", ctypes.c_int),
        ("player_car_id", ctypes.c_int),
        # Optional trailing fields exposed in some AC/CSP builds
        ("surface_grip", ctypes.c_float),
        ("air_temp", ctypes.c_float),
        ("road_temp", ctypes.c_float),
        ("wind_speed", ctypes.c_float),
    ]


@dataclass(slots=True)
class _Maps:
    physics: "_WinNamedMap" | None = None
    graphics: "_WinNamedMap" | None = None
    static: "_WinNamedMap" | None = None


class _WinNamedMap:
    FILE_MAP_READ = 0x0004

    def __init__(self, handle: int, view_ptr: int, size: int) -> None:
        self.handle = handle
        self.view_ptr = view_ptr
        self.size = size

    @classmethod
    def open_existing(cls, names: tuple[str, ...], size: int) -> "_WinNamedMap | None":
        kernel32 = ctypes.windll.kernel32
        kernel32.OpenFileMappingW.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_wchar_p]
        kernel32.OpenFileMappingW.restype = ctypes.c_void_p
        kernel32.MapViewOfFile.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_size_t,
        ]
        kernel32.MapViewOfFile.restype = ctypes.c_void_p

        for name in names:
            handle = kernel32.OpenFileMappingW(cls.FILE_MAP_READ, 0, name)
            if not handle:
                continue

            view_ptr = kernel32.MapViewOfFile(handle, cls.FILE_MAP_READ, 0, 0, size)
            if not view_ptr:
                kernel32.CloseHandle(handle)
                continue

            return cls(handle=int(handle), view_ptr=int(view_ptr), size=size)

        return None

    def read(self) -> bytes:
        return ctypes.string_at(self.view_ptr, self.size)

    def close(self) -> None:
        kernel32 = ctypes.windll.kernel32
        if self.view_ptr:
            kernel32.UnmapViewOfFile(ctypes.c_void_p(self.view_ptr))
            self.view_ptr = 0
        if self.handle:
            kernel32.CloseHandle(ctypes.c_void_p(self.handle))
            self.handle = 0


class AcSharedMemoryReader:
    """Reads telemetry from Assetto Corsa shared memory blocks.

    If some fields are unavailable in a specific AC build, this reader returns
    safe defaults and keeps running.
    """

    PHYSICS_MAP = "acpmf_physics"
    GRAPHICS_MAP = "acpmf_graphics"
    STATIC_MAP = "acpmf_static"

    def __init__(self) -> None:
        self._maps = _Maps()
        self._warned_not_found = False
        self._prev_positions: dict[int, tuple[float, float, float]] = {}
        self._prev_ts: float = time.time()

    def open(self) -> None:
        self._maps.physics = self._open_map(self.PHYSICS_MAP, ctypes.sizeof(_SPageFilePhysics))
        self._maps.graphics = self._open_map(self.GRAPHICS_MAP, ctypes.sizeof(_SPageFileGraphics))
        self._maps.static = self._open_map(self.STATIC_MAP, ctypes.sizeof(_SPageFileStatic))

    def close(self) -> None:
        for mapped in (self._maps.physics, self._maps.graphics, self._maps.static):
            if mapped is not None:
                mapped.close()
        self._maps = _Maps()

    def read_snapshot(self) -> TelemetrySnapshot | None:
        if self._maps.physics is None or self._maps.graphics is None:
            self.open()

        if self._maps.physics is None or self._maps.graphics is None:
            if not self._warned_not_found:
                print("Esperando shared memory de Assetto Corsa...")
                self._warned_not_found = True
            return None

        physics = self._read_struct(self._maps.physics, _SPageFilePhysics)
        graphics = self._read_struct(self._maps.graphics, _SPageFileGraphics)
        static = (
            self._read_struct(self._maps.static, _SPageFileStatic)
            if self._maps.static is not None
            else None
        )

        (
            nearby_count,
            closest_distance,
            closest_index,
            closest_speed_kmh,
            nearby_incidents,
        ) = self._compute_proximity(graphics)
        track_name = self._clean_wchar(static.track) if static is not None else "unknown"
        vehicle_name = self._clean_wchar(static.car_model) if static is not None else "unknown"
        grip_raw = self._optional_range(
            float(getattr(graphics, "surface_grip", 0.0)), 0.4, 1.5, reject_zero=True
        )
        grip_percent = round(grip_raw * 100.0, 1) if grip_raw is not None else None
        air_temp_c = self._optional_range(
            float(getattr(graphics, "air_temp", 0.0)), -40.0, 80.0, reject_zero=True
        )
        asphalt_temp_c = self._optional_range(
            float(getattr(graphics, "road_temp", 0.0)), -40.0, 100.0, reject_zero=True
        )
        wind_speed_kmh = self._optional_range(
            float(getattr(graphics, "wind_speed", 0.0)), 0.0, 200.0, reject_zero=True
        )

        self._warned_not_found = False
        return TelemetrySnapshot(
            fuel=max(0.0, float(physics.fuel)),
            speed_kmh=max(0.0, float(physics.speed_kmh)),
            gear=int(physics.gear),
            rpm=max(0, int(physics.rpm)),
            lap_number=max(0, int(graphics.completed_laps)),
            current_lap_time_seconds=max(0.0, float(graphics.i_current_time) / 1000.0),
            last_lap_time_seconds=(
                float(graphics.i_last_time) / 1000.0 if graphics.i_last_time > 0 else None
            ),
            normalized_car_position=min(max(float(graphics.normalized_car_position), 0.0), 1.0),
            throttle=min(max(float(physics.gas), 0.0), 1.0),
            brake=min(max(float(physics.brake), 0.0), 1.0),
            session_type=SESSION_MAP.get(int(graphics.session), "unknown"),
            status=STATUS_MAP.get(int(graphics.status), "unknown"),
            player_position=max(0, int(graphics.position)),
            is_in_pit=bool(graphics.is_in_pit),
            current_sector_index=max(0, int(graphics.current_sector_index)),
            nearby_car_count=nearby_count,
            closest_car_distance_m=closest_distance,
            closest_car_index=closest_index,
            closest_car_speed_kmh=closest_speed_kmh,
            nearby_incident_count=nearby_incidents,
            track_name=track_name,
            vehicle_name=vehicle_name,
            session_laps_total=max(0, int(graphics.number_of_laps)),
            session_time_left_seconds=max(0.0, float(graphics.session_time_left)),
            track_grip_percent=grip_percent,
            air_temp_c=air_temp_c,
            asphalt_temp_c=asphalt_temp_c,
            wind_speed_kmh=wind_speed_kmh,
        )

    def _compute_proximity(
        self, graphics: _SPageFileGraphics
    ) -> tuple[int, float | None, int | None, float | None, int]:
        player_idx = int(graphics.player_car_id)
        if player_idx < 0 or player_idx >= 60:
            return 0, None, None, None, 0

        now = time.time()
        dt = max(0.001, now - self._prev_ts)

        coords = graphics.car_coordinates

        def pos_for(i: int) -> tuple[float, float, float]:
            base = i * 3
            return (float(coords[base]), float(coords[base + 1]), float(coords[base + 2]))

        player_pos = pos_for(player_idx)
        if player_pos == (0.0, 0.0, 0.0):
            self._prev_ts = now
            return 0, None, None, None, 0

        close_threshold_m = 18.0
        incident_radius_m = 35.0
        incident_speed_mps = 4.0

        nearby_count = 0
        closest_distance: float | None = None
        closest_index: int | None = None
        closest_speed_kmh: float | None = None
        nearby_incidents = 0
        new_prev: dict[int, tuple[float, float, float]] = {}

        for i in range(60):
            pos = pos_for(i)
            if pos == (0.0, 0.0, 0.0):
                continue
            new_prev[i] = pos
            if i == player_idx:
                continue

            distance = math.dist(player_pos, pos)
            speed_est_kmh: float | None = None
            prev = self._prev_positions.get(i)
            if prev is not None:
                moved = math.dist(prev, pos)
                speed_est_kmh = (moved / dt) * 3.6

            if closest_distance is None or distance < closest_distance:
                closest_distance = distance
                closest_index = i
                closest_speed_kmh = speed_est_kmh

            if distance <= close_threshold_m:
                nearby_count += 1

            if prev is not None:
                moved = math.dist(prev, pos)
                speed_est = moved / dt
                if distance <= incident_radius_m and speed_est <= incident_speed_mps:
                    nearby_incidents += 1

        self._prev_positions = new_prev
        self._prev_ts = now
        return nearby_count, closest_distance, closest_index, closest_speed_kmh, nearby_incidents

    @staticmethod
    def _open_map(name: str, size: int) -> _WinNamedMap | None:
        # AC usually exposes these with Local\ prefix on Windows.
        return _WinNamedMap.open_existing(names=(f"Local\\{name}", name), size=size)

    @staticmethod
    def _read_struct(mm: _WinNamedMap, struct_type: type[ctypes.Structure]) -> ctypes.Structure:
        raw = mm.read()
        return struct_type.from_buffer_copy(raw)

    @staticmethod
    def _clean_wchar(value: str) -> str:
        clean = (value or "").split("\x00", 1)[0].strip()
        return clean if clean else "unknown"

    @staticmethod
    def _optional_range(
        value: float,
        minimum: float,
        maximum: float,
        reject_zero: bool = False,
    ) -> float | None:
        if not math.isfinite(value):
            return None
        if reject_zero and abs(value) < 1e-6:
            return None
        if value < minimum or value > maximum:
            return None
        return value
