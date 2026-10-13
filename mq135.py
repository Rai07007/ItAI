"""Traceable empirical eCO2 model. All voltages refer to the actual physical circuit.
No claim of selective CO2 sensing or manufacturer-validated CO2 calibration.
"""
from datetime import datetime, timezone
import hashlib
import json
import math
import statistics

A=116.6020
B=-2.7690
MODEL='MQ135 empirical power law 116.6020 * (Rs/R0)^-2.7690'

def validate_circuit(c):
    for name in ('circuit_v','divider_factor'):
        if not isinstance(c.get(name),(int,float)) or not math.isfinite(c[name]) or c[name]<=0:
            raise ValueError('Falta un valor positivo para '+name)
    if not 2.5<=c['circuit_v']<=5.5: raise ValueError('Verifica la alimentación del circuito sensor (V), no la referencia ADC.')
    if not 1<=c['divider_factor']<=20: raise ValueError('Divisor inválido. Factor = (R superior + R inferior) / R inferior.')
    rl=c.get('rl_kohm')
    if rl is not None and (not math.isfinite(rl) or rl<=0): raise ValueError('RL debe ser positiva, o dejarse vacía si se desconoce.')

def normalized_rs(mv,c):
    validate_circuit(c)
    if mv is None or not math.isfinite(mv): raise ValueError('Voltaje ausente')
    pin=mv/1000
    # Calibrated ADC input, ESP32 classic at 11 dB; avoid extrapolation at endpoints.
    if not .15<pin<3.10: raise ValueError('Voltaje fuera del intervalo útil configurado del ADC')
    ao=pin*c['divider_factor']
    if not 0<ao<c['circuit_v']*.99: raise ValueError('A0 está demasiado cerca o por encima de Vc; revisar divisor/alimentación')
    return c['circuit_v']/ao-1

def calibrate(rows,circuit,reference_ppm,reference_kind,reference_note,device_mac='',conditioned=False):
    validate_circuit(circuit)
    if not math.isfinite(reference_ppm) or not 100<=reference_ppm<=5000: raise ValueError('Referencia ppm fuera del intervalo de configuración 100–5000')
    if reference_kind not in ('measured','assumed'):raise ValueError('Indica si la referencia fue medida o supuesta')
    if not reference_note.strip():raise ValueError('Describe la referencia y las condiciones de calibración')
    if not conditioned:raise ValueError('Falta confirmar el acondicionamiento del sensor')
    seen=set(); ratios=[]; pins=[]
    for r in rows:
        if r['sequence'] in seen:continue
        seen.add(r['sequence'])
        if r['flags'] & (16|32) or not r['flags'] & 4:continue
        try: value=normalized_rs(r.get('mq_mv'),circuit)
        except ValueError:continue
        ratios.append(value);pins.append(r['mq_mv'])
    if len(ratios)<20:raise ValueError('Se necesitan al menos 20 lecturas válidas, sin arranque ni saturación')
    cv=statistics.stdev(ratios)/statistics.mean(ratios)
    if cv>.05:raise ValueError(f'La referencia varió demasiado (CV={cv:.1%}); espera estabilidad y repite')
    baseline=statistics.median(ratios)
    r0=baseline / (reference_ppm/A)**(1/B)
    result={**circuit,'r0_over_rl':r0,'a':A,'b':B,'model':MODEL,'reference_ppm':reference_ppm,
        'reference_kind':reference_kind,'reference_note':reference_note,'conditioned':True,'device_mac':device_mac,
        'created_utc':datetime.now(timezone.utc).isoformat(),'baseline_n':len(ratios),'baseline_cv':cv,
        'baseline_pin_mv':statistics.median(pins),'baseline_rs_over_rl':baseline,
        'limitation':'Una referencia supuesta solo normaliza el modelo. Incluso con referencia medida, el ajuste de un punto no valida selectividad ni exactitud.'}
    result['id']=hashlib.sha256(json.dumps(result,sort_keys=True).encode()).hexdigest()[:16]
    return result

def convert(mv,profile,flags=4):
    if not profile:return {'eco2':None,'reason':'Falta preparar la referencia del MQ135'}
    if not flags&4:return {'eco2':None,'reason':'Sin adquisición MQ135'}
    if flags&32:return {'eco2':None,'reason':'ADC próximo a saturación'}
    if flags&16:return {'eco2':None,'reason':'Sensor en arranque'}
    if flags&64 and not profile.get('conditioned'):return {'eco2':None,'reason':'Acondicionamiento no declarado'}
    try:
        ratio=normalized_rs(mv,profile)
        r0=profile['r0_over_rl'];a=profile['a'];b=profile['b']
        if not all(math.isfinite(x) for x in (r0,a,b)) or r0<=0 or a<=0 or b>=0:raise ValueError('Perfil de conversión inválido')
        ppm=a*(ratio/r0)**b
        if not math.isfinite(ppm) or ppm<=0:raise ValueError('Resultado no finito')
        # Numerical model extrapolation is flagged, never clamped into the safe band.
        return {'eco2':ppm,'reason':'Estimación con referencia '+('medida' if profile['reference_kind']=='measured' else 'supuesta'),
            'rs_over_rl':ratio,'rs_kohm':ratio*profile['rl_kohm'] if profile.get('rl_kohm') else None,
            'ao_v':mv/1000*profile['divider_factor'],'extrapolated':not 100<=ppm<=5000}
    except (ValueError,KeyError,OverflowError,TypeError,ZeroDivisionError) as e:
        return {'eco2':None,'reason':str(e)}

def enrich(rows,profile):
    return [{**r,**convert(r.get('mq_mv'),profile,r.get('flags',0))} for r in rows]
