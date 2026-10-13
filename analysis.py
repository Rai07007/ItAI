"""Reproducible environmental analysis. LLM never calculates the statistics."""
import json
import os
from pathlib import Path
import html
import numpy as np
import pandas as pd
import statsmodels.api as sm
from mq135 import enrich
from insights import build_insights
from statsmodels.stats.multitest import multipletests
from runtime_paths import DATA_ROOT
os.environ.setdefault('MPLCONFIGDIR', str(DATA_ROOT / '.mplconfig'))
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

LIMITATIONS = (
    'Resultados exploratorios de esta visita, no certificación de habitabilidad ni diagnóstico sanitario. '
    'Las lecturas sucesivas no son hogares independientes. La inferencia temporal usa errores HAC '
    'y supone una tendencia aproximadamente lineal y dependencia residual de corto alcance. '
    'Los intervalos no incluyen el error de calibración del sensor. '
    'Los ppm del MQ135 son estimaciones de un modelo empírico multigás, no una medición selectiva de CO₂. '
    'Humedad elevada no demuestra presencia de moho. Visión artificial, fisuras y moho: no evaluados.'
)
THRESHOLDS = {
    'temperature': 'Riesgo: T<16 o T>32; advertencia: 16≤T<18 o 26<T≤32; aceptable: 18≤T≤26 °C.',
    'humidity': 'Fuera del intervalo del paper: H<20 o H>90; resequedad: 20≤H<30; óptimo: 30≤H≤60; exceso: 60<H≤70; humedad elevada: 70<H≤90 %. DHT11: se marca además H>80 fuera de especificación conservadora.',
    'eco2': 'Solo estimación con calibración documentada: <1000 aceptable; 1000–2000 advertencia; >2000 riesgo. Sin calibración: no evaluable.'
}
LABELS = {'temperature': 'Temperatura (°C)', 'humidity': 'Humedad relativa (%)', 'mq_adc': 'MQ135 (ADC relativo)', 'eco2': 'eCO₂ estimado (ppm)'}

def classify(variable, value):
    if value is None or not np.isfinite(value): return 'sin dato'
    if variable == 'temperature':
        if value < 16: return 'riesgo por frío'
        if value > 32: return 'riesgo por calor'
        if value < 18 or value > 26: return 'advertencia'
        return 'aceptable'
    if variable == 'humidity':
        if value < 20 or value > 90: return 'fuera del intervalo del paper'
        if value < 30: return 'advertencia por resequedad'
        if value <= 60: return 'óptimo'
        if value <= 70: return 'advertencia por exceso'
        return 'humedad elevada'
    if variable == 'eco2':
        return 'aceptable' if value < 1000 else 'advertencia' if value <= 2000 else 'riesgo'
    return 'indicador relativo, sin umbral ppm'

def clean_frame(rows, profile=None):
    df = pd.DataFrame(enrich(rows,profile)).sort_values('sequence').drop_duplicates('sequence').copy()
    for key in ['temperature','humidity','mq_adc','elapsed_ms','flags']:
        df[key] = pd.to_numeric(df[key], errors='coerce')
    df['minutes'] = df.elapsed_ms / 60000
    df['temperature_analysis'] = df.temperature.where((df['flags'].astype(int) & (1|256)) == 1)
    df['humidity_analysis'] = df.humidity.where((df['flags'].astype(int) & (2|512)) == 2)
    # Unconditioned readings remain visible, but cannot support MQ inference.
    df['mq_adc_analysis'] = df.mq_adc.where((df['flags'].astype(int) & (4|16|32|64)) == 4)
    df['eco2_analysis']=pd.to_numeric(df.eco2,errors='coerce').where(~df.get('extrapolated',pd.Series(False,index=df.index)).fillna(False).astype(bool))
    if profile and profile.get('conditioned'):
        df['mq_adc_analysis']=df.mq_adc.where((df['flags'].astype(int)&(4|16|32))==4)
    return df

def longest_regular_run(times, values):
    """Keep actual 2.5s adjacency; do not concatenate across missing samples."""
    best=[]; run=[]
    for i in range(len(times)):
        if not np.isfinite(values[i]):
            if len(run)>len(best): best=run
            run=[]; continue
        if run and not 2.2 <= (times[i]-times[run[-1]])*60 <= 2.8:
            if len(run)>len(best): best=run
            run=[]
        run.append(i)
    return np.asarray(run if len(run)>len(best) else best, dtype=int)

