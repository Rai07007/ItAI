# ItAI — aplicación de escritorio

**Windows:** consulta [instalación y traslado del equipo](WINDOWS.md). Los pasos de `ItAI.app` de esta guía corresponden a macOS.

Ahora se usa una **ventana nativa de Python/Qt**, sin navegador: visita en curso, gráficos, resultados y preparación del equipo. Se conserva el protocolo de 30 minutos y 720 adquisiciones, cada 2,5 segundos. El firmware que ya cargaste es compatible. Se respeta DHT11 DATA → GPIO16 y MQ135 A0 → GPIO34 con divisor; D0 continúa desactivado.

## Abrir y medir

Abre **ItAI.app**, que está en esta misma carpeta. También puedes ejecutar el mismo `Medir.command` de antes: ahora abre la aplicación de escritorio y no pide comandos ni códigos en Terminal. No muevas ItAI.app sola: depende de los archivos y del entorno `.venv` de esta carpeta.

1. Conecta el Mac a **ItAI-Local**, contraseña **CAMBIAR_WIFI_2026**. No hace falta internet.
2. En **Preparar equipo**, usa **Comprobar conexión**. La comprobación no inicia una medición.
3. Si quieres ppm estimados, prepara una referencia del MQ135 como se explica abajo.
4. En **Visita en curso**, introduce un código anónimo (por ejemplo H001) y pulsa **Iniciar visita**.
5. Las lecturas se guardan a medida que llegan. Puedes seleccionar temperatura, humedad o gases en el gráfico y pasar el puntero para ver una lectura concreta.
6. Al terminar, **Resultados** muestra la explicación cercana y una tabla con media, rango, N válido, pendiente, IC95 % y p ajustada. **Ver archivos** abre CSV, resultados detallados y gráficos exportados.

**Terminar** detiene la sesión y descarga el lote final antes de calcular el informe. **Recuperar** permite retomar la descarga de una carpeta existente. No reinicies el ESP32 si necesitas recuperar lecturas: su memoria durante cortes Wi-Fi es RAM, no almacenamiento ante cortes de energía. La app no deja empezar otra sesión si detecta lecturas pendientes en la ESP32 que no están guardadas en este Mac.

## Ppm estimados: qué cambió y cómo se calculan

Se implementó el modelo empírico solicitado:

```
V_A0 = (mV_GPIO / 1000) × factor_divisor
q = Rs / RL_efectiva = V_circuito / V_A0 - 1
q0 = q_referencia / (ppm_referencia / 116.6020)^(1 / -2.7690)
ppm_estimados = 116.6020 × (q / q0)^(-2.7690)
```

Se usan los mV calibrados que ya entrega la ESP32, no `ADC × 3,3/4095` suponiendo un ADC ideal. El voltaje que alimenta el circuito del sensor y el voltaje del GPIO son distintos. El factor del divisor es `(R_superior + R_inferior) / R_inferior`; debe coincidir con el circuito físico. R superior: A0 → GPIO; R inferior: GPIO → GND.

No se necesita adivinar RL para el cociente `Rs/R0` cuando la referencia y las visitas usan exactamente el mismo circuito: el factor RL efectivo se cancela. Por eso la app no inventa una resistencia de 10 kΩ. Tampoco afirma una resistencia absoluta Rs en kΩ sin conocer la carga efectiva, que puede incluir el divisor en paralelo.

Los coeficientes son los del modelo empírico propuesto, **no una calibración de CO₂ validada por Winsen**. El fabricante describe un sensor multigás. El resultado se llama siempre **eCO₂ estimado / ppm estimados** y no concentración real garantizada de CO₂.

### Por qué no se copió literalmente la calibración propuesta

Con `R0 = Rs/3,6`, la fórmula `116,602 × (Rs/R0)^-2,769` devuelve **3,36 ppm** para el mismo aire usado al calibrar. Esa combinación no sirve como referencia de CO₂ ambiental. Además, usar 3,3 V a la vez como referencia ADC y alimentación del sensor ignora la alimentación real y el divisor. Aquí la referencia es una concentración explícita, con origen registrado.

### Preparar la referencia

En **Preparar equipo → Preparar referencia**, indica:

