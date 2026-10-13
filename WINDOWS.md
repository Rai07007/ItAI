# ItAI en Windows

Destino: Windows 10/11 de 64 bits (Intel/AMD). El instalador incluye Python, Qt y las bibliotecas estadísticas. No incluye Ollama, modelos, claves ni datos de hogares. No requiere derechos de administrador: se instala para el usuario actual.

## Instalar y abrir

1. Descarga el artefacto **ItAI-Windows-x64** de una ejecución correcta de **Construir ItAI para Windows**, en la pestaña Actions del repositorio.
2. Extrae el ZIP y abre **ItAI-Setup-Windows-x64.exe**.
3. Completa la instalación y abre **ItAI** desde el escritorio o el menú Inicio.

La aplicación no tiene firma comercial de editor. No desactives el antivirus para instalarla. Si Windows bloquea la descarga o el inicio, conserva el mensaje para revisar el motivo antes de continuar.

## Usar tu mismo circuito

1. Copia por USB el archivo privado `ItAI-equipo-privado.json` preparado en el Mac.
2. En la app, abre **Preparar equipo → Importar equipo desde el Mac**.
3. Confirma que usas el mismo sensor, ESP32 y divisor de voltaje.
4. Conecta Windows al Wi-Fi **ItAI-Local**, con la contraseña ya configurada en tu placa.
5. Pulsa **Comprobar conexión**. Después inicia una visita de 30 minutos.

No subas el archivo privado a GitHub: incluye la clave de conexión y la referencia real. No es necesario volver a cargar el firmware por cambiar de computador. La referencia importada mantiene su carácter de medida o supuesta; importarla no valida su precisión. Los cambios de configuración conservan una copia anterior en `equipment-backups`.

Termina y descarga la sesión en el Mac antes de pasar al HP. Usa un solo computador para controlar la misma ESP32. Si hay una sesión pendiente, traslada también su carpeta de medición y usa Recuperar; no reinicies la placa para resolverlo.

## Dónde se guardan las mediciones

En `%LOCALAPPDATA%\ItAI`. Puedes abrirla desde **Preparar equipo → Abrir carpeta de datos y respaldos**. Contiene `mediciones`, `referencias`, configuración y registros. Actualizar o desinstalar la app no debe eliminar esa carpeta. Respalda sus contenidos antes de cambiar de computador.

## Chat opcional con Ollama

Para medir, analizar y graficar no necesitas instalar Ollama. Para conversar, instala Ollama desde su sitio oficial, descarga un modelo local adecuado para la RAM del HP y mantén Ollama abierto. Escribe el nombre del modelo en Preparar equipo; `auto` elige uno instalado. La aplicación usa únicamente el servidor local en `127.0.0.1:11434`. La descarga inicial requiere internet; el uso posterior no.

Para el HP con i3-7020U y 4 GB de RAM, comienza con **qwen2.5:0.5b**. Su descarga es de aproximadamente 398 MB; eso no equivale al consumo total de RAM. La viabilidad debe comprobarse en el HP con la app y Windows abiertos.

1. Instala y abre Ollama desde https://ollama.com/download/windows (requiere Windows 10 22H2 o posterior).
2. Con internet, abre PowerShell y ejecuta `ollama pull qwen2.5:0.5b` una sola vez.
3. En ItAI abre Preparar equipo y pulsa **Usar Ollama en un equipo de 4 GB**.
4. En este perfil, la app usa CPU con dos hilos, contexto de 4096 tokens, hasta 256 tokens de respuesta, contexto resumido y el último intercambio. Serializa sus consultas y pide liberar el modelo al terminar. Esto reduce memoria a costa de respuestas menos detalladas y recargas del modelo.
5. Cierra aplicaciones que no uses. Completa una prueba de 30 minutos con Wi-Fi del ESP32 y realiza preguntas con internet desconectado; verifica lecturas recibidas y calidad de las respuestas.

El modelo pequeño puede equivocarse o seguir peor las instrucciones. Las cifras y recomendaciones del informe siguen calculándose en Python; el chat no valida resultados. No se ha medido todavía su RAM o velocidad en el HP. Fuentes: https://ollama.com/library/qwen2.5:0.5b y https://docs.ollama.com/faq.

## Qué verifica la construcción automática

Pruebas del análisis y de recuperación con sensores simulados; importación de configuración; inicio del ejecutable; escritura SQLite; regresión HAC; exportación de gráficos; caracteres españoles en rutas; instalación y desinstalación. Ninguna prueba automática sustituye un ensayo real de 30 minutos con el HP y el circuito.
