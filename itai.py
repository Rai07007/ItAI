#!/usr/bin/env python3
"""ItAI local collector: durable SQLite, reconnect/replay, local dashboard and Ollama."""
import argparse
from datetime import datetime, timezone
import hashlib
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
import urllib.parse
import urllib.request
import urllib.error
import uuid
import webbrowser
from analysis import report, classify

ROOT=Path(__file__).resolve().parent
FIELDS=['sequence','elapsed_ms','uptime_ms','temperature','humidity','mq_adc','mq_mv','mq_min','mq_max','flags']

def utc(): return datetime.now(timezone.utc).isoformat()
def atomic_json(path,value):
    tmp=Path(str(path)+'.tmp');tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False));tmp.replace(path)

def request(base,path,key='',data=None,timeout=5):
    body=urllib.parse.urlencode(data).encode() if data is not None else None
    req=urllib.request.Request(base+path,data=body,headers={'X-ItAI-Key':key})
    with urllib.request.urlopen(req,timeout=timeout) as r: return json.load(r)

def connect_db(folder):
    db=sqlite3.connect(folder/'lecturas.sqlite')
    db.execute('PRAGMA journal_mode=WAL'); db.execute('PRAGMA synchronous=FULL')
    db.execute('CREATE TABLE IF NOT EXISTS samples (sequence INTEGER PRIMARY KEY, payload TEXT NOT NULL, received_utc TEXT NOT NULL)')
    return db

def save_batch(db,batch):
    for row in batch:
        if any(k not in row for k in FIELDS): raise ValueError('Paquete incompleto')
        if not isinstance(row['sequence'],int) or row['sequence']<1: raise ValueError('Secuencia inválida')
        for key in FIELDS:
            value=row[key]
            if value is None and key in ('temperature','humidity'): continue
            if not isinstance(value,(int,float)) or not math.isfinite(value): raise ValueError('Número inválido: '+key)
        payload=json.dumps(row,sort_keys=True,allow_nan=False)
        old=db.execute('SELECT payload FROM samples WHERE sequence=?',(row['sequence'],)).fetchone()
        if old and old[0]!=payload: raise ValueError('Misma secuencia con distinto contenido: captura detenida')
        db.execute('INSERT OR IGNORE INTO samples VALUES (?,?,?)',(row['sequence'],payload,utc()))
    db.commit()

def read_rows(db): return [json.loads(r[0]) for r in db.execute('SELECT payload FROM samples ORDER BY sequence')]

OPENINGS = [
    'Ya tenemos una mirada de cómo estuvo el ambiente durante esta visita. Vamos por partes.',
    'Te cuento lo que pudimos observar y qué conviene revisar primero.',
    'Terminamos la visita. Estos datos nos ayudan a decidir por dónde empezar, sin sacar conclusiones apresuradas.'
]
CLOSINGS = [
    'No hace falta resolver todo de una vez: conviene empezar por lo observado y comprobar si se repite.',
    'Podemos usar esta visita como punto de partida y volver a medir después de hacer cambios.',
    'Lo importante ahora es contrastar estas observaciones con lo que ocurre habitualmente en la casa.'
]

