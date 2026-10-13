# ItAI · medición ambiental local

Autonomous robot created to test and measure environmental variables in homes with qualitative housing deficits.

Aplicación de escritorio Python/Qt conectada por Wi-Fi a un ESP32 Dev Kit. Registra temperatura, humedad y señal MQ135, muestra gráficos temporales y calcula un análisis exploratorio con recomendaciones basadas en los resultados. Incluye conversación con Ollama ejecutada localmente.

## Qué incluye

- Firmware ESP32: DHT11 en GPIO16 y MQ135 en GPIO34 mediante un divisor adecuado.
- Sesiones de 30 minutos: 720 adquisiciones cada 2,5 segundos.
- Persistencia SQLite y recuperación de lecturas después de desconexiones, mientras la ESP32 siga encendida.
- Descriptivos, episodios, regresión temporal HAC, intervalos de confianza y ajuste Holm.
- Conversión empírica a eCO₂ estimado con una referencia explícita; conserva ADC y milivoltios.
- Chat local con contexto de la visita y exportación separada de la conversación.

## Estado de distribución

Probado en macOS. El lanzador `ItAI.app` necesita el código y un entorno Python al lado: no es un ejecutable autónomo. **Este repositorio todavía no incluye un instalador Windows ni una validación en el HP.** Publicar el código no equivale a disponer de una aplicación Windows instalada.

## Instalación desde el código

Instala Python (la versión usada en el Mac de desarrollo fue 3.14). Dentro de la carpeta del proyecto, en macOS:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python desktop.py
```

En Windows, con Python instalado, estos son los comandos de preparación; su funcionamiento debe comprobarse en el equipo de destino:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe desktop.py
```

El entorno se crea en cada computador: no copiar `.venv` desde otro sistema. Para conversar, instala Ollama y descarga un modelo local compatible con tu equipo. El modelo utilizado en la prueba local fue `llama3:latest`. Mantén Ollama abierto y selecciona el modelo en Preparar equipo. La adquisición y los cálculos no necesitan Ollama.

## Configuración antes de conectar

Las claves del equipo en uso fueron retiradas de esta copia. `CAMBIAR_WIFI_2026` y `CONFIGURAR_CLAVE_API` son marcadores, no credenciales reales.

Para un equipo nuevo, configura `PASSWORD` y `API_KEY` en `firmware/ItAI_Local/ItAI_Local.ino` antes de compilar. En Python, establece la variable de entorno `ITAI_KEY` con esa misma clave API. Conecta el computador al Wi-Fi configurado en la placa. El texto de ayuda de la interfaz contiene el marcador de contraseña y debe adaptarse a tu despliegue.

Para una placa ya configurada, conserva su firmware y utiliza su contraseña Wi-Fi y clave API existentes de forma privada. No subas esas claves a GitHub.

Con el mismo sensor y circuito puedes trasladar por un medio privado `mq_reference.json` y las carpetas de referencia. No reutilizar ese perfil como calibración de otro sensor o circuito.

## Datos y límites

Este repositorio no incluye lecturas de hogares, perfiles reales de referencia, conversaciones, registros ni el paper adjunto. `.gitignore` ayuda a mantener esos archivos fuera del repositorio.

El MQ135 es multigás: sus ppm son una estimación empírica, no una medición selectiva validada de CO₂. Una visita breve no certifica habitabilidad. La inferencia describe tendencias dentro de la visita, no diferencias entre hogares independientes. Las respuestas libres del LLM requieren revisión frente a los resultados calculados.

Consulta [la guía de uso](LEEME.md) y [la verificación realizada](VERIFICACION.md).

## Pruebas

```sh
QT_QPA_PLATFORM=offscreen .venv/bin/python -m unittest discover -s . -p 'test_*.py' -v
```

En PowerShell, establece primero `$env:QT_QPA_PLATFORM="offscreen"` y ejecuta el equivalente con `.\.venv\Scripts\python.exe`.
