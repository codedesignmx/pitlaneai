from __future__ import annotations

import signal
import time
import os

from config import SETTINGS
from ac_race_engineer.ai.client import OpenAIAssistantClient
from ac_race_engineer.analysis.setup_coach import SetupCoach
from ac_race_engineer.analysis.session_state import SessionState
from ac_race_engineer.analysis.time_format import format_lap_time
from ac_race_engineer.analysis.objective_engine import (
    compute_race_pace,
    compute_fuel_predictor,
    build_objective_summary,
)
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
from ac_race_engineer.storage.rival_intel import RivalIntelStore
from ac_race_engineer.storage.session_profile import SessionProfile
from ac_race_engineer.storage.setup_registry import detect_current_setup, save_setup_document
from ac_race_engineer.telemetry.ac_reader import AcSharedMemoryReader


def run() -> None:
    reader = AcSharedMemoryReader()
    state = SessionState()
    queue = SpeechMessageQueue()
    speaker = Speaker(queue=queue, volume_multiplier=SETTINGS.voice_volume_multiplier)
    
    # Detectar si OpenAI está disponible (API key configurada)
    openai_api_key = os.getenv("OPENAI_API_KEY", "").strip()
    ai_enabled = bool(openai_api_key)
    if not ai_enabled:
        print("[AI] ⚠️ OPENAI_API_KEY no configurada. Asistente desactivado.")
    ai_client = OpenAIAssistantClient(enabled=ai_enabled)

    # Configurar control PTT si está habilitado
    controller: ControllerMonitor | None = None
    if SETTINGS.ptt_enabled:
        controller = ControllerMonitor(
            joystick_index=SETTINGS.ptt_joystick_index,
            button_index=SETTINGS.ptt_button_index,
        )
        controller.start()

    setup_coach = SetupCoach()
    mic = MicrophoneListener(
        speaker=speaker,
        ai_client=ai_client,
        session_state=state,
        setup_coach=setup_coach,
        controller=controller,
    )
    detector = EventDetector(
        cooldown_seconds=SETTINGS.event_cooldown_seconds,
        thresholds_by_session=SETTINGS.event_thresholds_by_session,
    )
    logger = SessionLogger(log_dir=SETTINGS.log_dir)
    session_profile = SessionProfile(output_dir="session_logs")
    rival_intel = RivalIntelStore()

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
    last_is_in_pit: bool | None = None
    active_session_key: tuple[str, str] | None = None
    saved_session_keys: set[tuple[str, str]] = set()
    active_setup_id = "default"

    def _finalize_session(snapshot_for_save: object | None = None) -> None:
        nonlocal pending_session_end_announce
        if not session_profile.has_data() or active_session_key is None:
            pending_session_end_announce = False
            return
        if active_session_key in saved_session_keys:
            pending_session_end_announce = False
            return

        session_profile.set_setup_iterations(setup_coach.export_iterations())

        txt_path = session_profile.save_to_file()
        json_path = session_profile.save_json_archive()
        print(f"[PROFILE] Sesión guardada: {txt_path}")
        print(f"[PROFILE] JSON archivado: {json_path}")

        stats = state.get_stats()
        laps_valid = [s.lap_time for s in session_profile.snapshots if s.lap_time > 30.0]
        race_metrics = compute_race_pace(laps_valid)
        session_time_left_seconds = 0.0
        if snapshot_for_save is not None and hasattr(snapshot_for_save, "session_time_left_seconds"):
            session_time_left_seconds = getattr(snapshot_for_save, "session_time_left_seconds") or 0.0
        fuel_metrics = compute_fuel_predictor(
            avg_fuel_per_lap=stats.avg_fuel_per_lap,
            time_left_minutes=session_time_left_seconds / 60.0,
            avg_lap_time_seconds=race_metrics.race_pace_avg,
        )

        objective_msg = build_objective_summary(fuel_metrics)
        if objective_msg and objective_msg != "Sin métricas objetivo aún.":
            print(f"[SPEAK] {objective_msg}")
            queue.push(objective_msg)

        standings = load_latest_standings(
            SETTINGS.results_search_dirs,
            expected_session_type=ended_session_type,
        )
        end_msg = build_session_end_summary(
            session_label=ended_session_type,
            own_position=ended_session_position,
            own_best_lap=ended_best_lap,
            standings=standings,
        )
        print(f"[SPEAK] {end_msg}")
        queue.push(end_msg)

        saved_session_keys.add(active_session_key)

        rivals_path = rival_intel.finalize_active_session()
        if rivals_path:
            print(f"[RIVALS] Sesión rival guardada: {rivals_path}")
        pending_session_end_announce = False

    try:
        while not should_stop:
            snapshot = reader.read_snapshot()
            if snapshot is None:
                time.sleep(SETTINGS.poll_interval_seconds)
                continue

            if (
                last_is_in_pit is True
                and not snapshot.is_in_pit
                and snapshot.status == "live"
            ):
                pit_exit_msg = state.build_pit_exit_report()
                if pit_exit_msg:
                    print(f"[SPEAK] {pit_exit_msg}")
                    queue.push(pit_exit_msg)

            state.record_tick(snapshot)

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
                    print(f"[SPEAK] {msg}")
                    queue.push(msg)

            lap_record = state.update(snapshot)
            if lap_record is not None:
                logger.log_lap(lap_record)

                # Detectar setup activo por vuelta (permite A/B y cambios en pits).
                setup_info = detect_current_setup()
                active_setup_id = setup_info.setup_id
                state.update_setup_context(
                    active_setup_id,
                    setup_info.setup_label,
                    track_name=setup_info.track_name,
                    track_layout=setup_info.track_layout,
                )
                setup_coach.update_from_setup(setup_info)
                setup_coach.register_lap_result(setup_info, lap_record)
                setup_doc = save_setup_document(setup_info)

                # Registrar en profile de sesión
                session_profile.record_lap(lap_record, snapshot, setup_hash=active_setup_id)
                session_profile.setup_notes = f"{active_setup_id} ({setup_info.setup_label})"
                print(f"[SETUP] id={active_setup_id} label={setup_info.setup_label} doc={setup_doc}")
                print(
                    f"[LAP] lap={lap_record.lap_number} time={format_lap_time(lap_record.lap_time_seconds)} "
                    f"fuel_used={lap_record.fuel_used:.3f}"
                    if lap_record.fuel_used is not None
                    else f"[LAP] lap={lap_record.lap_number} time={format_lap_time(lap_record.lap_time_seconds)}"
                )

                current_session = snapshot.session_type
                lap_competitor_msg = state.build_lap_competitor_summary()
                if lap_competitor_msg:
                    print(f"[SPEAK] {lap_competitor_msg}")
                    queue.push(lap_competitor_msg)

                events = detector.on_new_lap(state, session_type=current_session)
                for event in events:
                    logger.log_event(event)
                    print(f"[EVENT] {event.name}: {event.payload}")
                    msg = build_event_message(event)
                    if msg:
                        print(f"[SPEAK] {msg}")
                        queue.push(msg)

            now = time.time()
            current_session = snapshot.session_type
            current_track = snapshot.track_name if snapshot.track_name and snapshot.track_name != "unknown" else "unknown"
            current_session_key = None
            if current_session in {"practice", "qualifying", "race"}:
                current_session_key = (current_track, current_session)

            if current_session_key is not None and current_session_key != active_session_key:
                if active_session_key is not None and active_session_key not in saved_session_keys:
                    pending_session_end_announce = True
                    ended_session_type = session_profile.session_type
                    ended_session_position = snapshot.player_position
                    ended_best_lap = state.get_stats().best_lap_seconds
                    _finalize_session(snapshot)

                session_profile.reset()
                session_profile.begin_session(snapshot)
                setup_coach.reset_session_notes()
                rival_intel.begin_session(
                    track_name=current_track,
                    session_type=current_session,
                    stamp=session_profile.session_start_stamp,
                )

                setup_info = detect_current_setup()
                active_setup_id = setup_info.setup_id
                state.update_setup_context(
                    active_setup_id,
                    setup_info.setup_label,
                    track_name=setup_info.track_name,
                    track_layout=setup_info.track_layout,
                )
                setup_coach.update_from_setup(setup_info)
                session_profile.setup_notes = f"{active_setup_id} ({setup_info.setup_label})"
                setup_doc = save_setup_document(setup_info)
                print(f"[SETUP] sesión={current_session_key} id={active_setup_id} label={setup_info.setup_label} doc={setup_doc}")
                active_session_key = current_session_key

            if current_session != last_session_type:
                print(f"[SESSION] detectada: {current_session}")
                last_session_type = current_session
                standings_initialized = False
                last_standings = []
                state.update_live_timing([])

            # Optional live timing feed from results files (depends on server/build)
            if (
                snapshot.status == "live"
                and current_session in {"practice", "qualifying", "race"}
                and now - last_standings_check >= SETTINGS.standings_poll_interval_seconds
            ):
                current_standings = load_latest_standings(
                    SETTINGS.results_search_dirs,
                    expected_session_type=current_session,
                )
                last_car_index_map = load_live_car_index_map(
                    SETTINGS.results_search_dirs,
                    expected_session_type=current_session,
                )
                gap_info = load_live_gap_info(
                    SETTINGS.results_search_dirs,
                    expected_session_type=current_session,
                )
                last_gap_ahead_seconds = gap_info.gap_ahead_seconds
                last_gap_behind_seconds = gap_info.gap_behind_seconds
                state.update_live_timing(
                    current_standings,
                    gap_ahead_seconds=last_gap_ahead_seconds,
                    gap_behind_seconds=last_gap_behind_seconds,
                )
                rival_intel.observe(
                    standings=current_standings,
                    player_position=snapshot.player_position,
                )
                if current_standings:
                    if standings_initialized:
                        updates = detect_standings_updates(last_standings, current_standings)
                        for update_msg in updates:
                            print(f"[SPEAK] {update_msg}")
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
                ended_session_type = session_profile.session_type
                ended_session_position = snapshot.player_position
                ended_best_lap = state.get_stats().best_lap_seconds

            if pending_session_end_announce:
                _finalize_session(snapshot)

            last_snapshot_status = snapshot.status
            last_is_in_pit = snapshot.is_in_pit

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
                    print(f"[SPEAK] {auto_msg}")
                    queue.push(auto_msg)
                    last_auto_feedback = now

            time.sleep(SETTINGS.poll_interval_seconds)
    finally:
        if active_session_key is not None and active_session_key not in saved_session_keys:
            ended_session_type = session_profile.session_type
            ended_session_position = state.last_snapshot.player_position if state.last_snapshot is not None else 0
            ended_best_lap = state.get_stats().best_lap_seconds
            _finalize_session(state.last_snapshot)
        reader.close()
        mic.stop()
        if controller is not None:
            controller.stop()
        speaker.stop()
        logger.close()
        print("AC Race Engineer MVP detenido.")


if __name__ == "__main__":
    run()