def llm_catalogue(result):
    from analysis import fmt
    facts={}
    for key,label,unit in [('temperature','temperatura','°C'),('humidity','humedad','%'),('eco2','indicador estimado de gases','ppm estimados')]:
        v=result['variables'].get(key)
        if not v or not v['raw_n']:
            facts[key]='En esta visita no tuvimos datos suficientes para interpretar '+label+'. Conviene revisar esa medición antes de sacar conclusiones.'
            continue
        facts[key]=f"La {label} estuvo entre {v['min']:.1f} y {v['max']:.1f} {unit}, con un promedio de {v['mean']:.1f} {unit}. " if key!='eco2' else f"El indicador de gases estuvo entre {v['min']:.0f} y {v['max']:.0f} ppm estimados. "
        if key=='humidity':
            facts[key]+= ('Hubo lecturas por encima del intervalo óptimo del estudio. Un primer paso es revisar fuentes de humedad y observar si esto se repite; estos datos por sí solos no confirman moho.' if v['max']>60 else 'Esto describe solo el rato que medimos, no necesariamente cómo está la casa todo el día.')
        elif key=='temperature':
            facts[key]+=('Hubo lecturas fuera del intervalo aceptable del estudio; vale la pena revisar en qué momentos ocurrió.' if v['min']<18 or v['max']>26 else 'Las lecturas registradas quedaron dentro del intervalo aceptable del estudio.')
        else:
            prof=result.get('eco2',{}).get('profile') or {}
            facts[key]+=('La referencia usada fue supuesta, no medida. ' if prof.get('reference_kind')=='assumed' else 'Usamos una referencia registrada para ajustar el modelo. ')
            facts[key]+='Sirve para seguir cambios, pero no equivale a medir CO₂ de forma selectiva ni a certificar que el aire sea seguro.'
        inf=v['inference']
        if inf['available']:
            if inf['p_slope_holm']<.05:
                direction='aumentar' if inf['slope_per_minute']>0 else 'disminuir'
                facts[key]+=f' El análisis encontró una tendencia a {direction} durante el tramo evaluado; no permite atribuirle una causa.'
            else:facts[key]+=' El análisis no encontró evidencia suficiente de una tendencia lineal; eso no demuestra que no haya cambios.'
    insights=result.get('insights',{})
    notable=[e for e in insights.get('events',{}).values() if e['outside_n'] and e['variable']!='eco2']
    if notable:
        facts['episodes']='Al mirar los episodios, observamos: '+'; '.join(f"{e['title'].lower()}, aproximadamente {e['estimated_minutes']:.1f} minutos y {e['episode_count']} episodio(s)" for e in notable)+'. Son duraciones aproximadas; los umbrales se superponen y no se suman.'
    plan=insights.get('action_plan',[])[:2]
    if plan:
        facts['next_steps']='Para empezar, propongo estos pasos: '+' '.join(item['title']+'. '+item['actions'][0]+' Para comprobarlo: '+item['how_to_check'] for item in plan)
    facts['scope']='Esta es una observación de la visita, no un diagnóstico completo de la vivienda. No evaluamos moho ni fisuras con este programa. Las cifras detalladas y su incertidumbre están en el análisis estadístico.'
    return facts

def validate_llm_order(response,catalogue):
    content=json.loads(response)
    order=content.get('order')
    if not isinstance(order,list) or any(not isinstance(k,str) for k in order):raise ValueError('Respuesta LLM inválida')
    if not order or not set(order).issubset(catalogue):raise ValueError('El LLM inventó identificadores; no se publica su respuesta')
    # Include every verified finding and limitation even if the model omits it.
    return list(dict.fromkeys(order + list(catalogue)))

def ollama_report(result,folder,model):
    # Fixed loopback endpoint: household information is never sent to a cloud LLM.
    base='http://127.0.0.1:11434'
    try:
        tags=request(base,'/api/tags',timeout=3)
        models=[m['name'] for m in tags.get('models',[]) if not m.get('remote_host') and 'cloud' not in m['name'].lower()]
        if model=='auto': model=models[0] if models else None
        if not model or model not in models: raise ValueError('No hay modelo local seleccionado/instalado. Use ollama list y --model NOMBRE.')
        # The LLM orders verified sentences. It cannot invent numbers, units or diagnoses.
        catalogue = llm_catalogue(result)
        prompt=('Eres la voz de ItAI: cercana, respetuosa, clara, sin culpar ni alarmar a las familias. '
            'Prioriza problemas observados, después incertidumbre y revisiones. Devuelve únicamente JSON '
            '{"opening":"una apertura permitida","order":["identificador",...],"closing":"un cierre permitido"}. Usa cada identificador exactamente una vez. Aperturas permitidas: '+json.dumps(OPENINGS,ensure_ascii=False)+' Cierres permitidos: '+json.dumps(CLOSINGS,ensure_ascii=False)+'. '
            'No redactes ni alteres las frases. CATÁLOGO:\n'+json.dumps(catalogue,ensure_ascii=False))
        schema={'type':'object','properties':{'opening':{'type':'string','enum':OPENINGS},'closing':{'type':'string','enum':CLOSINGS},'order':{'type':'array','items':{'type':'string','enum':list(catalogue)},'minItems':1,'maxItems':len(catalogue)}},'required':['opening','order','closing'],'additionalProperties':False}
        body=json.dumps({'model':model,'prompt':prompt,'format':schema,'stream':False,
            'options':{'temperature':0,'num_predict':500}}).encode()
        req=urllib.request.Request(base+'/api/generate',data=body,headers={'Content-Type':'application/json'})
        with urllib.request.urlopen(req,timeout=180) as r: response=json.load(r)
        ordered=validate_llm_order(response['response'],catalogue)
        content=json.loads(response['response'])
        opening=content.get('opening') if content.get('opening') in OPENINGS else OPENINGS[0]
        closing=content.get('closing') if content.get('closing') in CLOSINGS else CLOSINGS[0]
        rendered=opening+'\n\n'+'\n\n'.join(catalogue[k] for k in ordered)+'\n\n'+closing
        (folder/'interpretacion_llm.txt').write_text(('DATOS SIMULADOS · NO ES UNA MEDICIÓN\n' if result['metadata'].get('demo') else '')+'SÍNTESIS LOCAL · REQUIERE REVISIÓN HUMANA\nModelo: '+model+
            '\nEl LLM organiza frases verificadas; las cifras y conclusiones proceden de Python.\n\n'+rendered)
        atomic_json(folder/'llm_auditoria.json',{'model':model,'created_utc':utc(),'prompt':prompt,'response':response,'validated_order':ordered})
        print('Interpretación LLM guardada.')
    except Exception as exc:
        (folder/'interpretacion_llm.txt').write_text('LLM NO DISPONIBLE. El análisis numérico y los gráficos están completos.\n'+str(exc))
        print('LLM no disponible; los datos y el informe se conservaron:',exc)