- Alimentación del circuito MQ, en voltios (habitualmente 5 V; confirmar el montaje).
- Factor real del divisor. Empieza vacío: no se activa un valor supuesto sin que lo indiques.
- Concentración de referencia y si proviene de un **instrumento de referencia** o de un **supuesto exterior**.
- Una nota sobre instrumento, lugar y condiciones.
- Confirmación de que completaste el acondicionamiento y verificaste el circuito.

La app registra 24 lecturas durante 60 segundos y exige al menos 20 válidas, fuera del arranque y sin saturación. Rechaza una referencia inestable (CV de q >5 %; criterio operativo, no validación metrológica). Guarda el perfil con fecha, identificador, modelo, valores y origen de la referencia. Cada visita guarda una copia de ese perfil: un cambio futuro no modifica retrospectivamente los datos anteriores.

Si eliges una referencia **supuesta**, la salida se normaliza a ese supuesto y queda marcada como tal. Eso no mide la concentración de referencia ni prueba exactitud. Incluso un ajuste con instrumento en un solo punto no valida toda la curva ni elimina sensibilidad a otros gases, temperatura o humedad. Para otro sensor o un cambio de alimentación/divisor, prepara otra referencia.

No se recalibra automáticamente al encender ni al entrar a un hogar. No se asigna aire limpio a una vivienda por defecto. Valores nulos, arranque, saturación o circuitos inconsistentes producen «sin estimación», no ceros ni ppm artificiales. Fuera del intervalo numérico operativo 100–5000 ppm se marca extrapolación y se excluye de inferencia; ese intervalo no es una especificación de exactitud.

## Rangos del paper y estadística

Temperatura: riesgo <16 o >32 °C; advertencia 16≤T<18 o 26<T≤32; aceptable 18≤T≤26. Humedad: fuera del intervalo del paper <20 o >90 %; advertencia 20≤HR<30 o 60<HR≤70; óptimo 30≤HR≤60; humedad elevada 70<HR≤90. Los límites instrumentales conservadores siguen siendo 0–50 °C y 20–80 % para inferencia DHT11.

Sobre los **ppm estimados**, se conservan las categorías del paper: <1000 aceptable; 1000–2000 advertencia; >2000 riesgo. Son categorías exploratorias del modelo; no certifican el aire ni sustituyen medición selectiva de CO₂. La pantalla muestra siempre «estimados» y si la referencia fue medida o supuesta.

Continúan descriptivos, distribuciones, correlaciones descriptivas y regresión temporal con IC95 % HAC (24 rezagos y sensibilidad a 12/48), p de pendiente ajustada por Holm. Se añade la serie de ppm; ADC y mV originales se conservan. ADC y ppm provienen del mismo sensor, por lo que su correlación no representa evidencia independiente. El IC de la tendencia no incorpora el error de la curva ni de la referencia. Se usa el tramo válido consecutivo más largo y no se rellenan huecos.

El índice global de vulnerabilidad no está definido en el paper: se mantiene un porcentaje descriptivo T/HR fuera de rango, separado de gases. Visión, moho y fisuras no se evalúan en este programa. Las 720 adquisiciones no son 720 hogares independientes. Se mantiene la adaptación autorizada de 1000 a 720 adquisiciones para las visitas de 30 minutos.

## Una explicación más cercana

El resumen se presenta dentro de la aplicación con lenguaje sencillo: lo observado, qué cambió y qué conviene revisar. Ollama selecciona apertura/cierre y ordena frases vinculadas a resultados calculados. La redacción evita cifras inventadas, diagnósticos no observados y culpar a los residentes. En el resumen automático, las cifras y unidades proceden de frases calculadas en Python. El chat conversacional descrito abajo genera texto libre y puede equivocarse.

Ollama se ejecuta en este Mac y usa modelos ya descargados. En Preparar equipo puedes elegir `auto`, un modelo como `llama3:latest`, o `none`. Si no está disponible, la app muestra un resumen cercano construido por Python y lo identifica como tal. Los cálculos, gráficos y captura no dependen del LLM.

## Archivos y respaldo

- `mediciones/`: sesiones reales; SQLite con datos originales, `sesion.json` con referencia de conversión, CSV, resultados JSON, gráficos e informe exportable.
- `referencias/`: lecturas de referencia separadas de las visitas.
- `mq_reference.json`: perfil vigente, creado solo tras preparar una referencia válida.
- `desktop_settings.json`: alimentación/divisor indicados por el operador.
- `demo_escritorio/`: ejemplo explícitamente simulado; nunca usar como resultado real.
- `respaldo_v1/`: implementación anterior conservada.

