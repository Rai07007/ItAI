"""Visit-specific evidence and actionable plans, computed without a language model."""
import math
import numpy as np

SOURCES={
    'epa_moisture':{'title':'EPA · Prevención de humedad y moho','url':'https://www.epa.gov/mold/mold-course-chapter-9'},
    'who_housing':{'title':'OMS · Vivienda y salud','url':'https://www.who.int/publications/i/item/9789241550376'},
    'mq135':{'title':'Winsen · MQ135','url':'https://www.winsen-sensor.com/manual/mq135.html'}
}
EVENTS={
    'cold':('temperature','Temperatura menor de 18 °C',lambda x:x<18),
    'very_cold':('temperature','Temperatura menor de 16 °C',lambda x:x<16),
    'warm':('temperature','Temperatura mayor de 26 °C',lambda x:x>26),
    'very_warm':('temperature','Temperatura mayor de 32 °C',lambda x:x>32),
    'dry':('humidity','Humedad menor de 30 %',lambda x:x<30),
    'humid':('humidity','Humedad mayor de 60 %',lambda x:x>60),
    'very_humid':('humidity','Humedad mayor de 70 %',lambda x:x>70),
    'gas_warning':('eco2','Modelo eCO₂ desde 1000 ppm estimados',lambda x:x>=1000),
    'gas_high':('eco2','Modelo eCO₂ sobre 2000 ppm estimados',lambda x:x>2000),
}

def weighted_events(times_ms,values,condition,period_ms=2500):
    times=np.asarray(times_ms,float);values=np.asarray(values,float)
    valid=np.isfinite(values);mask=valid & condition(values)
    # Each value represents at most its preceding nominal sampling window.
    # Long gaps are never filled. Duration is an explicit approximation, not observed continuity.
    delta=np.diff(np.r_[0.,times]);weights=np.clip(delta,0,period_ms)/1000
    episodes=[];run=[]
    def finish():
        if not run:return
        ids=np.array(run);episodes.append({'start_min':float(times[ids[0]]/60000),'end_min':float(times[ids[-1]]/60000),
            'n':len(run),'observed_span_min':float((times[ids[-1]]-times[ids[0]])/60000),
            'estimated_minutes':float(weights[ids].sum()/60),'min':float(values[ids].min()),'max':float(values[ids].max())})
    for i,selected in enumerate(mask):
        if run and (not selected or times[i]-times[run[-1]]>period_ms*1.12):finish();run=[]
        if selected:run.append(i)
    finish()
    covered=float(weights[valid].sum()/60);outside=float(weights[mask].sum()/60)
    return {'valid_n':int(valid.sum()),'outside_n':int(mask.sum()),'percent_valid_readings':float(100*mask.sum()/valid.sum()) if valid.any() else None,
        'estimated_minutes':outside,'covered_valid_minutes':covered,'episode_count':len(episodes),
        'longest_episode_minutes':max((e['estimated_minutes'] for e in episodes),default=0.),'episodes':episodes}

