# Verificación de la aplicación actualizada

- 29 pruebas automatizadas aprobadas: límites del paper, inferencia con pendiente conocida, datos ausentes, series constantes, banderas, persistencia/duplicados/conflictos, 720 lecturas recuperadas tras una desconexión simulada, conversión MQ135, referencia insuficiente/inestable, conservación de lecturas pendientes, episodios con huecos, duración de 30 minutos, recomendaciones basadas en datos elegibles, ventanas temporales, contexto del chat, roles permitidos, historial limitado, selección local finalización del flujo de respuesta y aislamiento de conversaciones al cambiar de visita en la interfaz.
- Ollama `llama3:latest` respondió a una pregunta contextual con estadísticas simuladas a través del endpoint local de conversación. La primera prueba tardó aproximadamente 46 segundos; depende de la carga y del modelo.
- Se revisaron imágenes renderizadas de la propia app Qt con datos simulados: Plan de acción, Estadística y chat. Esto no equivale a una prueba física de sensores.
- La captura nativa se probó con un dispositivo simulado, incluyendo reconexión; el análisis y el chat se ejecutan independientemente del trabajo de captura.
- La conversión a ppm sigue siendo empírica. Probar la fórmula no valida la precisión del sensor, la referencia ni el circuito real.
- No se modificaron las lecturas ni los perfiles reales guardados durante esta actualización. Sigue pendiente verificar en campo la estabilidad eléctrica y una sesión completa de 30 minutos.
- La versión de firmware previamente compilada fue cargada por el usuario. Esta actualización se limita a Python y no requiere volver a cargar el ESP32.
