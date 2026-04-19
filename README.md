# Pendientes
- Final de race incorrecto:
Final de race. Terminamos en posición 3. Mejor vuelta 1 minuto : 5 segundos . 943 milésimas. Repaso general. Posición 1, #1 | Taeeon Kim, mejor vuelta 1 minuto : 5 segundos . 982 milésimas. Posición 2, #4 | Gabriel Rannseier, mejor vuelta 1 minuto : 6 segundos . 523 milésimas. Posición 3, #8 | Marco Cabanas, mejor vuelta 1 minuto : 6 segundos . 062 milésimas. Posición 4, #7 | Alex Rannseier, mejor vuelta 1 minuto : 7 segundos . 298 milésimas. Posición 5, #9 | Javi Pardo, mejor vuelta 1 minuto : 7 segundos . 076 milésimas. Posición 6, #2 | Jack Alec, mejor vuelta 1 minuto : 7 segundos . 048 milésimas. Posición 7, #6 | Chad Dillon, mejor vuelta 1 minuto : 5 segundos . 834 milésimas. Posición 8, #3 | Inaki Berazadi, mejor vuelta 1 minuto : 6 segundos . 063 milésimas. Posición 9, #5 | Viktoria Kitarta.
También me he dado cuenta que marca final de race justo cuando se acaba el tiempo, y digamos que no se si sea del todo correcto, por que cuando justo se acaba el tiempo, después de eso, cuando el lider pasa se marca última vuelta, entonces hasta que el lider llega a la siguiente realmente acaba la carrera, y de hecho sale una bandera a cuadros en cmrt, quizá podría ser esta la referencia para tomar correctamente el final de carrera y con esto dar el resumen.
- Cuando es carrera por tiempo sale esto: [SPEAK] Consumo 2.00 litros por vuelta. Combustible para 5 coma 6 vueltas. Carrera por tiempo: quedan aprox 2863 coma 3 vueltas. Carga al menos 5722.0 litros.
Y pues noto algunos erroes en cuanto a esto por que mira lo que dice del combustible al final (que es lo real y correcto)
[SPEAK] Combustible necesario 0.8 litros. Margen 0.5 minutos. (Esto es lo que va diciendo del combustible, que según eso es necesario)
[SPEAK] Lo malo: última vuelta lejos de tu mejor ritmo, mucho ruido entre vueltas, falta una tanda limpia. Sobró combustible al final; la próxima carrera puedes recortar aproximadamente 5.7 litros. (Esto es lo que dice al final de la carrera)

- En cualquier sesión, si el piloto pide calcular el combustible para x vueltas o x tiempo
- Retroalimentar si el piloto dice Posición, en que posición vamos, etc.
- Si el piloto habla, cortar TTS y responder primero al comando del piloto siempre.
- Perfil de voz por contexto: práctica más analítico, qualy más breve, carrera ultra-corto.
- Box next lap
- Modo crítico: Si hay alerta crítica, silenciar mensajes no críticos automáticamente por unos segundos.
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
- Revisión de fuel: En cualquier punto y sesión, si a la siguiente vuelta no paramos a repostar, es necesario priorizar mandar mensaje para cargar
- EstrategiasAi: What-if engine” por voz:
“Si paro en 3 vueltas y cargo X litros, ¿dónde salgo?”
- Dashboard web companion: sector, comparación contra stints previos. “Highlights automáticos” de la sesión: Sistema de perfiles: Piloto, coche, pista, setup, condiciones, y recomendaciones persistentes. Presets de personalidad de ingeniero: Calmado, agresivo, minimalista, data-driven.
- Appanion móvil (segunda pantalla)
- Onboarding: Primeros 2-3 minutos con guía mínima: comandos clave, cómo cancelar, cómo pedir resumen. Comando Ayuda rápida.
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
  - Activa el asistente local.
  - Responde `Loud and Clear`.
  - No se manda al asistente IA; es un comando local de verificacion.
  - Detecta variantes comunes del reconocimiento como `radio chec`, `radio chek` o `radio shrek`.
  - **Si estás en pits:** Briefing objetivo completo
    - Pista y sesión actual
    - Mejor vuelta (si la hay)
    - Objetivo de sesión (ritmo target, vueltas, consejo de setup)
    - Referencia competitiva si hay timing en vivo
    - Combustible actual
  - **Si estás en pista:** Briefing estándar (pista, sesión, ritmo, combustible y contexto competitivo si existe)