El informe HTML sigue disponible como archivo exportable; **no es la interfaz de medición**. La aplicación usa widgets nativos Qt, no un navegador embebido. La versión CLI anterior permanece para recuperación, pero la referencia de ppm debe estar en los metadatos de la sesión; no aplica retroactivamente un perfil nuevo a mediciones antiguas.

## Dependencias y verificación

El entorno de este Mac ya incluye NumPy, SciPy, Pandas, Matplotlib, Statsmodels y Qt/PySide6. En otra computadora, recrea `.venv` e instala `requirements.txt`. La app macOS se distribuye como lanzador local, no como binario autónomo firmado; debe permanecer junto a `.venv` y los módulos Python.

Se probaron límites, inferencia, valores ausentes, duplicados, conversión inversa a la referencia, curva conocida, voltajes inválidos, saturación, referencia inestable/insuficiente, protección de lecturas pendientes y recuperación de la captura nativa. Se revisó visualmente la ventana con datos simulados. Sigue pendiente el ensayo físico con tu divisor y una referencia registrada; no se ha calibrado tu sensor desde aquí.

Fuentes: [fórmula del circuito y condiciones del MQ135, Winsen](https://www.winsen-sensor.com/manual/mq135.html), [implementación pública del modelo empírico MQ135](https://github.com/GeorgK/MQ135/blob/master/MQ135.cpp), [Qt para Python](https://doc.qt.io/qtforpython-6.11/index.html).


## Análisis ampliado y conversación dentro de la app

En **Resultados** aparecen cuatro pestañas:

- **Resumen:** explicación cercana de lo observado, episodios y próximos pasos.
- **Episodios:** número de lecturas fuera de cada umbral, porcentaje sobre lecturas válidas, duración aproximada y episodio más largo. Cada lectura representa como máximo los 2,5 segundos precedentes; los huecos y las lecturas inválidas interrumpen episodios. Los umbrales se superponen: no sumar sus minutos.
- **Plan de acción:** qué dato motivó cada recomendación, pasos concretos y cómo comprobar el resultado. Incluye fuentes sobre vivienda, humedad y características del sensor. Las prioridades orientan la revisión; no constituyen una escala validada de vulnerabilidad.
- **Estadística:** cobertura, percentiles, medias de los primeros y últimos cinco minutos, cambio ajustado e intervalos de confianza. La comparación entre ventanas es descriptiva; la inferencia sigue usando regresión temporal HAC y ajuste Holm. Los intervalos son puntuales, no intervalos simultáneos ajustados por multiplicidad.

Elige **Preguntar a ItAI** en la barra lateral o **Preguntarle a ItAI** desde Resultados. Puedes preguntar qué revisar primero, qué significa una tendencia o cómo preparar la referencia. La respuesta aparece progresivamente. **Detener** solicita cancelar; puede tardar hasta que Ollama envíe el siguiente fragmento o venza el tiempo de espera. **Nueva conversación** limpia el historial. **Guardar conversación** exporta un texto separado del informe.

El chat usa el modelo instalado que elijas en **Preparar equipo**, por ejemplo `llama3:latest`. Ollama debe estar disponible; el lanzador intenta iniciarlo automáticamente. El chat conecta únicamente con `127.0.0.1:11434` y rechaza modelos remotos. No necesita internet una vez descargado el modelo.

Se envían estadísticas de la visita seleccionada, su calidad, referencia y recomendaciones; no se incluyen identificadores de hogar ni notas del perfil. Lo que escribas sí forma parte de la conversación local: utiliza códigos y evita nombres o direcciones. El historial reciente se limita para no saturar el contexto. Al cambiar de visita se limpia y se descartan respuestas antiguas que lleguen tarde. Durante la captura, la pregunta usa una instantánea parcial y no interrumpe la adquisición.

La conversación es texto generado por IA: puede equivocarse y debe contrastarse con las cifras. No modifica lecturas, referencias ni conclusiones calculadas. El resumen automático mantiene su mecanismo de frases verificadas. Las visitas antiguas se recalculan en memoria al abrirlas para mostrar las pestañas nuevas, sin sobrescribir sus archivos originales.

Para cargar esta actualización, termina cualquier captura en curso, cierra la app y vuelve a abrir **ItAI.app** o **Medir.command**. No es necesario volver a cargar el firmware por estos cambios.