def dashboard(folder,port):
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self,*a,**kw): super().__init__(*a,directory=str(folder),**kw)
        def log_message(self,*a): pass
        def end_headers(self): self.send_header('Cache-Control','no-store');super().end_headers()
    server=ThreadingHTTPServer(('127.0.0.1',port),Handler)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    url=f'http://127.0.0.1:{port}/panel.html'; print('Panel:',url)
    webbrowser.open(url)
    return server

PANEL='''<!doctype html><html lang="es"><meta charset="utf-8"><title>ItAI · Medición local</title>
<style>body{font:17px system-ui;background:#f3f7f9;color:#183441;max-width:1100px;margin:40px auto;padding:20px}h1{color:#127589}section{background:white;padding:24px;border-radius:16px;margin:20px 0}.cards{display:flex;gap:30px;flex-wrap:wrap}.value{font-size:32px}progress{width:100%;height:24px}canvas{width:100%;height:200px}a{color:#087086}#error{color:#a45317}</style>
<h1>ItAI · Medición local</h1><p id="house"></p><section><p id="status">Esperando datos…</p><progress id="progress" max="720" value="0"></progress><p id="error"></p><p id="counts"></p><div class="cards" id="cards"></div></section><section><h2>Evolución durante la visita</h2><div id="plots"></div></section><p>MQ135: señal relativa, sin conversión a ppm. Las clasificaciones describen lecturas; no certifican seguridad del hogar.</p><p><a href="informe.html">Abrir informe final</a> · <a href="datos.csv">CSV final</a> · <a href="interpretacion_llm.txt">Borrador del LLM</a></p>
<script>const labels={temperature:'Temperatura (°C)',humidity:'Humedad (%)',mq_adc:'MQ135 (ADC relativo)'};for(const k in labels){const p=document.createElement('p');p.textContent=labels[k];document.querySelector('#plots').append(p);const c=document.createElement('canvas');c.id=k;c.width=1000;c.height=200;document.querySelector('#plots').append(c)}
function draw(k,rows){let c=document.getElementById(k),g=c.getContext('2d');g.clearRect(0,0,1000,200);let vals=rows.map(r=>r[k]).filter(v=>v!==null);if(!vals.length)return;let lo=Math.min(...vals),hi=Math.max(...vals);if(hi===lo){lo-=1;hi+=1}g.fillStyle='#456';g.font='14px system-ui';g.fillText(hi.toFixed(1),5,18);g.fillText(lo.toFixed(1),5,175);g.fillText('0 min',50,198);g.fillText('30 min',930,198);g.strokeStyle='#147d92';g.lineWidth=2;g.beginPath();let gap=true;for(const r of rows){if(r[k]===null){gap=true;continue}let x=55+r.elapsed_ms/1800000*920,y=175-(r[k]-lo)/(hi-lo)*150;if(gap)g.moveTo(x,y);else g.lineTo(x,y);gap=false}g.stroke()}
async function refresh(){try{let s=await(await fetch('estado.json')).json();document.getElementById('house').textContent=s.house+(s.demo?' · DATOS SIMULADOS':'');document.getElementById('status').textContent=s.status;document.getElementById('error').textContent=s.error||'';document.getElementById('progress').max=s.target;document.getElementById('progress').value=s.count;document.getElementById('counts').textContent=s.count+' / '+s.target+' adquisiciones guardadas · última actualización '+s.updated;let root=document.getElementById('cards');root.replaceChildren();for(const k in labels){let d=document.createElement('div'),title=document.createElement('p'),v=document.createElement('div'),cl=document.createElement('p');title.textContent=labels[k];v.className='value';v.textContent=s.latest?.[k]??'—';cl.textContent=s.categories?.[k]||'';d.append(title,v,cl);root.append(d)}let rows=await(await fetch('series_vivas.json')).json();for(const k in labels)draw(k,rows)}catch(e){document.getElementById('error').textContent='Esperando actualización local…'}setTimeout(refresh,2000)}refresh();</script></html>'''