def inference(times, values):
    times=np.asarray(times,float); values=np.asarray(values,float)
    ids=longest_regular_run(times,values)
    if len(ids)<60: return {'available':False,'reason':'Menos de 60 lecturas válidas consecutivas a cadencia regular.', 'n':len(ids)}
    x=times[ids]; y=values[ids]
    if np.ptp(y)<1e-10:
        return {'available':False,'reason':'Serie constante a la resolución del sensor; no se estima significación.', 'n':len(ids)}
    X=sm.add_constant(x-x.mean())
    fits={lag:sm.OLS(y,X).fit(cov_type='HAC',cov_kwds={'maxlags':min(lag,len(y)//4)},use_t=False) for lag in (12,24,48)}
    fit=fits[24]; ci=np.asarray(fit.conf_int(alpha=.05))
    return {'available':True,'n':len(ids),'start_min':float(x[0]),'end_min':float(x[-1]),
        'mean_fitted':float(fit.params[0]),'mean_ci95':ci[0].tolist(),
        'slope_per_minute':float(fit.params[1]),'slope_ci95':ci[1].tolist(),
        'p_slope':float(fit.pvalues[1]),'hac_lags':24,
        'sensitivity':{str(lag):{'slope_ci95':np.asarray(f.conf_int())[1].tolist(),'p':float(f.pvalues[1])} for lag,f in fits.items()}}

def summarize(rows, metadata):
    df=clean_frame(rows,metadata.get("mq_profile"))
    result={'metadata':metadata,'limitations':LIMITATIONS,'thresholds':THRESHOLDS,'variables':{},
        'eco2':{'available':bool(df.eco2.notna().any()),'profile':metadata.get('mq_profile'),'reason':'Estimación empírica; incertidumbre de calibración no incluida en IC.' if metadata.get('mq_profile') else 'Falta una referencia guardada del MQ135.'}}
    flags=df['flags'].astype(int)
    result['quality']={'received':len(df),'expected':metadata.get('target',720),
        'missing_expected':max(0,metadata.get('target',720)-len(df)),
        'mq_startup':int(((flags&16)!=0).sum()),'mq_unconditioned':int(((flags&64)!=0).sum()),
        'mq_rail':int(((flags&32)!=0).sum()),'dht_temperature_out_spec':int(((flags&256)!=0).sum()),
        'dht_humidity_out_spec':int(((flags&512)!=0).sum()),
        'median_interval_seconds':float(df.elapsed_ms.diff().median()/1000) if len(df)>1 else None}
    for key in LABELS:
        s=df[key].dropna(); a=df[key+'_analysis'].dropna()
        result['variables'][key]={'raw_n':len(s),'analysis_n':len(a),
            'mean':float(s.mean()) if len(s) else None,'median':float(s.median()) if len(s) else None,
            'sd':float(s.std()) if len(s)>1 else None,'min':float(s.min()) if len(s) else None,
            'max':float(s.max()) if len(s) else None,
            'categories_raw':s.map(lambda v:classify(key,v)).value_counts().to_dict(),
            'inference':inference(df.minutes,df[key+'_analysis'])}
    usable=[v['inference'] for v in result['variables'].values() if v['inference']['available']]
    if usable:
        adjusted=multipletests([v['p_slope'] for v in usable],method='holm')[1]
        for inf,p in zip(usable,adjusted): inf['p_slope_holm']=float(p)
    pairs=[]
    for i,a in enumerate(LABELS):
        for b in list(LABELS)[i+1:]:
            sub=df[[a+'_analysis',b+'_analysis']].dropna()
            valid=len(sub)>2 and (sub.nunique()>1).all()
            pairs.append({'a':a,'b':b,'n':len(sub),'pearson':float(sub.corr().iloc[0,1]) if valid else None,
                'spearman':float(sub.corr(method='spearman').iloc[0,1]) if valid else None})
    result['correlations_descriptive']=pairs
    # Transparent visit-specific fraction; NOT a fabricated overall vulnerability index.
    eligible=df[['temperature_analysis','humidity_analysis']].notna().all(axis=1)
    outside=(~df.temperature.between(18,26)) | (~df.humidity.between(30,60))
    result['environmental_fraction']={'name':'Porcentaje de lecturas válidas fuera de rango T/HR',
        'percent':float(100*outside[eligible].mean()) if eligible.any() else None,'n':int(eligible.sum()),
        'definition':'T fuera de [18,26] o HR fuera de [30,60], solo pares dentro de especificación. No es un índice validado de vulnerabilidad.'}
    result['insights']=build_insights(df,result)
    result['recommendations']=[item['title']+': '+item['actions'][0] for item in result['insights']['action_plan']]
    result['analysis_version']=3
    return df,result

def p_fmt(v): return '<1e-300 (límite numérico)' if v == 0 else fmt(v)

def fmt(v): return 'No evaluable' if v is None else f'{v:.4g}' if isinstance(v,(float,int)) else str(v)

def report(rows,metadata,folder):
    folder=Path(folder); folder.mkdir(parents=True,exist_ok=True)
    df,result=summarize(rows,metadata)
    export=df.copy(); export.insert(0,'house',metadata['house']); export.insert(1,'simulated',bool(metadata.get('demo',False)))
    export.to_csv(folder/'datos.csv',index=False)
    (folder/'analisis.json').write_text(json.dumps(result,indent=2,ensure_ascii=False,allow_nan=False), encoding='utf-8')
    plt.rcParams.update({'font.size':10,'axes.spines.top':False,'axes.spines.right':False})
    fig,axes=plt.subplots(len(LABELS),1,figsize=(11,11),sharex=True,layout='constrained')
    for ax,key in zip(axes,LABELS):
        ax.plot(df.minutes,df[key],color='#147d92',lw=1,label='Lectura registrada')
        invalid=df[key+'_analysis'].isna() & df[key].notna()
        ax.scatter(df.minutes[invalid],df[key][invalid],s=9,color='#b56b18',label='Excluida de inferencia')
        if key=='temperature':
            ax.axhspan(18,26,color='#29aa71',alpha=.10)
            for v in [16,18,26,32]: ax.axhline(v,color='#999',lw=.6,ls='--')
        elif key=='humidity':
            ax.axhspan(30,60,color='#29aa71',alpha=.10)
            for v in [20,30,60,70,90]: ax.axhline(v,color='#999',lw=.6,ls='--')
        elif key=='eco2':
            for v in [1000,2000]: ax.axhline(v,color='#b56b18',lw=.6,ls='--')
        ax.set_ylabel(LABELS[key]); ax.grid(alpha=.18); ax.legend(loc='upper right',fontsize=8)
    axes[-1].set_xlabel('Minutos desde inicio del hogar')
    fig.suptitle('ItAI · Evolución temporal · '+metadata['house']+(' · DATOS SIMULADOS' if metadata.get('demo') else ''))
    fig.savefig(folder/'series.png',dpi=150); plt.close(fig)
    fig,axes=plt.subplots(1,len(LABELS),figsize=(14,3.6),layout='constrained')
    for ax,key in zip(axes,LABELS):
        ax.hist(df[key].dropna(),bins=20,color='#147d92',alpha=.8); ax.set_xlabel(LABELS[key]);ax.set_ylabel('Lecturas')
    fig.suptitle('Distribuciones de lecturas registradas'+(' · DATOS SIMULADOS' if metadata.get('demo') else ''))
    fig.savefig(folder/'distribuciones.png',dpi=150);plt.close(fig)
    corr=df[[k+'_analysis' for k in LABELS]].corr(); fig,ax=plt.subplots(figsize=(6,5),layout='constrained')
    im=ax.imshow(corr,vmin=-1,vmax=1,cmap='RdBu_r'); fig.colorbar(im,ax=ax,label='r de Pearson (descriptivo)')
    ax.set_xticks(range(len(LABELS)),['T','HR','ADC','eCO₂']);ax.set_yticks(range(len(LABELS)),['T','HR','ADC','eCO₂'])
    for i in range(len(LABELS)):
        for j in range(len(LABELS)): ax.text(j,i,fmt(corr.iloc[i,j]) if np.isfinite(corr.iloc[i,j]) else 'N/E',ha='center',va='center')
    ax.set_title('Correlaciones descriptivas'+(' · DATOS SIMULADOS' if metadata.get('demo') else ''))
    fig.savefig(folder/'correlaciones.png',dpi=150);plt.close(fig)
    esc=html.escape
    body=f'<h1>ItAI · {esc(metadata["house"])}</h1>'
    if metadata.get('demo'): body+='<h2>DEMOSTRACIÓN · DATOS SIMULADOS · NO USAR COMO MEDICIÓN</h2>'
    body+=f'<p>{esc(LIMITATIONS)}</p><h2>Registro y calidad</h2><pre>{esc(json.dumps(result["quality"],indent=2,ensure_ascii=False))}</pre>'
    body+='<p>Protocolo adaptado: 30 minutos / 720 adquisiciones previstas, cada 2,5 s. El paper propone 1000; esta visita no cumple ese tamaño. Los valores inválidos no se reemplazan ni se interpolan.</p>'
    body+='<h2>Descriptivos de lecturas registradas</h2><table><tr><th>Variable</th><th>N bruto / inferencia elegible</th><th>Media</th><th>Mediana</th><th>DE</th><th>Mín–Máx</th></tr>'
    for key,v in result['variables'].items():
        body+=f'<tr><td>{LABELS[key]}</td><td>{v["raw_n"]} / {v["analysis_n"]}</td><td>{fmt(v["mean"])}</td><td>{fmt(v["median"])}</td><td>{fmt(v["sd"])}</td><td>{fmt(v["min"])}–{fmt(v["max"])}</td></tr>'
    body+='</table><h2>Inferencia temporal exploratoria</h2><p>Regresión lineal sobre el tramo válido consecutivo más largo (mínimo 60 lecturas). IC 95 % HAC, 24 rezagos (60 s), sensibilidad a 12/48. Prueba bilateral de pendiente cero, p ajustada por Holm entre variables disponibles. No es una comparación entre hogares ni prueba de causalidad.</p>'
    for key,v in result['variables'].items():
        inf=v['inference'];body+=f'<h3>{LABELS[key]}</h3>'
        if not inf['available']: body+=f'<p>{esc(inf["reason"])}</p>';continue
        body+=f'<p>n={inf["n"]}; tramo {inf["start_min"]:.2f}–{inf["end_min"]:.2f} min. Media ajustada: {fmt(inf["mean_fitted"])}; IC95 % {esc(str(inf["mean_ci95"]))}. Pendiente/min: {fmt(inf["slope_per_minute"])}; IC95 % {esc(str(inf["slope_ci95"]))}; p Holm={esc(p_fmt(inf["p_slope_holm"]))}.</p>'
        body+=f'<details><summary>Sensibilidad de la inferencia</summary><pre>{esc(json.dumps(inf["sensitivity"],indent=2))}</pre></details>'
    body+='<h2>Referencia MQ135</h2><pre>'+esc(json.dumps(result['eco2'],indent=2,ensure_ascii=False))+'</pre>'
    body+='<h2>Rangos del paper</h2>'+''.join(f'<p>{esc(v)}</p>' for v in THRESHOLDS.values())
    body+='<p>Las categorías describen lecturas, no certifican seguridad del hogar. Los valores DHT11 fuera de especificación se conservan en el CSV y se excluyen de inferencia.</p>'
    body+='<pre>'+esc(json.dumps({k:v['categories_raw'] for k,v in result['variables'].items()},indent=2,ensure_ascii=False))+'</pre>'
    body+='<h2>Indicador descriptivo T/HR</h2><p>'+esc(json.dumps(result['environmental_fraction'],ensure_ascii=False))+'</p>'
    body+='<h2>Gráficos</h2>'+''.join(f'<img src="{p}" alt="{p}">' for p in ['series.png','distribuciones.png','correlaciones.png'])
    body+='<h2>Correlaciones descriptivas</h2><pre>'+esc(json.dumps(result['correlations_descriptive'],indent=2,ensure_ascii=False))+'</pre>'
    body+='<h2>Episodios y seguimiento</h2><p>'+esc(result['insights']['duration_method'])+'</p>'
    for event in result['insights']['events'].values():
        if event['outside_n']: body+=f'<p><b>{esc(event["title"])}</b>: {event["percent_valid_readings"]:.1f} % de lecturas válidas; {event["estimated_minutes"]:.1f} min aproximados; {event["episode_count"]} episodio(s).</p>'
    body+='<h2>Plan de revisión</h2>'
    for item in result['insights']['action_plan']:
        body+='<h3>'+esc(item['title'])+'</h3><p>'+esc(item['evidence'])+'</p><ul>'+''.join('<li>'+esc(a)+'</li>' for a in item['actions'])+'</ul><p><b>Cómo comprobarlo:</b> '+esc(item['how_to_check'])+'</p>'
        for source_id in item['source_ids']:
            source=result['insights']['sources'][source_id];body+='<p><a href="'+esc(source['url'])+'">'+esc(source['title'])+'</a></p>'
    body+='<h2>Recomendaciones de revisión</h2><ul>'+''.join('<li>'+esc(x)+'</li>' for x in result['recommendations'])+'</ul>'
    body+='<p>Interpretación local opcional del LLM: archivo interpretacion_llm.txt. Es un borrador para revisión humana, independiente de los resultados numéricos anteriores.</p>'
    body+='<details><summary>Metadatos</summary><pre>'+esc(json.dumps(metadata,indent=2,ensure_ascii=False))+'</pre></details>'
    (folder/'informe.html').write_text('<!doctype html><html lang="es"><meta charset="utf-8"><title>ItAI · Informe</title><style>body{font:16px system-ui;max-width:1050px;margin:40px auto;padding:20px;color:#163040;line-height:1.5}h1,h2{color:#12697d}table{border-collapse:collapse;width:100%}td,th{padding:10px;border-bottom:1px solid #ccd}img{max-width:100%}pre{white-space:pre-wrap;background:#f1f5f7;padding:16px;overflow-wrap:anywhere}</style>'+body+'</html>', encoding='utf-8')
    return result
