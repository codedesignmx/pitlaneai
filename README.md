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
  - Da un briefing inicial (pista, sesion, estado, ritmo, combustible).

- `Cancelar Radio`
  - Desactiva el asistente de voz.

- `Que lugar vamos`
  - Responde posicion actual y tiempo segun la sesion (practica, qualy o carrera).

### Conversacion / memoria

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

## Notas

- El sistema esta preparado para practica, qualy y carrera.
- Funciona en offline y online (incluyendo servidores tipo LFMS) mientras Assetto Corsa exponga shared memory.
- Algunos datos avanzados (ej: meteo completa por feed base) pueden no estar disponibles en todas las versiones/builds del juego.
- En tiempo real, los nombres/tiempos de *otros* pilotos dependen de la telemetria disponible del servidor o de un feed externo.
