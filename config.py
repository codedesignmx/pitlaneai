from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class Settings:
    poll_interval_seconds: float = 0.2
    debug_print_interval_seconds: float = 5.0
    event_cooldown_seconds: float = 12.0
    voice_rate: int = 185
    voice_volume_multiplier: float = 2.0
    auto_feedback_enabled: bool = True
    auto_feedback_interval_seconds: float = 75.0
    standings_poll_interval_seconds: float = 8.0
    auto_feedback_interval_by_session: dict[str, float] = field(
        default_factory=lambda: {
            "practice": 75.0,
            "qualifying": 40.0,
            "race": 60.0,
            "hotlap": 45.0,
            "unknown": 75.0,
        }
    )
    event_thresholds_by_session: dict[str, dict[str, float]] = field(
        default_factory=lambda: {
            "practice": {
                "pace_drop": 0.5,
                "pace_improving": -0.3,
                "consistency_window": 0.30,
            },
            "qualifying": {
                "pace_drop": 0.35,
                "pace_improving": -0.20,
                "consistency_window": 0.25,
            },
            "race": {
                "pace_drop": 0.7,
                "pace_improving": -0.4,
                "consistency_window": 0.45,
            },
            "unknown": {
                "pace_drop": 0.5,
                "pace_improving": -0.3,
                "consistency_window": 0.30,
            },
        }
    )
    # --- Push-to-talk (control de juego) ---
    ptt_enabled: bool = True
    # Índice del control (0 = primer control conectado)
    ptt_joystick_index: int = 0
    # Número del botón que activa el micrófono.
    # Si no sabes cuál es, ejecuta:
    #   python -c "from ac_race_engineer.audio.controller import print_button_map; print_button_map()"
    ptt_button_index: int = 1
    # --- Objetivos por sesión ---
    session_objectives: dict[str, dict[str, str]] = field(
        default_factory=lambda: {
            "practice": {
                "goal": "Búsqueda de ritmo y consistencia",
                "target_pace": "1:45.000",
                "target_laps": "10 vueltas",
                "setup_advice": "Comienza con setup anterior o default. Ajusta presión si hay understeer/oversteer.",
                "fuel_strategy": "Llena para 20-30 vueltas sin preocupación.",
            },
            "qualifying": {
                "goal": "Maximizar una vuelta limpia",
                "target_pace": "1:43.500",
                "target_laps": "1 vuelta de push",
                "setup_advice": "Asegúrate goma en ventana. Bias 58-60 para tracción.",
                "fuel_strategy": "Mínimo requerido, enfocado en pico.",
            },
            "race": {
                "goal": "Ritmo sostenible y gestión de fuel",
                "target_pace": "1:45.000",
                "target_laps": "Carrera completa",
                "setup_advice": "Setup de carrera: presión estable, downforce medio, diferenciales conservadores.",
                "fuel_strategy": "Calcula vueltas restantes × consumo promedio + 1 vuelta margen.",
            },
            "unknown": {
                "goal": "Completar sesión",
                "target_pace": "Ritmo confortable",
                "target_laps": "Múltiples",
                "setup_advice": "Default.",
                "fuel_strategy": "Llena según necesidad.",
            },
        }
    )
    log_dir: str = "logs"
    results_search_dirs: list[str] = field(
        default_factory=lambda: [
            "~/Documents/Assetto Corsa/out/results",
            "~/OneDrive/Documents/Assetto Corsa/out/results",
            "~/AppData/Local/AcTools Content Manager/Data/Online",
            "~/AppData/Local/AcTools Content Manager/Logs",
        ]
    )
    event_messages: dict[str, str] = field(
        default_factory=lambda: {
            "new_best_lap": "Nueva mejor vuelta",
            "pace_improving": "Buen ritmo, mantén así",
            "pace_drop": "Perdiste ritmo en la última vuelta",
            "stint_consistent": "Stint consistente, sigue así",
            "fuel_update": "Consumo estimado disponible",
        }
    )


SETTINGS = Settings()