def build_insights(df,result):
    period=int(result['metadata'].get('period_ms',2500));times=df.elapsed_ms.to_numpy(float)
    events={}
    for name,(key,title,condition) in EVENTS.items():events[name]={'title':title,'variable':key,**weighted_events(times,df[key+'_analysis'],condition,period)}
    details={}
    for key in result['variables']:
        s=df[key+'_analysis'];valid=s.dropna();first=s[df.minutes<=5].dropna();last=s[df.minutes>df.minutes.max()-5].dropna()
        item={'n_valid':len(valid),'valid_percent_expected':100*len(valid)/max(1,result['quality']['expected']),
            'p05':float(valid.quantile(.05)) if len(valid) else None,'p25':float(valid.quantile(.25)) if len(valid) else None,
            'p75':float(valid.quantile(.75)) if len(valid) else None,'p95':float(valid.quantile(.95)) if len(valid) else None,
            'first_last':{'available':False,'reason':'Se requieren al menos 10 minutos y 60 lecturas válidas en cada ventana de 5 minutos.'}}
        if df.minutes.max()>=10 and len(first)>=60 and len(last)>=60:
            item['first_last']={'available':True,'first_mean':float(first.mean()),'last_mean':float(last.mean()),
                'difference':float(last.mean()-first.mean()),'first_n':len(first),'last_n':len(last),
                'last_window_start_min':float(df.minutes.max()-5),'note':'Comparación descriptiva; ventanas dentro de una misma visita, no grupos independientes.'}
        inf=result['variables'][key]['inference']
        if inf['available']:
            duration=inf['end_min']-inf['start_min']
            item['fitted_change']={'change':inf['slope_per_minute']*duration,'ci95':[v*duration for v in inf['slope_ci95']],
                'segment_minutes':duration,'p_holm':inf['p_slope_holm'],'note':'Cambio estimado por la recta en el tramo analizado; no es cambio causal.'}
        details[key]=item
    plan=[]
    def add(id,priority,title,basis,steps,verify,source=None):
        plan.append({'id':id,'priority':priority,'title':title,'evidence':basis,'actions':steps,'how_to_check':verify,'source_ids':source or []})
    def evidence(name):
        e=events[name]
        return f"{e['outside_n']} de {e['valid_n']} lecturas válidas ({e['percent_valid_readings']:.1f} %), aproximadamente {e['estimated_minutes']:.1f} min; {e['episode_count']} episodio(s)."
    for key in ('temperature','humidity','eco2'):
        coverage=details[key]['valid_percent_expected']
        if result['metadata'].get('analysis_stage')=='en curso':coverage=100*details[key]['n_valid']/max(1,result['quality']['received'])
        if coverage<80:
            add('quality_'+key,1 if key!='eco2' else 2,'Completar o verificar '+{'temperature':'temperatura','humidity':'humedad','eco2':'la referencia de gases'}[key],
                f"Cobertura válida: {coverage:.1f} % de las adquisiciones "+("recibidas hasta ahora." if result["metadata"].get("analysis_stage")=="en curso" else "previstas."),
                ['Revisar lecturas ausentes, conexión y banderas antes de interpretar una media.',
                 'Si es el MQ135, comprobar alimentación, divisor, acondicionamiento y el origen de la referencia; no rellenar ppm faltantes.'] if key=='eco2' else
                ['Comprobar conexión del DHT11, ubicación y valores fuera de especificación.','Repetir la visita completa si no hay datos suficientes para el objetivo.'],
                'Confirmar la tasa de lecturas válidas en una nueva captura, sin sustituir las ausentes por ceros.')
    if events['humid']['outside_n']:
        add('moisture',1 if events['very_humid']['outside_n'] else 2,'Buscar y reducir fuentes de humedad',evidence('humid'),
            ['Inspeccionar filtraciones visibles y condensación en ventanas o paredes; registrar dónde aparecen.',
             'Revisar extracción hacia el exterior al cocinar o ducharse. Ventilar si el aire exterior ayuda a secar; si es cálido y húmedo puede no ayudar.',
             'Si la humedad persiste, evaluar reparación de filtraciones y opciones de deshumectación con apoyo técnico.'],
            'Repetir en el mismo lugar y comparar tiempo sobre 60 %, anotando clima exterior y actividades. No afirmar moho solo por la humedad.',['epa_moisture'])
    if events['cold']['outside_n']:
        add('cold',1 if events['very_cold']['outside_n'] else 2,'Revisar pérdidas de calor',evidence('cold'),
            ['Observar corrientes por puertas y ventanas y registrar las zonas más frías.',
             'Revisar aislación y opciones de calefacción segura; no obstruir ventilación necesaria ni salidas de combustión.'],
            'Repetir a una hora comparable, con la misma ubicación, y comparar el tiempo bajo 18 °C.',['who_housing'])
    if events['warm']['outside_n']:
        add('heat',1 if events['very_warm']['outside_n'] else 2,'Revisar ganancias de calor',evidence('warm'),
            ['Registrar sol directo, calefacción y actividades que aportan calor.','Evaluar sombreado y ventilación cuando el exterior sea más favorable, sin asumir que abrir ventanas siempre enfría.'],
            'Comparar una nueva visita bajo condiciones exteriores semejantes y registrar tiempo sobre 26 °C.',['who_housing'])
    if events['dry']['outside_n']:
        add('dry',2,'Confirmar si la sequedad es persistente',evidence('dry'),
            ['Verificar la humedad con otra referencia y repetir en distintos momentos.','Revisar el contexto de calefacción y ventilación antes de decidir una intervención.'],
            'Comprobar si se repiten valores bajo 30 %; no recomendar humidificación automática con una sola visita.')
    if events['gas_warning']['outside_n']:
        add('gas',2,'Contrastar los cambios del indicador de gases',evidence('gas_warning'),
            ['Anotar cocina, calefacción, limpieza, ocupación y apertura de ventanas durante los episodios, sin atribuirles una causa confirmada.',
             'Comparar con un instrumento selectivo de CO₂ y revisar el perfil MQ135 antes de concluir sobre ventilación.'],
            'Repetir con la misma referencia; conservar ADC y mV. Los umbrales se aplicaron a ppm estimados, no a CO₂ verificado.',['mq135'])
    if events['humid']['outside_n'] and events['cold']['outside_n']:
        add('combined',1,'Abordar frío y humedad juntos','La visita tuvo lecturas de frío y de humedad elevada; no se supone que siempre coincidieran.',
            ['Coordinar control de fuentes de humedad con mejoras térmicas. Evitar una recomendación genérica de ventilar continuamente si empeora el frío.'],
            'Comparar ambas variables después de la intervención, registrando condiciones exteriores.',['epa_moisture','who_housing'])
    add('repeat',3,'Dar seguimiento con condiciones comparables','Una visita no describe todo el día ni todas las estaciones.',
        ['Mantener o registrar ubicación, altura, ventanas, ocupación y actividades. Si el robot cambia de habitación, anotarlo.',
         'Conservar el mismo sensor y referencia entre visitas comparables; registrar cualquier cambio de circuito.'],
        'Comparar distribuciones y duración de episodios, no solo promedios. Para comparar grupos, usar hogares independientes.')
    plan.sort(key=lambda x:(x['priority'],x['id']))
    return {'events':events,'details':details,'action_plan':plan,'sources':SOURCES,
        'duration_method':'Duración aproximada: cada lectura válida representa como máximo sus 2,5 segundos precedentes. No se rellenan desconexiones; episodios se separan ante ausencias o saltos >2,8 s. No prueba exposición continua.',
        'priority_method':'Orden orientativo de revisión: calidad insuficiente (<80 % operativo), extremos del paper o frío/humedad combinados primero; otras excursiones después; seguimiento al final. No es una escala validada ni asignación de beneficios.'}
