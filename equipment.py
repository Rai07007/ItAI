"""Local equipment transfer. Never include this configuration in public packages."""
import json
import math
import shutil
import uuid
from pathlib import Path
from mq135 import A,B,validate_circuit,convert
from runtime_paths import DATA_ROOT

CONFIG = DATA_ROOT/'equipment.json'

def read_config():
    try:return json.loads(CONFIG.read_text(encoding='utf-8'))
    except (OSError,ValueError):return {}

def validate_transfer(payload):
    if not isinstance(payload,dict) or payload.get('format')!='itai-equipment-v1':
        raise ValueError('El archivo no es una configuración de equipo ItAI.')
    key=payload.get('api_key')
    if not isinstance(key,str) or not 1<=len(key)<=200 or any(ord(c)<33 or ord(c)>126 for c in key):
        raise ValueError('La clave de conexión no es válida.')
    p=payload.get('mq_profile')
    if p is not None:
        if not isinstance(p,dict):raise ValueError('Referencia inválida.')
        validate_circuit(p)
        for field in ('r0_over_rl','a','baseline_pin_mv','baseline_rs_over_rl','reference_ppm'):
            v=p.get(field)
            if isinstance(v,bool) or not isinstance(v,(float,int)) or not math.isfinite(v) or v<=0:
                raise ValueError('Referencia inválida: '+field)
        if p.get('a')!=A or p.get('b')!=B or p.get('reference_kind') not in ('assumed','measured'):
            raise ValueError('Modelo de referencia incompatible.')
        for field in ('id','created_utc','reference_note'):
            if not isinstance(p.get(field),str) or not p[field]:raise ValueError('Falta '+field)
        if not 100<=p['reference_ppm']<=5000 or not p.get('conditioned'):
            raise ValueError('Referencia sin preparación válida.')
        # For an even sample count, median(f(voltage)) need not equal f(median(voltage)).
        # Calibration uses the median resistance ratio stored in the profile.
        value=p['a']*(p['baseline_rs_over_rl']/p['r0_over_rl'])**p['b']
        if value is None or not math.isclose(value,p['reference_ppm'],rel_tol=1e-6):
            raise ValueError('La referencia no reproduce su concentración declarada.')
    settings=payload.get('circuit_settings') or {}
    if not isinstance(settings,dict):raise ValueError('Configuración eléctrica inválida.')
    if settings:validate_circuit(settings)
    return {'format':'itai-equipment-v1','api_key':key,'mq_profile':p,'circuit_settings':settings}

def import_transfer(path, root=DATA_ROOT):
    path=Path(path)
    if path.stat().st_size>100000:raise ValueError('El archivo es demasiado grande.')
    data=validate_transfer(json.loads(path.read_text(encoding='utf-8')))
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    # Keep a full backup before replacing any existing settings.
    targets={'equipment.json':{'api_key':data['api_key']}}
    if data['mq_profile'] is not None:targets['mq_reference.json']=data['mq_profile']
    if data['circuit_settings']:targets['desktop_settings.json']=data['circuit_settings']
    backup=root/'equipment-backups'/uuid.uuid4().hex
    staged=[];previous={}
    try:
        for name,value in targets.items():
            target=root/name;previous[name]=target.read_bytes() if target.exists() else None
            if target.exists():backup.mkdir(parents=True,exist_ok=True);shutil.copy2(target,backup/name)
            temp=root/(name+'.importing');temp.write_text(json.dumps(value,ensure_ascii=False,allow_nan=False,indent=2),encoding='utf-8');staged.append((temp,target))
        for temp,target in staged:temp.replace(target)
    except Exception:
        for name,content in previous.items():
            target=root/name
            if content is not None:target.write_bytes(content)
            elif target.exists():target.unlink()
        raise
    finally:
        for temp,_ in staged:
            if temp.exists():temp.unlink()
    return data
