from __future__ import annotations

import configparser
import hashlib
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class SetupInfo:
    setup_id: str
    car_model: str
    track_name: str
    track_layout: str
    source_path: str | None
    setup_label: str
    setup_text: str | None


def detect_current_setup() -> SetupInfo:
    """Detecta setup activo en AC y genera un ID estable por contenido."""
    documents_root = Path(os.path.expanduser("~/Documents/Assetto Corsa"))
    race_ini = documents_root / "cfg" / "race.ini"

    car_model = "unknown"
    track_name = "unknown"
    track_layout = ""
    setup_hint = ""

    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str  # preserve key casing

    if race_ini.exists():
        try:
            parser.read(race_ini, encoding="utf-8")
            car_model = _safe_get(parser, "RACE", "MODEL", "unknown")
            track_name = _safe_get(parser, "RACE", "TRACK", "unknown")
            track_layout = _safe_get(parser, "RACE", "CONFIG_TRACK", "")
            setup_hint = _safe_get(parser, "CAR_0", "SETUP", "")
        except Exception:
            pass

    setup_file = _resolve_setup_file(documents_root, car_model, track_name, setup_hint)

    if setup_file is not None and setup_file.exists():
        try:
            setup_text = setup_file.read_text(encoding="utf-8", errors="replace")
        except Exception:
            setup_text = setup_file.read_text(encoding="latin-1", errors="replace")

        setup_id = _build_setup_id(
            car_model=car_model,
            track_name=track_name,
            setup_text=setup_text,
        )
        return SetupInfo(
            setup_id=setup_id,
            car_model=car_model,
            track_name=track_name,
            track_layout=track_layout,
            source_path=str(setup_file),
            setup_label=setup_file.stem,
            setup_text=setup_text,
        )

    # Fallback sin archivo detectado: ID estable por metadatos disponibles.
    fallback_label = setup_hint.strip() if setup_hint.strip() else "default"
    fallback_seed = f"car={car_model}|track={track_name}|hint={fallback_label}"
    setup_id = hashlib.sha1(fallback_seed.encode("utf-8")).hexdigest()[:12]
    return SetupInfo(
        setup_id=setup_id,
        car_model=car_model,
        track_name=track_name,
        track_layout=track_layout,
        source_path=None,
        setup_label=fallback_label,
        setup_text=None,
    )


def save_setup_document(setup: SetupInfo, output_dir: str = "session_logs/setups") -> str:
    """Guarda un documento por setup usando el ID como nombre de archivo."""
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    path = out_dir / f"{setup.setup_id}setup.txt"
    if path.exists():
        return str(path)

    lines: list[str] = []
    lines.append("=" * 80)
    lines.append("PITRADIO SETUP RECORD")
    lines.append("=" * 80)
    lines.append("")
    lines.append(f"Setup ID: {setup.setup_id}")
    lines.append(f"Car: {setup.car_model}")
    lines.append(f"Track: {setup.track_name}")
    lines.append(f"Label: {setup.setup_label}")
    lines.append(f"Source: {setup.source_path or 'not-found'}")
    lines.append("")

    if setup.setup_text:
        lines.append("Setup file content:")
        lines.append("-" * 80)
        lines.append(setup.setup_text)
    else:
        lines.append("No setup file content was detected for this session.")

    path.write_text("\n".join(lines), encoding="utf-8")
    return str(path)


def _resolve_setup_file(
    documents_root: Path,
    car_model: str,
    track_name: str,
    setup_hint: str,
) -> Path | None:
    setups_root = documents_root / "setups"
    if not setups_root.exists() or car_model == "unknown":
        return None

    car_dir = setups_root / car_model
    if not car_dir.exists():
        return None

    track_dirs = _candidate_track_dirs(car_dir, track_name)

    # 1) Si race.ini trae setup explícito, intentamos resolverlo primero.
    if setup_hint.strip():
        hint = setup_hint.strip().replace("\\", "/")
        hint_path = Path(hint)

        candidates_from_hint: list[Path] = []
        if hint_path.suffix.lower() == ".ini":
            candidates_from_hint.extend([track_dir / hint_path.name for track_dir in track_dirs])
        else:
            candidates_from_hint.extend([track_dir / f"{hint}.ini" for track_dir in track_dirs])
            candidates_from_hint.extend([track_dir / hint for track_dir in track_dirs])

        for candidate in candidates_from_hint:
            if candidate.exists():
                return candidate

    # 2) Fallback: último setup modificado de la pista actual.
    ini_candidates: list[Path] = []
    for track_dir in track_dirs:
        if not track_dir.exists():
            continue
        ini_candidates.extend(track_dir.glob("*.ini"))

    if not ini_candidates:
        return None

    ini_candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return ini_candidates[0]


def _candidate_track_dirs(car_dir: Path, track_name: str) -> list[Path]:
    if track_name == "unknown":
        return [car_dir]

    # AC suele usar carpetas por track base y/o track_config.
    dirs: list[Path] = []
    direct = car_dir / track_name
    dirs.append(direct)

    if "_" in track_name:
        base = track_name.split("_", 1)[0]
        dirs.append(car_dir / base)

    # Candidatos flexibles por prefijo para variaciones de config.
    prefix = f"{track_name}_"
    for child in car_dir.iterdir():
        if not child.is_dir():
            continue
        name = child.name
        if name == track_name or name.startswith(prefix):
            dirs.append(child)

    # Dedupe preservando orden.
    seen: set[str] = set()
    unique: list[Path] = []
    for d in dirs:
        key = str(d).lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(d)
    return unique


def _build_setup_id(car_model: str, track_name: str, setup_text: str) -> str:
    normalized = setup_text.replace("\r\n", "\n").strip()
    seed = f"car={car_model}|setup={normalized}"
    return hashlib.sha1(seed.encode("utf-8")).hexdigest()[:12]


def _safe_get(parser: configparser.ConfigParser, section: str, key: str, default: str) -> str:
    if parser.has_section(section) and parser.has_option(section, key):
        value = parser.get(section, key).strip()
        if value:
            return value
    return default
