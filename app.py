from __future__ import annotations

import signal
import time

from config import SETTINGS
from ac_race_engineer.ai.client import OpenAIAssistantClient
from ac_race_engineer.analysis.session_state import SessionState
from ac_race_engineer.analysis.time_format import format_lap_time
from ac_race_engineer.audio.controller import ControllerMonitor
from ac_race_engineer.audio.microphone import MicrophoneListener
from ac_race_engineer.audio.queue import SpeechMessageQueue
from ac_race_engineer.audio.speaker import Speaker
from ac_race_engineer.events.detector import EventDetector
from ac_race_engineer.events.message_builder import build_event_message
from ac_race_engineer.storage.results_summary import (
    build_session_end_summary,
    describe_standings_source,
    detect_standings_updates,
    load_live_car_index_map,
    load_live_gap_info,
    load_latest_standings,
)
from ac_race_engineer.storage.logger import SessionLogger
from ac_race_engineer.telemetry.ac_reader import AcSharedMemoryReader


def run() -> None:
    reader = AcSharedMemoryReader()
    state = SessionState()
    queue = SpeechMessageQueue()
    speaker = Speaker(queue=queue, volume_multiplier=SETTINGS.voice_volume_multiplier)
    ai_client = OpenAIAssistantClient(enabled=True)

    # Configurar control PTT si está habilitado
    controller: ControllerMonitor | None = None
    if SETTINGS.ptt_enabled:
        controller = ControllerMonitor(
            joystick_index=SETTINGS.ptt_joystick_index,
            button_index=SETTINGS.ptt_button_index,
        )
        controller.start()

    mic = MicrophoneListener(
        speaker=speaker,
        ai_client=ai_client,
        session_state=state,
        controller=controller,
    )
    detector = EventDetector(
        cooldown_seconds=SETTINGS.event_cooldown_seconds,
        thresholds_by_session=SETTINGS.event_thresholds_by_session,
    )
    logger = SessionLogger(log_dir=SETTINGS.log_dir)

    should_stop = False

    def _stop_handler(_sig: int, _frame: object) -> None:
        nonlocal should_stop
        should_stop = True

    signal.signal(signal.SIGINT, _stop_handler)

    speaker.start()
    mic.start()
    reader.open()
    print("AC Race Engineer MVP iniciado. Ctrl+C para salir.")

    last_debug_print = 0.0
    last_auto_feedback = 0.0
    last_session_type = "unknown"
    last_snapshot_status = "unknown"
    pending_session_end_announce = False
    ended_session_type = "unknown"
    ended_session_position = 0
    ended_best_lap: float | None = None
    last_standings_check = 0.0
    last_standings: list = []
    standings_initialized = False
    last_standings_diag = 0.0
    last_gap_ahead_seconds: float | None = None
    last_gap_behind_seconds: float | None = None
    last_car_index_map: dict[int, str] = {}

    try:
        while not should_stop:
            snapshot = reader.read_snapshot()
            if snapshot is None:
                time.sleep(SETTINGS.poll_interval_seconds)
                continue

            tick_events = detector.on_tick(snapshot)
            for event in tick_events:
                if event.name == "collision_contact":
                    idx = event.payload.get("closest_car_index")
                    if isinstance(idx, int) and idx in last_car_index_map:
                        event.payload["opponent_name"] = last_car_index_map[idx]
                logger.log_event(event)
                print(f"[EVENT] {event.name}: {event.payload}")
                msg = build_event_message(event)
                if msg:
                    if event.name == "collision_contact":
                        state.register_collision_note(msg)
                    queue.push(msg)

            lap_record = state.update(snapshot)
            if lap_record is not None:
                logger.log_lap(lap_record)
                print(
                    f"[LAP] lap={lap_record.lap_number} time={format_lap_time(lap_record.lap_time_seconds)} "
                    f"fuel_used={lap_record.fuel_used:.3f}"
                    if lap_record.fuel_used is not None
                    else f"[LAP] lap={lap_record.lap_number} time={format_lap_time(lap_record.lap_time_seconds)}"
                )

                current_session = snapshot.session_type
                events = detector.on_new_lap(state, session_type=current_session)
                for event in events:
                    logger.log_event(event)
                    print(f"[EVENT] {event.name}: {event.payload}")
                    msg = build_event_message(event)
                    if msg:
                        queue.push(msg)

            now = time.time()
            current_session = snapshot.session_type
            if current_session != last_session_type:
                print(f"[SESSION] detectada: {current_session}")
                last_session_type = current_session
                standings_initialized = False
                last_standings = []

            # Optional live timing feed from results files (depends on server/build)
            if (
                snapshot.status == "live"
                and current_session in {"practice", "qualifying", "race"}
                and now - last_standings_check >= SETTINGS.standings_poll_interval_seconds
            ):
                current_standings = load_latest_standings(SETTINGS.results_search_dirs)
                last_car_index_map = load_live_car_index_map(SETTINGS.results_search_dirs)
                gap_info = load_live_gap_info(SETTINGS.results_search_dirs)
                last_gap_ahead_seconds = gap_info.gap_ahead_seconds
                last_gap_behind_seconds = gap_info.gap_behind_seconds
                if current_standings:
                    if standings_initialized:
                        updates = detect_standings_updates(last_standings, current_standings)
                        for update_msg in updates:
                            print(f"[TIMING] {update_msg}")
                            queue.push(update_msg)
                    else:
                        print(f"[TIMING] feed activo con {len(current_standings)} pilotos")
                        standings_initialized = True
                    last_standings = current_standings
                elif now - last_standings_diag >= 30.0:
                    print(f"[TIMING] sin standings en vivo ({describe_standings_source(SETTINGS.results_search_dirs)})")
                    last_standings_diag = now
                last_standings_check = now

            # Session-end trigger: transition from live to non-live status
            if last_snapshot_status == "live" and snapshot.status != "live":
                pending_session_end_announce = True
                ended_session_type = snapshot.session_type
                ended_session_position = snapshot.player_position
                ended_best_lap = state.get_stats().best_lap_seconds

            if pending_session_end_announce:
                standings = load_latest_standings(SETTINGS.results_search_dirs)
                end_msg = build_session_end_summary(
                    session_label=ended_session_type,
                    own_position=ended_session_position,
                    own_best_lap=ended_best_lap,
                    standings=standings,
                )
                print(f"[SESSION_END] {end_msg}")
                queue.push(end_msg)
                pending_session_end_announce = False

            last_snapshot_status = snapshot.status

            if now - last_debug_print >= SETTINGS.debug_print_interval_seconds:
                stats = state.get_stats()
                print(
                    "[DBG] "
                    f"speed={snapshot.speed_kmh:.1f}kmh "
                    f"gear={snapshot.gear} rpm={snapshot.rpm} "
                    f"pos={snapshot.player_position} "
                    f"fuel={snapshot.fuel:.2f}L "
                    f"session={snapshot.session_type} "
                    f"lap={snapshot.lap_number} lap_time={format_lap_time(snapshot.current_lap_time_seconds)} "
                    f"best={format_lap_time(stats.best_lap_seconds)}"
                )
                last_debug_print = now

            interval = SETTINGS.auto_feedback_interval_by_session.get(
                current_session, SETTINGS.auto_feedback_interval_seconds
            )

            if (
                SETTINGS.auto_feedback_enabled
                and now - last_auto_feedback >= interval
            ):
                auto_msg = state.build_auto_feedback()
                if current_session == "race":
                    auto_msg = state.build_auto_feedback(
                        gap_ahead_seconds=last_gap_ahead_seconds,
                        gap_behind_seconds=last_gap_behind_seconds,
                    )
                else:
                    auto_msg = state.build_auto_feedback()
                if auto_msg:
                    print(f"[AUTO] {auto_msg}")
                    queue.push(auto_msg)
                    last_auto_feedback = now

            time.sleep(SETTINGS.poll_interval_seconds)
    finally:
        reader.close()
        mic.stop()
        if controller is not None:
            controller.stop()
        speaker.stop()
        logger.close()
        print("AC Race Engineer MVP detenido.")


if __name__ == "__main__":
    run()