def update_panel(folder,meta,rows,status,error=''):
    latest=rows[-1] if rows else {}
    categories={k:classify(k,latest.get(k)) for k in ['temperature','humidity','mq_adc']}
    flags=latest.get('flags',0)
    if flags & 256: categories['temperature']+=' · fuera de especificación'
    if flags & 512: categories['humidity']+=' · fuera de especificación'
    if flags & 16: categories['mq_adc']+=' · en arranque'
    if flags & 64: categories['mq_adc']+=' · sin acondicionamiento declarado'
    if flags & 32: categories['mq_adc']+=' · lectura próxima al límite ADC'
    atomic_json(folder/'estado.json',{'house':meta['house'],'demo':meta.get('demo',False),'target':meta['target'],'count':len(rows),
        'status':status,'error':error,'updated':utc(),'latest':latest,
        'categories':categories})
    atomic_json(folder/'series_vivas.json',rows)

def finish(db,folder,meta,args):
    rows=read_rows(db)
    if not rows:
        update_panel(folder,meta,rows,'Sin lecturas; no se genera inferencia');return
    update_panel(folder,meta,rows,'Generando análisis y gráficos')
    result=report(rows,meta,folder)
    if args.model!='none': ollama_report(result,folder,args.model)
    else: (folder/'interpretacion_llm.txt').write_text('LLM desactivado para esta ejecución.')
    update_panel(folder,meta,rows,'Informe disponible · '+meta.get('completion','finalizada'))
    hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in folder.iterdir() if p.suffix in ('.csv','.json','.png','.html','.txt') and p.name!='sha256.json'}
    atomic_json(folder/'sha256.json',hashes)
    print('Informe:',folder/'informe.html')

def collect(args):
    if args.resume:
        folder=Path(args.resume).resolve();meta=json.loads((folder/'sesion.json').read_text())
    else:
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,40}',args.house): raise ValueError('Use código de hogar sin nombres ni direcciones, por ejemplo H001')
        sid=uuid.uuid4().hex;folder=ROOT/'mediciones'/(datetime.now().strftime('%Y%m%d-%H%M%S')+'_'+args.house+'_'+sid[:6]);folder.mkdir(parents=True)
        meta={'house':args.house,'session_id':sid,'created_utc':utc(),'target':720,'period_ms':2500,'duration_ms':1800000,
            'protocol_adaptation':'720 adquisiciones en 30 min; paper indica 1000. Cadencia del código original: 2500 ms.',
            'scope':'visita ambiental; visión y estructura no evaluadas','demo':False,'base':args.device}
        atomic_json(folder/'sesion.json',meta)
    (folder/'panel.html').write_text(PANEL)
    db=connect_db(folder);rows=read_rows(db);update_panel(folder,meta,rows,'Conectando al ESP32')
    server=dashboard(folder,args.port) if not args.no_browser else None
    base=meta['base']; key=args.key;cursor=rows[-1]['sequence'] if rows else 0
    print('Carpeta:',folder,'\nPara recuperar esta captura: --resume',folder)
    errors=0
    try:
        # Retry idempotent /start with persisted UUID if the response was lost.
        status=request(base,'/status',key)
        if status.get('protocol')!=2: raise ValueError('Versión de protocolo incompatible')
        if 'boot_id' in meta and status['boot_id']!=meta['boot_id']: raise ValueError('ESP32 reiniciado: no mezclar sesiones. Datos previos conservados.')
        if args.resume and (cursor or 'boot_id' in meta) and status['session_id']!=meta['session_id']: raise ValueError('La sesión del ESP32 cambió; no se sobrescribe.')
        if status['session_id']!=meta['session_id']:
            if status['active']: raise ValueError('Hay otra sesión activa; recupérela con --resume.')
            status=request(base,'/start',key,{'id':meta['session_id'],'seconds':1800})
        meta['boot_id']=status['boot_id'];meta['device_at_start']=status;atomic_json(folder/'sesion.json',meta)
        while True:
            try:
                status=request(base,'/status',key)
                if status['boot_id']!=meta['boot_id'] or status['session_id']!=meta['session_id']: raise ValueError('Dispositivo reiniciado o sesión reemplazada. Captura detenida para no mezclar datos.')
                query=urllib.parse.urlencode({'id':meta['session_id'],'after':cursor})
                batch=request(base,'/samples?'+query,key)
                if batch['boot_id']!=meta['boot_id'] or batch['session_id']!=meta['session_id']: raise ValueError('Identidad de lote distinta')
                expected=cursor+1
                for row in batch['samples']:
                    if row['sequence']!=expected: raise ValueError('Lote con secuencia no contigua')
                    expected+=1
                save_batch(db,batch['samples']);rows=read_rows(db)
                if rows:cursor=rows[-1]['sequence']
                errors=0;update_panel(folder,meta,rows,'Midiendo' if status['active'] else 'Recuperando últimas lecturas')
                if not status['active'] and cursor>=status['count']:
                    meta['completion']='completa' if cursor==meta['target'] else 'incompleta'
                    meta['device_final']=status;break
                time.sleep(.1 if cursor<status['count'] else 1)
            except (urllib.error.URLError,TimeoutError,OSError) as exc:
                errors+=1;update_panel(folder,meta,read_rows(db),'Sin conexión; recuperación pendiente',str(exc))
                if errors>=60: raise RuntimeError('Sin conexión durante aproximadamente 5 minutos. Reanude con --resume; el ESP32 conserva la sesión mientras siga encendido.')
                time.sleep(2)
    except KeyboardInterrupt:
        # Stop only this session, never an unrelated household measurement.
        try: request(base,'/stop',key,{'id':meta['session_id']})
        except Exception: pass
        meta['completion']='interrumpida por operador; puede haber lecturas pendientes de recuperar'
    except Exception as exc:
        meta['completion']='incompleta: '+str(exc);print(meta['completion'])
    finally:
        meta['last_saved_utc']=utc();atomic_json(folder/'sesion.json',meta)
        finish(db,folder,meta,args);db.close()
    if server:
        print('Panel abierto. Enter para cerrar (los archivos permanecen).')
        try: input()
        except (EOFError,KeyboardInterrupt): pass
        server.shutdown()

