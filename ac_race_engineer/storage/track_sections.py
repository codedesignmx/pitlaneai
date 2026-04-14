"""
track_sections.py
-----------------
Lee sections.ini del circuito activo en AC y devuelve nombres reales de curvas/zonas.

sections.ini format:
    [SECTION_0]
    IN=0.0
    OUT=0.0945
    TEXT=Wheatcroft Straight
"""
from __future__ import annotations

import configparser
import os
from pathlib import Path


# ---------------------------------------------------------------------------
# AC install path auto-detection
# ---------------------------------------------------------------------------

def find_ac_content_path() -> Path | None:
    """Busca la carpeta content/tracks de AC en las rutas de Steam más comunes."""
    candidates = [
        Path(os.path.expandvars(r"%ProgramFiles(x86)%\Steam\steamapps\common\assettocorsa")),
        Path(r"C:\Program Files (x86)\Steam\steamapps\common\assettocorsa"),
        Path(r"D:\Steam\steamapps\common\assettocorsa"),
        Path(r"D:\SteamLibrary\steamapps\common\assettocorsa"),
        Path(r"E:\Steam\steamapps\common\assettocorsa"),
        Path(r"E:\SteamLibrary\steamapps\common\assettocorsa"),
        Path(r"F:\SteamLibrary\steamapps\common\assettocorsa"),
    ]
    for path in candidates:
        content = path / "content" / "tracks"
        if content.exists():
            return content
    return None


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def load_sections(track_name: str, layout: str | None) -> list[dict]:
    """
    Carga sections.ini para el track+layout especificado.
    Devuelve lista de dicts: [{"in": float, "out": float, "name": str}, ...]
    Retorna [] si no se encuentra o no tiene secciones.
    """
    tracks_root = find_ac_content_path()
    if tracks_root is None:
        return []

    # Intentar con layout y sin layout
    paths_to_try: list[Path] = []
    if layout:
        paths_to_try.append(tracks_root / track_name / layout / "data" / "sections.ini")
    paths_to_try.append(tracks_root / track_name / "data" / "sections.ini")

    sections_ini: Path | None = None
    for p in paths_to_try:
        if p.exists():
            sections_ini = p
            break

    if sections_ini is None:
        return []

    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str
    try:
        parser.read(sections_ini, encoding="utf-8")
    except Exception:
        return []

    result: list[dict] = []
    for section in parser.sections():
        if not section.startswith("SECTION_"):
            continue
        try:
            in_pos = float(parser.get(section, "IN"))
            out_pos = float(parser.get(section, "OUT"))
            text = parser.get(section, "TEXT", fallback="").strip()
        except (configparser.NoOptionError, ValueError):
            continue
        if text:
            result.append({"in": in_pos, "out": out_pos, "name": text})

    return result


# ---------------------------------------------------------------------------
# Position → name lookup
# ---------------------------------------------------------------------------

def label_for_position(normalized_pos: float, sections: list[dict]) -> str | None:
    """
    Dado un normalized_car_position (0.0–1.0) y la lista de secciones,
    devuelve el nombre de la sección que lo contiene, o None.

    Las secciones con OUT < IN se tratan como wrapping (no deberían darse
    en circuitos normales, pero se ignoran para evitar falsos positivos).
    """
    for sec in sections:
        s_in = sec["in"]
        s_out = sec["out"]
        if s_in <= s_out:
            if s_in <= normalized_pos <= s_out:
                return sec["name"]
        # wrap-around (s_out < s_in), e.g. start/finish zone
        else:
            if normalized_pos >= s_in or normalized_pos <= s_out:
                return sec["name"]
    return None