- `Cancelar Radio`
  - Desactiva el asistente de voz.

- `Que lugar vamos`
- `Posicion`
- `Posición`
- `Puesto`
  - Responde posición actual y tiempo según la sesión.
  - **En práctica / qualy:**
    - Tu posición actual
    - Tu mejor tiempo o última vuelta
    - Líder actual y cuánto estás del mejor tiempo
    - Si aplica, cuánto te falta respecto al piloto inmediatamente delante
  - **En carrera:**
    - Tu posición actual
    - Tu última vuelta o mejor vuelta
    - Quién va delante y por cuánto
    - Quién va detrás y por cuánto

- `Estado del auto` / `Estado del coche` / `Daños` / `Colisión`
  - Reporta: velocidad actual, combustible, y última colisión detectada (si aplica).

### Metricas objetivo (nuevo)

- `Objetivo` / `Métricas` / `Ritmo` / `Pace` / `Consumo` / `Fuel` / `Readiness` / `Listos`
  - Reporte rápido en vivo:
    - Mejor vuelta y promedio últimas 3.
    - Consistencia (excelente / buena).
    - Combustible estimado para X vueltas.
    - Referencia competitiva cuando hay timing disponible.
  - **En práctica / qualy:** puede decir líder actual y gap respecto al mejor tiempo.
  - **En carrera:** puede decir piloto delante y detrás con sus diferencias.
  - Ideal durante practica para evaluar setup y pace target.

- `Informe` / `Briefing` / `Situación general`
  - Mismo briefing que `Radio Check` (pista, sesión, ritmo, combustible, clima).

### Resumen de sesión por radio

- `Resumen`
- `Resumen de sesión` / `Resumen de sesion`
- `Resumen de carrera`
- `Resumen de práctica` / `Resumen de practica`
- `Resumen de qualy`
- `Resumen final`
- `Repaso general`
  - Entrega un resumen de la sesión actual bajo demanda (sin esperar al cierre oficial).
  - Usa la mejor información disponible de posición, mejor vuelta y rivales.

### Box + setup feedback

- `Box`
- `Box box`
- Variantes ASR aceptadas: `box bo`, `boxbo`, `boxx`, `vox`, `bos`
  - Activa reporte de entrada a pits y evaluación de objetivos.
  - Si hay recomendación de setup pendiente, la prioriza y pide validación.

- Feedback de resultado tras probar ajuste (2 vueltas aprox):
  - Mejora: `mejoró`, `mejor`, `va mejor`, `funcionó`, `gano tiempo`, `se siente mejor`, `más estable`
  - Igual: `igual`, `sin cambio`, `sin cambios`, `sin diferencia`, `se siente igual`, `parecido`
  - Empeora: `empeoró`, `peor`, `salió peor`, `no funcionó`, `no sirvió`, `más inestable`, `perdió agarre`
  - Si detecta mejora, mantiene ese ajuste y puede proponer automáticamente la siguiente línea.

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

### Respuesta en consola

- Todo lo que el sistema realmente va a decir por voz aparece en consola como:

```text
[SPEAK] ...
```

- Si una respuesta vieja de IA llega tarde y ya hubo un comando local más reciente, esa respuesta se descarta para no mezclar mensajes.

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
- En practica y qualy, al pasar por meta puede decir quien lidera y cuanto te falta respecto al mejor tiempo.
- En carrera, al pasar por meta puede decir quien va delante y detras, con sus gaps.
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
- Los tiempos objetivo y competitivos se locutan en formato de voz para evitar lecturas raras tipo "1 de la mañana".