def demo(args):
    import numpy as np
    rng=np.random.default_rng(42);folder=ROOT/'demo';folder.mkdir(exist_ok=True)
    rows=[];noise=0
    for i in range(720):
        noise=.8*noise+rng.normal(0,.13)
        adc=round(1300+100*math.sin(i/150)+noise*60)
        rows.append({'sequence':i+1,'elapsed_ms':(i+1)*2500,'uptime_ms':1900000+(i+1)*2500,
            'temperature':round(19+.002*i+noise,1),'humidity':round(58+.025*i+noise*3,1),
            'mq_adc':adc,'mq_mv':round(adc*.8),'mq_min':adc-8,'mq_max':adc+8,'flags':7})
    meta={'house':'DEMO_NO_REAL','target':720,'demo':True,'completion':'demostración','created_utc':utc()}
    atomic_json(folder/'sesion.json',meta);(folder/'panel.html').write_text(PANEL)
    # Deterministic demo can be regenerated; measured folders are never overwritten.
    db=connect_db(folder);db.execute('DELETE FROM samples');db.commit();save_batch(db,rows)
    finish(db,folder,meta,args);db.close()
    if not args.no_browser:webbrowser.open((folder/'informe.html').as_uri())

def main():
    p=argparse.ArgumentParser(description='ItAI: captura de 30 minutos, estadística y LLM local')
    p.add_argument('command',choices=['collect','demo','analyze'])
    p.add_argument('--house',default='H001');p.add_argument('--device',default='http://192.168.4.1')
    p.add_argument('--key',default=os.environ.get('ITAI_KEY','CONFIGURAR_CLAVE_API'))
    p.add_argument('--resume',help='Carpeta existente para recuperar o analizar')
    p.add_argument('--model',default='auto',help='Modelo Ollama instalado, auto, o none')
    p.add_argument('--port',type=int,default=8765);p.add_argument('--no-browser',action='store_true')
    args=p.parse_args()
    if args.command=='demo':demo(args)
    elif args.command=='collect':collect(args)
    else:
        if not args.resume:p.error('analyze requiere --resume CARPETA')
        folder=Path(args.resume).resolve();meta=json.loads((folder/'sesion.json').read_text());db=connect_db(folder)
        finish(db,folder,meta,args);db.close()

if __name__=='__main__':main()
