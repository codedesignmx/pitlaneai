# Pendientes
- *En carrera, estos mensajes:
    [AUTO] Carrera. Promedio ultimas tres 2 minutos : 3 segundos . 988 milesimas. Prioriza traccion y cuida neumatico. Fuel para 4.3 vueltas.
Quita lo de carrera. Solo que diga la diferencia que tenemos con el de adelante y la ventaja que le tenemos al de atrás.
Quita lo de prioriza tracción y cuida neumático y que si siga diciendo lo de Fuel para 4.3 vueltas.
- *En una parte donde dice las vueltas, la toma como si fuera miles, creo lo correcto es x.x vueltas.
- *En práctica, qualy y carrera cuando alguien hace record el nombre y tiempos de los demás competidores de lfms no lo dice
- *Se puede saber el estado del auto, si tiene daños y detectar colisiones y saber quién nos golpeó o a quién golpeamos

- Banderas amarillas
- Banderas azules
- Si pregunto que lugar estamos, qualy, practica, carrera que de el lugar y el tiempo
- Volumen subirlo a 2 y promediar los demás.
- El clima, viento, temperatura del asfalto no lo dice
- Que costo tiene aproximado por vuelta con lo del asistente AI

- Estado llantas
# AC Race Engineer MVP
Race engineer por voz para Assetto Corsa (shared memory directo), con eventos de ritmo/combustible, asistente IA y feedback automatico.

## Requisitos

- Python 3.11+ recomendado (3.10 funciona en tu entorno actual)
- Assetto Corsa ejecutandose
- Dependencias de `requirements.txt`

## Ejecutar

```bash
python app.py
```

## Comandos de voz

### Activacion y estado

- `Radio Check`
  - Activa el asistente.
  - Responde `Loud and Clear`.
  - **Si estás en pits:** Briefing objetivo completo
    - Pista y sesión actual
    - Mejor vuelta (si la hay)
    - Objetivo de sesión (ritmo target, vueltas, consejo de setup)
    - Combustible actual
  - **Si estás en pista:** Briefing estándar (pista, sesión, ritmo, combustible)

- `Cancelar Radio`
  - Desactiva el asistente de voz.

- `Que lugar vamos`
  - Responde posicion actual y tiempo segun la sesion (practica, qualy o carrera).

- `Estado del auto` / `Estado del coche` / `Daños` / `Colisión`
  - Reporta: velocidad actual, combustible, y última colisión detectada (si aplica).

### Metricas objetivo (nuevo)

- `Objetivo` / `Métricas` / `Ritmo` / `Pace` / `Consumo` / `Fuel` / `Readiness` / `Listos`
  - Reporte rápido en vivo:
    - Mejor vuelta y promedio últimas 3.
    - Consistencia (excelente / buena).
    - Combustible estimado para X vueltas.
  - Ideal durante practica para evaluar setup y pace target.

- `Informe` / `Briefing` / `Situación general`
  - Mismo briefing que `Radio Check` (pista, sesión, ritmo, combustible, clima).

- `Resetear hilo`
- `Reiniciar hilo`
- `Borrar memoria`
  - Reinicia el hilo conversacional con OpenAI.

### Volumen

- `Volumen bajo`
- `Volumen medio`
- `Volumen alto`

Perfiles actuales de volumen:

- Bajo: `1.00`
- Medio: `1.50`
- Alto: `2.00`

Tambien puedes dejar un valor por defecto en `config.py`:

- `voice_volume_multiplier`

## Eventos automaticos (sin pedirlos por voz)

- `new_best_lap`
- `pace_drop`
- `pace_improving`
- `stint_consistent`
- `fuel_update`
- `traffic_close`
- `incident_nearby`

### Timing y posiciones

- Cuando marcas `new_best_lap`, el asistente anuncia tambien tu posicion actual.
- Si en ese momento estas en `P1`, lo canta como mejor tiempo general.
- Si hay feed de standings en vivo, anuncia mejoras de otros pilotos con nombre, tiempo y posicion.
- Si alguien marca record de sesion (P1), lo canta con nombre, tiempo y posicion.
- Al finalizar sesion (`practice`, `qualifying`, `race`) genera un resumen:
  - Primero tu posicion final y mejor vuelta.
  - Luego repaso general de posiciones si encuentra archivo de resultados local.

Rutas por defecto para buscar resultados:

- `~/Documents/Assetto Corsa/out/results`
- `~/OneDrive/Documents/Assetto Corsa/out/results`
- `~/AppData/Local/AcTools Content Manager/Data/Online`
- `~/AppData/Local/AcTools Content Manager/Logs`

Se pueden cambiar en `config.py` con `results_search_dirs`.

Frecuencia de lectura del feed de standings: `standings_poll_interval_seconds`.

Diagnostico en consola de timing:

- `[TIMING] feed activo con N pilotos`
- `[TIMING] sin standings en vivo (...)`

### LFMS + CMRT (solucion recomendada)

Si LFMS no deja standings en JSON por defecto, usa el exportador Lua `PITRADIO-Timing-Export`.

Pasos:

- En Content Manager, habilita la app Lua `PITRADIO Timing Export`.
- Entra a pista (online LFMS o cualquier sesion online).
- Verifica que se cree `pitradio_live_standings.json` en `Documents/Assetto Corsa/out/results`.
- Ejecuta PITRADIO y confirma que en consola aparezca `[TIMING] feed activo con N pilotos`.

Con eso el asistente ya puede anunciar nombre, tiempo y posicion de otros pilotos.

## Sistema de Sesiones y Metricas Objetivo

### Archivos guardados

Al finalizar una sesion de practica (o cualquier otra), el sistema guarda automaticamente:

- **`session_logs/session_<track>_<tipo>_<timestamp>.txt`**
  - Resumen legible con metrics clave:
    - Mejor vuelta y promedio.
    - Consumo de combustible.
    - Condiciones (grip, temperatura aire/asfalto).
    - Detalle de últimas 20 vueltas.
    - Setups testeados (si cambiaste).

- **`session_logs/session_<track>_<tipo>_<timestamp>.json`**
  - Datos raw (vueltas, fuel, condiciones) para análisis futuro.

### Metricas objetivo en 30 minutos

El asistente calcula automaticamente al fin de sesion:

- **Pace promedio**: ritmo sostenible de carrera.
- **Degradacion**: cuantos segundos pierdes por vuelta en long-run.
- **Fuel prediction**: combustible exacto para X minutos restantes + margen.
- **Setup score** (futuro): consistencia de cada setup testeado.

Ejemplo de reporte final:

```
[OBJECTIVE] Ritmo de carrera 1:45,250. Combustible necesario 12.3 litros. Margen 0.5 minutos.
```

### Modo Push-to-Talk (PTT)

Si tienes un control de juego conectado:

- **Detecta automaticamente el control**.
- **Presiona botón B (configurado en `config.py` → `ptt_button_index`)**.
- **Habla mientras sostienes**: todo se graba en vivo.
- **Suelta botón**: se procesa el audio y el asistente responde.

No requiere decir "Radio Check"; funciona como un PTT real de radio.

Para detectar el numero de tu boton:

```bash
python -c "from ac_race_engineer.audio.controller import print_button_map; print_button_map()"
```

## Objetivos de Sesión (Briefing en Pits)

Al decir **`Radio Check` desde los pits**, el asistente anuncia el objetivo de sesión personalizado.

### Objetivos por defecto

- **Práctica**: Búsqueda de ritmo y consistencia → Target pace ~1:45.000
- **Clasificación**: Maximizar una vuelta limpia → Target pace ~1:43.500
- **Carrera**: Ritmo sostenible + gestión de fuel → Target pace ~1:45.000

Cada objetivo incluye:
- Meta de sesión (ritmo, vueltas, o carrera completa)
- Consejo de setup (presión, bias, diferenciales)
- Estrategia de fuel

### Personalizar objetivos

Edita `config.py` → `session_objectives` para cambiar targets:

```python
"practice": {
    "goal": "Búsqueda de ritmo y consistencia",
    "target_pace": "1:45.000",  # ← Cambia este valor
    "target_laps": "10 vueltas",
    "setup_advice": "Comienza con setup anterior o default.",
    "fuel_strategy": "Llena para 20-30 vueltas."
}
```

### Flujo típico de sesión (30 min)

1. **Pits (0-1 min)**: Di `Radio Check` → recibes briefing objetivo
2. **En pista (1-28 min)**: Realiza vueltas
   - Comando `Objetivo` para revisar métricas en vivo
3. **Final (28-30 min)**: Sistema guarda resumen automático en `session_logs/`

## Notas

- El sistema esta preparado para practica, qualy y carrera.
- Funciona en offline y online (incluyendo servidores tipo LFMS) mientras Assetto Corsa exponga shared memory.
- Algunos datos avanzados (ej: meteo completa por feed base) pueden no estar disponibles en todas las versiones/builds del juego.
- En tiempo real, los nombres/tiempos de *otros* pilotos dependen de la telemetria disponible del servidor o de un feed externo.
