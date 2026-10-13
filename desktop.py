#!/usr/bin/env python3
"""ItAI desktop. Native Qt widgets, no browser, no embedded web view."""
import json
import html
import math
import os
from pathlib import Path
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import uuid
from datetime import datetime
from PySide6.QtCore import Qt, QThread, Signal, QTimer, QRectF, QPointF, QUrl
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QFont, QDesktopServices, QIcon
from PySide6.QtWidgets import (QApplication,QMainWindow,QWidget,QFrame,QLabel,QPushButton,QVBoxLayout,
    QHBoxLayout,QGridLayout,QLineEdit,QComboBox,QStackedWidget,QProgressBar,QTextBrowser,QFileDialog,
    QMessageBox,QDialog,QFormLayout,QDoubleSpinBox,QCheckBox,QDialogButtonBox,QScrollArea,QTableWidget,
    QTableWidgetItem,QHeaderView,QTabWidget,QInputDialog)
from itai import ROOT, request, connect_db, save_batch, read_rows, atomic_json, utc, ollama_report
from analysis import report, summarize, classify, fmt, p_fmt
from local_chat import chat_stream, visit_context
from mq135 import enrich, convert, calibrate
from runtime_paths import ASSETS
from equipment import read_config, import_transfer, CONFIG

SETTINGS=ROOT/'desktop_settings.json'
PROFILE=ROOT/'mq_reference.json'
KEY=os.environ.get('ITAI_KEY') or read_config().get('api_key','')
BASE='http://192.168.4.1'
COLORS={'temperature':'#df8959','humidity':'#488cbe','eco2':'#1f9c83'}
NAMES={'temperature':'Temperatura','humidity':'Humedad relativa','eco2':'Gases · eCO₂ estimado'}
UNITS={'temperature':'°C','humidity':'%','eco2':'ppm estimados'}

STYLE='''
QWidget {font-family:"Segoe UI","Avenir Next","Helvetica Neue",sans-serif;font-size:14px;color:#203e47;}
QMainWindow, #Main {background:#f3f5f4;}
#Sidebar {background:#173e45;} #Sidebar QLabel {color:#c5dedc;} #Sidebar #Brand {font-size:36px;font-weight:700;color:#ffffff;}
#Sidebar QPushButton {background:transparent;color:#d4e7e5;border:0;text-align:left;padding:13px 16px;border-radius:10px;font-weight:600;}
#Sidebar QPushButton:checked {background:#2e565b;color:white;} #Sidebar QPushButton:hover{background:#264e55;}
QLabel#Title {font-size:30px;font-weight:700;letter-spacing:-1px;} QLabel#Subtitle {color:#6d858a;}
QLabel#Eyebrow {color:#72968f;font-size:11px;font-weight:700;letter-spacing:2px;}
QFrame#Card {background:white;border:1px solid #e0e8e4;border-radius:16px;}
QLabel#Value {font-size:40px;font-weight:600;letter-spacing:-1px;} QLabel#CardTitle {font-size:13px;color:#657e85;font-weight:600;}
QLabel#Badge {color:#397d70;background:#edf6f2;border-radius:6px;padding:5px 8px;font-size:12px;}
QPushButton {background:white;border:1px solid #d7e2dd;border-radius:9px;padding:10px 15px;font-weight:600;}
QPushButton:hover {background:#eaf3ef;} QPushButton:disabled {color:#9aa9a6;background:#edf1ee;}
QPushButton#Primary {background:#1c806d;color:white;border:0;} QPushButton#Primary:hover{background:#176b5c;}
QPushButton#Stop:disabled {color:#aab6b3;background:#edf1ee;border:1px solid #e2e8e5;}
QPushButton#Stop {color:#a15b3a;background:#fcf2e9;border:1px solid #ecd9cb;}
QLineEdit,QComboBox,QDoubleSpinBox {background:white;border:1px solid #d7e2dd;border-radius:8px;padding:8px;min-height:20px;}
QProgressBar {background:#e4eeea;border:0;border-radius:4px;height:8px;max-height:8px;} QProgressBar::chunk {background:#299b81;border-radius:4px;}
QTextBrowser {background:white;border:0;padding:12px;} QTableWidget {background:white;border:0;gridline-color:#e7ece9;}
QHeaderView::section {background:#edf3ef;border:0;padding:9px;color:#536b70;font-weight:600;}
QTabWidget::pane {border:0;background:white;} QTabBar::tab {padding:12px 18px;background:#eaf0ed;color:#52706c;} QTabBar::tab:selected {background:white;color:#176d5b;font-weight:700;}
QScrollArea {border:0;background:transparent;} QDialog {background:#f3f5f4;}
'''

def label(text='',name='',wrap=False):
    q=QLabel(text);q.setObjectName(name);q.setWordWrap(wrap);return q

def card():
    w=QFrame();w.setObjectName('Card');return w

def json_read(path,default=None):
    try:return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError,ValueError):return default

def preserved(status):
    """Never overwrite ESP32 buffered readings which are not already saved on this Mac."""
    if not status.get('count'):return True
    for base in (ROOT/'mediciones',ROOT/'referencias'):
        for path in base.glob('*/sesion.json'):
            meta=json_read(path,{})
            if meta.get('session_id')==status['session_id']:
                try:
                    db=sqlite3.connect((path.parent / "lecturas.sqlite").resolve().as_uri()+"?mode=ro",uri=True)
                    n=db.execute('SELECT COUNT(*) FROM samples').fetchone()[0];db.close()
                    if n>=status['count']:return True
                except sqlite3.Error:pass
    return False

class Job(QThread):
    updated=Signal(dict)
    completed=Signal(str,dict)
    failed=Signal(str)
    def __init__(self,house,profile=None,resume=None,calibration_config=None,model='auto'):
        super().__init__();self.house=house;self.profile=profile;self.resume=resume;self.calibration_config=calibration_config;self.model=model
    def emit_state(self,rows,meta,message,error=''):
        self.updated.emit({'rows':rows,'meta':meta,'message':message,'error':error})
    def run(self):
        db=None;folder=None;meta=None
        try:
            status=request(BASE,'/status',KEY)
            if status.get('protocol')!=2:raise ValueError('El firmware no corresponde a ItAI local.')
            if self.resume:
                folder=Path(self.resume);meta=json_read(folder/'sesion.json')
                if not meta:raise ValueError('La carpeta no contiene una sesión de ItAI.')
                self.calibration_config=meta.get('calibration_config')
                if meta['session_id']!=status['session_id'] or (meta.get('boot_id') is not None and meta['boot_id']!=status['boot_id']):
                    raise ValueError('La ESP32 se reinició o tiene otra sesión. Puedes consultar los datos ya guardados en Resultados.')
            else:
                if status['active']:raise ValueError('La ESP32 sigue midiendo otra sesión. Pulsa Recuperar visita.')
                if not preserved(status):raise ValueError('Hay lecturas pendientes en la ESP32. Recupéralas antes de comenzar otra visita o referencia.')
                if self.calibration_config and status.get('startup_remaining_ms',0)>0:
                    raise ValueError(f"El sensor está en arranque. Faltan aproximadamente {math.ceil(status['startup_remaining_ms']/60000)} minutos antes de preparar la referencia.")
                sid=uuid.uuid4().hex;kind='referencias' if self.calibration_config else 'mediciones'
                folder=ROOT/kind/(datetime.now().strftime('%Y%m%d-%H%M%S')+'_'+self.house+'_'+sid[:6]);folder.mkdir(parents=True)
                seconds=60 if self.calibration_config else 1800
                meta={'house':self.house,'session_id':sid,'created_utc':utc(),'target':int(seconds/2.5),'period_ms':2500,
                    'duration_ms':seconds*1000,'demo':False,'base':BASE,'mq_profile':self.profile,
                    'kind':kind,'calibration_config':self.calibration_config,'protocol_adaptation':'720 adquisiciones en 30 minutos; referencia separada de 60 segundos.'}
                atomic_json(folder/'sesion.json',meta)
                # UUID persisted before /start: a lost response can be recovered, not restarted.
                status=request(BASE,'/start',KEY,{'id':sid,'seconds':seconds})
            meta['boot_id']=status['boot_id'];meta.setdefault('device_at_start',status);atomic_json(folder/'sesion.json',meta)
            db=connect_db(folder);rows=read_rows(db);cursor=rows[-1]['sequence'] if rows else 0
            errors=0;stopped=False
            while True:
                try:
                    if self.isInterruptionRequested() and not stopped:
                        request(BASE,'/stop',KEY,{'id':meta['session_id']});stopped=True
                    status=request(BASE,'/status',KEY)
                    if status['boot_id']!=meta['boot_id'] or status['session_id']!=meta['session_id']:
                        raise ValueError('Cambió la sesión o la ESP32 se reinició. Tus datos descargados están guardados.')
                    query=urllib.parse.urlencode({'id':meta['session_id'],'after':cursor})
                    batch=request(BASE,'/samples?'+query,KEY)
                    if batch['session_id']!=meta['session_id'] or batch['boot_id']!=meta['boot_id']:raise ValueError('Identidad de lote inesperada')
                    for expected,r in enumerate(batch['samples'],cursor+1):
                        if r['sequence']!=expected:raise ValueError('Secuencia discontinua en el lote recibido')
                    save_batch(db,batch['samples']);rows=read_rows(db);cursor=rows[-1]['sequence'] if rows else 0
                    self.emit_state(rows,meta,'Preparando referencia…' if self.calibration_config else 'Midiendo · tus lecturas se guardan automáticamente')
                    errors=0
                    if not status['active'] and cursor>=status['count']:
                        meta['completion']='completa' if cursor==meta['target'] else 'detenida antes de terminar';meta['device_final']=status;break
                    self.msleep(100 if cursor<status['count'] else 800)
                except (urllib.error.URLError,TimeoutError,OSError) as e:
                    errors+=1;self.emit_state(rows,meta,'Reconectando · conservamos las lecturas recibidas',str(e))
                    if self.isInterruptionRequested() or errors>=60:raise RuntimeError('No se pudo conectar. La ESP32 puede seguir midiendo: conserva la alimentación y usa Recuperar visita.')
                    self.msleep(1500)
            atomic_json(folder/'sesion.json',meta)
            if self.calibration_config:
                if meta.get('completion')!='completa':raise ValueError('Referencia interrumpida; no se reemplazó la referencia anterior.')
                c=self.calibration_config
                profile=calibrate(rows,c['circuit'],c['reference_ppm'],c['reference_kind'],c['reference_note'],conditioned=c['conditioned'])
                atomic_json(folder/'referencia.json',profile);atomic_json(PROFILE,profile)
                self.completed.emit(str(folder),{'reference':profile});return
            self.emit_state(rows,meta,'Preparando tu resumen y los gráficos…')
            if rows:
                result=report(rows,meta,folder)
                if self.model!='none':ollama_report(result,folder,self.model)
                self.completed.emit(str(folder),result)
            else:raise ValueError('La sesión no recibió lecturas. No se calcularon estadísticas.')
        except Exception as e:
            if folder and meta:
                meta['completion']='incompleta: '+str(e);atomic_json(folder/'sesion.json',meta)
            self.failed.emit(str(e)+(f'\nDatos: {folder}' if folder else ''))
        finally:
            if db:db.close()

class ChatJob(QThread):
    chunk=Signal(str);done=Signal(str);failed=Signal(str)
    def __init__(self,context,history,question,model):
        super().__init__();self.context=context;self.history=history;self.question=question;self.model=model
    def run(self):
        answer=''
        try:
            for part in chat_stream(self.context,self.history,self.question,self.model,self.isInterruptionRequested):
                answer+=part;self.chunk.emit(part)
            if self.isInterruptionRequested():answer+='\n\n[Respuesta detenida.]'
            self.done.emit(answer)
        except Exception as e:self.failed.emit(str(e))

class ReadJob(QThread):
    done=Signal(object);failed=Signal(str)
    def run(self):
        try:self.done.emit(request(BASE,'/status',KEY))
        except Exception:self.failed.emit('Conecta este computador a ItAI-Local. La red no necesita internet.')

class Plot(QWidget):
    def __init__(self):
        super().__init__();self.rows=[];self.key='temperature';self.setMinimumHeight(180);self.setMouseTracking(True);self.hover=None
    def set_rows(self,rows):self.rows=rows;self.update()
    def mouseMoveEvent(self,event):self.hover=event.position().x();self.update()
    def leaveEvent(self,event):self.hover=None;self.update()
    def paintEvent(self,event):
        p=QPainter(self);p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w,h=self.width(),self.height();left,right,top,bottom=65,w-25,25,h-36
        vals=[r.get(self.key) for r in self.rows if r.get(self.key) is not None and math.isfinite(r[self.key])]
        p.setFont(QFont('Segoe UI' if sys.platform=='win32' else 'Avenir Next',10))
        if not vals:
            p.setPen(QColor('#8ca09f'));p.drawText(self.rect(),Qt.AlignmentFlag.AlignCenter,'Las lecturas aparecerán aquí\n'+('Prepara una referencia para ver ppm estimados.' if self.key=='eco2' else 'Inicia una visita cuando el equipo esté listo.'));return
        lo,hi=min(vals),max(vals);pad=max((hi-lo)*.2,1 if self.key!='eco2' else 50);lo-=pad;hi+=pad
        if self.key=='eco2':lo=max(0,lo)
        xmax=max(30,max(r['elapsed_ms'] for r in self.rows)/60000)
        def x(v):return left+v/60000/xmax*(right-left)
        def y(v):return bottom-(v-lo)/(hi-lo)*(bottom-top)
        p.setPen(QPen(QColor('#e7eeea'),1))
        for i in range(5):
            value=lo+(hi-lo)*i/4;yy=y(value);p.drawLine(QPointF(left,yy),QPointF(right,yy));p.setPen(QColor('#839792'))
            p.drawText(QRectF(0,yy-10,left-10,20),Qt.AlignmentFlag.AlignRight,f'{value:.0f}' if self.key=='eco2' else f'{value:.1f}');p.setPen(QPen(QColor('#e7eeea'),1))
        p.setPen(QColor('#839792'))
        for minute in range(0,int(xmax)+1,5):p.drawText(QRectF(x(minute*60000)-20,bottom+10,50,20),Qt.AlignmentFlag.AlignCenter,f'{minute} min')
        thresholds={'temperature':[16,18,26,32],'humidity':[20,30,60,70,90],'eco2':[1000,2000]}[self.key]
        for t in thresholds:
            if lo<t<hi:p.setPen(QPen(QColor('#d7aa78'),1,Qt.PenStyle.DashLine));p.drawLine(QPointF(left,y(t)),QPointF(right,y(t)))
        path=QPainterPath();new=True;prev=None;valid=[]
        for r in self.rows:
            val=r.get(self.key)
            if val is None or not math.isfinite(val):new=True;continue
            xx,yy=x(r['elapsed_ms']),y(val)
            if new or (prev is not None and r['elapsed_ms']-prev>2800):path.moveTo(xx,yy)
            else:path.lineTo(xx,yy)
            new=False;prev=r['elapsed_ms'];valid.append((xx,yy,r))
        p.setPen(QPen(QColor(COLORS[self.key]),2.5));p.drawPath(path)
        if valid:
            xx,yy,last=valid[-1];p.setBrush(QColor(COLORS[self.key]));p.setPen(Qt.PenStyle.NoPen);p.drawEllipse(QPointF(xx,yy),4,4)
        if self.hover is not None and valid:
            xx,yy,r=min(valid,key=lambda z:abs(z[0]-self.hover));p.setPen(QPen(QColor('#adbdb8'),1,Qt.PenStyle.DashLine));p.drawLine(QPointF(xx,top),QPointF(xx,bottom))
            text=f"{r['elapsed_ms']/60000:.2f} min · {r[self.key]:.1f} {UNITS[self.key]}"
            box=QRectF(min(max(left,xx-115),right-260),top,260,27);p.setPen(Qt.PenStyle.NoPen);p.setBrush(QColor('#173e45'));p.drawRoundedRect(box,6,6);p.setPen(QColor('white'));p.drawText(box,Qt.AlignmentFlag.AlignCenter,text)
        p.end()

class ReferenceDialog(QDialog):
    def __init__(self,parent=None):
        super().__init__(parent);self.setWindowTitle('Preparar la referencia del MQ135');self.setMinimumWidth(550)
        layout=QVBoxLayout(self);layout.addWidget(label('Una referencia que podamos repetir','Title'))
        layout.addWidget(label('Se tomarán 24 lecturas durante 60 segundos. Hazlo antes de visitar hogares, en condiciones estables. La referencia se conserva; no se reinicia por casa.','Subtitle',True))
        form=QFormLayout();layout.addLayout(form);saved=json_read(SETTINGS,{})
        self.v=QDoubleSpinBox();self.v.setRange(2.5,5.5);self.v.setDecimals(3);self.v.setValue(saved.get('circuit_v',5));form.addRow('Alimentación del circuito MQ (V)',self.v)
        self.factor=QDoubleSpinBox();self.factor.setRange(0,20);self.factor.setDecimals(4);self.factor.setValue(saved.get('divider_factor',0));self.factor.setSpecialValueText('Confirmar divisor');form.addRow('Factor A0 / voltaje GPIO34',self.factor)
        layout.addWidget(label('Factor = (R superior + R inferior) / R inferior. No es la referencia de 3,3 V del ESP32.','Subtitle',True))
        self.kind=QComboBox();self.kind.addItem('Referencia medida con otro instrumento','measured');self.kind.addItem('Referencia exterior supuesta (normalización)','assumed');form.addRow('Origen de la referencia',self.kind)
        self.ppm=QDoubleSpinBox();self.ppm.setRange(0,5000);self.ppm.setValue(0);self.ppm.setSpecialValueText('Indicar concentración');self.ppm.setSuffix(' ppm');form.addRow('Concentración de referencia',self.ppm)
        self.note=QLineEdit();self.note.setPlaceholderText('Instrumento / lugar / condiciones / supuesto utilizado');form.addRow('Registro de la referencia',self.note)
        self.confirm=QCheckBox('Confirmé alimentación y divisor, y completé el acondicionamiento del sensor.');self.confirm.setChecked(False);layout.addWidget(self.confirm)
        layout.addWidget(label('El modelo produce ppm estimados. Una referencia supuesta no verifica la concentración real ni convierte al MQ135 en un sensor selectivo de CO₂.','Subtitle',True))
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel|QDialogButtonBox.StandardButton.Ok);buttons.button(QDialogButtonBox.StandardButton.Ok).setText('Preparar · 60 segundos');buttons.accepted.connect(self.accept_checked);buttons.rejected.connect(self.reject);layout.addWidget(buttons)
    def accept_checked(self):
        if self.factor.value()<1 or self.ppm.value()<100 or not self.note.text().strip() or not self.confirm.isChecked():
            QMessageBox.information(self,'Faltan datos','Indica el divisor, la concentración y su origen. Confirma la preparación del sensor.');return
        self.config={'circuit':{'circuit_v':self.v.value(),'divider_factor':self.factor.value(),'rl_kohm':None},
            'reference_ppm':self.ppm.value(),'reference_kind':self.kind.currentData(),'reference_note':self.note.text().strip(),'conditioned':True}
        atomic_json(SETTINGS,self.config['circuit']);self.accept()

class Window(QMainWindow):
    def __init__(self,demo=False):
        super().__init__();self.setWindowTitle('ItAI · Acompañar con evidencia');self.resize(1240,850);self.setMinimumSize(1020,760)
        self.rows=[];self.meta={};self.folder=None;self.worker=None;self.checker=None;self.chat_worker=None;self.chat_token=None;self.chat_history=[];self.chat_partial='';self.current_result=None;self.chat_scope=None;self.profile=json_read(PROFILE);self.demo=demo
        root=QWidget();root.setObjectName('Main');self.setCentralWidget(root);layout=QHBoxLayout(root);layout.setContentsMargins(0,0,0,0);layout.setSpacing(0)
        side=QFrame();side.setObjectName('Sidebar');side.setFixedWidth(205);sl=QVBoxLayout(side);sl.setContentsMargins(22,34,22,24)
        sl.addWidget(label('ItAI','Brand'));sl.addWidget(label('AMBIENTE Y HOGAR','Eyebrow'));sl.addSpacing(42)
        self.nav=[]
        for text in ['Visita en curso','Resultados','Preguntar a ItAI','Preparar equipo']:
            b=QPushButton(text);b.setCheckable(True);self.nav.append(b);sl.addWidget(b)
        sl.addStretch();sl.addWidget(label('Todo queda en este computador.\nSin internet. Sin nube.','',True));sl.addSpacing(20)
        self.connection=label('○  Sin comprobar','',True);sl.addWidget(self.connection)
        layout.addWidget(side);self.pages=QStackedWidget();layout.addWidget(self.pages)
        self.build_visit();self.build_results();self.build_chat();self.build_setup()
        for i,b in enumerate(self.nav):b.clicked.connect(lambda checked=False,i=i:self.navigate(i))
        self.navigate(0)
        if demo:self.load_demo()
    def navigate(self,i):
        self.pages.setCurrentIndex(i)
        for j,b in enumerate(self.nav):b.setChecked(i==j)
    def page(self):
        w=QWidget();box=QVBoxLayout(w);box.setContentsMargins(32,28,32,24);box.setSpacing(18);self.pages.addWidget(w);return w,box
    def build_visit(self):
        w,box=self.page();head=QHBoxLayout();title=QVBoxLayout();title.addWidget(label('UNA MIRADA AL AMBIENTE','Eyebrow'));title.addWidget(label('Cada hogar, una historia.','Title'));title.addWidget(label('Observa cómo cambia el ambiente durante esta visita.','Subtitle'));head.addLayout(title);head.addStretch()
        self.house=QLineEdit('PRUEBA01');self.house.setMaximumWidth(150);self.house.setPlaceholderText('Código del hogar');head.addWidget(self.house);box.addLayout(head)
        progress=card();pl=QVBoxLayout(progress);pl.setContentsMargins(22,18,22,18)
        row=QHBoxLayout();self.status=label('Listos cuando tú lo estés.');self.status.setStyleSheet('font-weight:600;');row.addWidget(self.status);row.addStretch();self.clock=label('00:00 / 30:00','Subtitle');row.addWidget(self.clock);pl.addLayout(row)
        self.bar=QProgressBar();self.bar.setRange(0,720);self.bar.setTextVisible(False);pl.addWidget(self.bar)
        buttons=QHBoxLayout();self.count=label('0 de 720 lecturas · cada 2,5 segundos','Subtitle');buttons.addWidget(self.count);buttons.addStretch()
        self.start=QPushButton('Iniciar visita');self.start.setObjectName('Primary');self.start.clicked.connect(self.begin)
        self.stop=QPushButton('Terminar');self.stop.setObjectName('Stop');self.stop.setEnabled(False);self.stop.clicked.connect(self.end_visit)
        self.resume=QPushButton('Recuperar');self.resume.clicked.connect(self.resume_visit)
        buttons.addWidget(self.resume);buttons.addWidget(self.stop);buttons.addWidget(self.start);pl.addLayout(buttons);box.addWidget(progress)
        cards=QHBoxLayout();cards.setSpacing(14);self.values={};self.badges={};self.notes={}
        for key in NAMES:
            c=card();cl=QVBoxLayout(c);cl.setContentsMargins(20,17,20,17);cl.addWidget(label(NAMES[key],'CardTitle'))
            val=label('—','Value');val.setStyleSheet(f'color:{COLORS[key]}');self.values[key]=val;cl.addWidget(val)
            cl.addWidget(label(UNITS[key],'Subtitle'));badge=label('Esperando lecturas','Badge',True);self.badges[key]=badge;cl.addWidget(badge)
            note=label('','Subtitle',True);self.notes[key]=note;cl.addWidget(note);cards.addWidget(c)
        box.addLayout(cards)
        chart=card();chl=QVBoxLayout(chart);chl.setContentsMargins(20,16,20,12);row=QHBoxLayout();row.addWidget(label('El ambiente, minuto a minuto'));row.addStretch();self.choose=QComboBox()
        for key in NAMES:self.choose.addItem(NAMES[key],key)
        self.choose.currentIndexChanged.connect(self.change_plot);row.addWidget(self.choose);chl.addLayout(row);self.plot=Plot();chl.addWidget(self.plot);box.addWidget(chart,1)
        self.notice=label('Conecta el computador a ItAI-Local. Puedes preparar la referencia del MQ135 desde «Preparar equipo».','Subtitle',True);box.addWidget(self.notice)
    def build_results(self):
        w,box=self.page();row=QHBoxLayout();row.addWidget(label('Lo que nos cuenta la visita','Title'));row.addStretch();openbtn=QPushButton('Abrir visita');self.open_visit_btn=openbtn;openbtn.clicked.connect(self.open_visit);row.addWidget(openbtn);box.addLayout(row)
        self.result_title=label('Al terminar, encontrarás aquí tu resumen y las cifras.','Subtitle',True);box.addWidget(self.result_title)
        self.result_tabs=QTabWidget();box.addWidget(self.result_tabs,1)
        self.narrative=QTextBrowser();self.result_tabs.addTab(self.narrative,'Resumen')
        self.episodes_view=QTextBrowser();self.result_tabs.addTab(self.episodes_view,'Episodios')
        self.plan_view=QTextBrowser();self.plan_view.setOpenExternalLinks(True);self.result_tabs.addTab(self.plan_view,'Plan de acción')
        stats_page=QWidget();stats_layout=QVBoxLayout(stats_page)
        self.table=QTableWidget(0,6);self.table.setHorizontalHeaderLabels(['Variable','Media','Rango','N válido','Pendiente [IC95 %]','p ajustada']);self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents);self.table.horizontalHeader().setSectionResizeMode(4,QHeaderView.ResizeMode.Stretch);self.table.setMaximumHeight(205);self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers);stats_layout.addWidget(self.table)
        self.stats_detail=QTextBrowser();stats_layout.addWidget(self.stats_detail,1)
        self.stats=label('','Subtitle',True);stats_layout.addWidget(self.stats);self.result_tabs.addTab(stats_page,'Estadística')
        row=QHBoxLayout();self.files=QPushButton('Ver archivos de la visita');self.files.clicked.connect(self.open_folder);row.addWidget(self.files);row.addStretch()
        ask=QPushButton('Preguntarle a ItAI');ask.setObjectName('Primary');ask.clicked.connect(lambda:self.navigate(2));row.addWidget(ask);box.addLayout(row)
    def build_chat(self):
        w,box=self.page();box.addWidget(label('Conversemos sobre esta visita','Title'))
        self.chat_context=label('Puedes preguntar por el equipo o seleccionar una visita en Resultados.','Subtitle',True);box.addWidget(self.chat_context)
        quick=QHBoxLayout()
        for title,question in [('Qué revisar primero','¿Qué debería revisar primero en esta visita y por qué?'),('Entender los resultados','Explícame qué significan los resultados, sin tecnicismos.'),('Preparar la referencia','¿Cómo completo la referencia del MQ135 y qué datos me faltan?')]:
            btn=QPushButton(title);btn.clicked.connect(lambda checked=False,q=question:self.ask_suggested(q));quick.addWidget(btn)
        box.addLayout(quick);self.chat_view=QTextBrowser();box.addWidget(self.chat_view,1)
        self.chat_status=label('Ollama local · las respuestas no modifican tus mediciones.','Subtitle',True);box.addWidget(self.chat_status)
        row=QHBoxLayout();self.chat_input=QLineEdit();self.chat_input.setPlaceholderText('Escribe tu pregunta…');self.chat_input.setMaxLength(4000);self.chat_input.returnPressed.connect(self.send_chat_question);row.addWidget(self.chat_input,1)
        self.send_btn=QPushButton('Preguntar');self.send_btn.setObjectName('Primary');self.send_btn.clicked.connect(self.send_chat_question);row.addWidget(self.send_btn)
        self.cancel_chat=QPushButton('Detener');self.cancel_chat.setEnabled(False);self.cancel_chat.clicked.connect(self.stop_chat);row.addWidget(self.cancel_chat);box.addLayout(row)
        row=QHBoxLayout();clear=QPushButton('Nueva conversación');clear.clicked.connect(self.clear_chat);row.addWidget(clear);save=QPushButton('Guardar conversación');save.clicked.connect(self.save_chat);row.addWidget(save);row.addStretch();box.addLayout(row)
        box.addWidget(label('Respuesta generada por IA: contrástala con las cifras del informe. El chat usa solo la visita actual y funciona sin nube.','Subtitle',True))
        self.render_chat()
    def render_chat(self):
        parts=['<style>p{line-height:1.55}h4{color:#1c806d}div{margin:12px 0 24px}</style>']
        if not self.chat_history:parts.append('<h3>Hola. ¿Qué te gustaría entender mejor?</h3><p>Puedo ayudarte a interpretar la visita, ordenar los próximos pasos o preparar la referencia del sensor.</p>')
        for m in self.chat_history:
            who='Tú' if m['role']=='user' else 'ItAI · respuesta de Ollama'
            parts.append('<h4>'+who+'</h4><div>'+html.escape(m['content']).replace('\n','<br>')+'</div>')
        if self.chat_partial:parts.append('<h4>ItAI · pensando contigo</h4><div>'+html.escape(self.chat_partial).replace('\n','<br>')+'</div>')
        self.chat_view.setHtml(''.join(parts));bar=self.chat_view.verticalScrollBar();bar.setValue(bar.maximum())
    def ask_suggested(self,question):
        self.chat_input.setText(question);self.send_chat_question()
    def send_chat_question(self):
        if self.chat_worker and self.chat_worker.isRunning():return
        question=self.chat_input.text().strip()
        if not question:return
        # Snapshot the current visit, never merge it with historical households.
        result=self.current_result
        if self.worker and self.worker.isRunning() and self.rows:
            _,result=summarize(self.rows,{**self.meta,'analysis_stage':'en curso'})
        context=visit_context(result)
        token=uuid.uuid4().hex;self.chat_token=token;history=list(self.chat_history)
        self.chat_history.append({'role':'user','content':question});self.chat_input.clear();self.chat_partial='';self.render_chat()
        self.send_btn.setEnabled(False);self.cancel_chat.setEnabled(True);self.chat_status.setText('Ollama está preparando una respuesta en este computador…')
        self.chat_worker=ChatJob(context,history,question,self.model.text().strip() or 'auto')
        self.chat_worker.chunk.connect(lambda part:self.chat_chunk(token,part))
        self.chat_worker.done.connect(lambda answer:self.chat_done(token,answer))
        self.chat_worker.failed.connect(lambda error:self.chat_failed(token,error))
        self.chat_worker.finished.connect(self.chat_finished);self.chat_worker.start()
    def chat_chunk(self,token,part):
        if token!=self.chat_token:return
        self.chat_partial+=part;self.render_chat()
    def chat_done(self,token,answer):
        if token!=self.chat_token:return
        self.chat_history.append({'role':'assistant','content':answer});self.chat_partial='';self.render_chat();self.chat_status.setText('Respuesta local. Puedes pedir una aclaración o revisar el plan de acción.')
    def chat_failed(self,token,error):
        if token!=self.chat_token:return
        self.chat_partial='';self.chat_history.append({'role':'assistant','content':'No pude completar la respuesta local. '+error});self.render_chat();self.chat_status.setText('El análisis y las mediciones siguen guardados. Comprueba Ollama o el modelo seleccionado.')
    def chat_finished(self):self.send_btn.setEnabled(True);self.cancel_chat.setEnabled(False)
    def stop_chat(self):
        if self.chat_worker and self.chat_worker.isRunning():self.chat_worker.requestInterruption();self.chat_status.setText('Deteniendo respuesta…');self.cancel_chat.setEnabled(False)
    def clear_chat(self):
        self.stop_chat();self.chat_token=None;self.chat_history=[];self.chat_partial='';self.render_chat()
    def save_chat(self):
        if not self.chat_history:return
        path,_=QFileDialog.getSaveFileName(self,'Guardar conversación local',str((self.folder or ROOT)/'conversacion_itai.txt'),'Texto (*.txt)')
        if path:
            lines=['CONVERSACIÓN CON OLLAMA · GENERADA POR IA, NO ES EL INFORME ESTADÍSTICO',self.chat_context.text(),'']
            lines.extend(('Tú: ' if m['role']=='user' else 'ItAI: ')+m['content']+'\n' for m in self.chat_history)
            Path(path).write_text('\n'.join(lines), encoding='utf-8');self.chat_status.setText('Conversación guardada en este computador.')
    def populate_insights(self,result):
        esc=html.escape;ins=result.get('insights',{})
        if not ins:
            self.episodes_view.setPlainText('Abre de nuevo esta visita para calcular el análisis ampliado.');self.plan_view.setPlainText('El análisis ampliado aparecerá al abrir la visita.');return
        body='<h2>Qué ocurrió y durante cuánto tiempo</h2><p>'+esc(ins['duration_method'])+'</p>'
        body+='<table cellspacing="10"><tr><th align="left">Condición</th><th>Lecturas</th><th>% válido</th><th>Min aprox.</th><th>Episodios</th><th>Más largo (min)</th></tr>'
        for e in ins['events'].values():
            percent='—' if e['percent_valid_readings'] is None else f"{e['percent_valid_readings']:.1f}"
            body+=f'<tr><td>{esc(e["title"])}</td><td>{e["outside_n"]}/{e["valid_n"]}</td><td>{percent}</td><td>{e["estimated_minutes"]:.1f}</td><td>{e["episode_count"]}</td><td>{e["longest_episode_minutes"]:.1f}</td></tr>'
        body+='</table><p>Los umbrales se superponen: por ejemplo, &gt;70 % es parte de &gt;60 %. No sumar sus duraciones.</p>'
        self.episodes_view.setHtml(body)
        body='<h2>Por dónde empezar</h2><p>Pasos orientativos basados en esta visita. La prioridad es de revisión, no una clasificación oficial del hogar.</p>'
        for item in ins['action_plan']:
            body+='<h3>'+esc(item['title'])+'</h3><p><b>Lo observado:</b> '+esc(item['evidence'])+'</p><ol>'+''.join('<li>'+esc(a)+'</li>' for a in item['actions'])+'</ol><p><b>Cómo comprobarlo:</b> '+esc(item['how_to_check'])+'</p>'
            for sid in item['source_ids']:
                source=ins['sources'][sid];body+='<p><a href="'+esc(source['url'])+'">'+esc(source['title'])+'</a></p>'
        self.plan_view.setHtml(body)
        body='<h3>Distribución, cobertura y cambio durante la visita</h3>'
        for key,d in ins['details'].items():
            body+='<h4>'+esc(NAMES.get(key,'MQ135 · ADC')+' ('+UNITS.get(key,'cuentas ADC')+')')+'</h4><p>Cobertura válida prevista: '+f"{d['valid_percent_expected']:.1f} %"+'. P05 / P25 / P75 / P95: '+', '.join(fmt(d[k]) for k in ('p05','p25','p75','p95'))+'.</p>'
            f=d['first_last']
            if f['available']:body+=f'<p>Primeros 5 min: {f["first_mean"]:.3g}; últimos 5 min: {f["last_mean"]:.3g}; diferencia descriptiva: {f["difference"]:.3g}. No es una prueba entre hogares.</p>'
            else:body+='<p>'+esc(f['reason'])+'</p>'
            if 'fitted_change' in d:
                c=d['fitted_change'];body+=f'<p>Cambio ajustado en el tramo de {c["segment_minutes"]:.1f} min: {c["change"]:.3g}; IC95 % [{c["ci95"][0]:.3g}, {c["ci95"][1]:.3g}].</p>'
            inf=result['variables'][key]['inference']
            if inf['available']:body+=f'<p>n del tramo inferencial: {inf["n"]}; desde {inf["start_min"]:.2f} hasta {inf["end_min"]:.2f} min. Media ajustada: {inf["mean_fitted"]:.3g}; IC95 % [{inf["mean_ci95"][0]:.3g}, {inf["mean_ci95"][1]:.3g}].</p>'
        self.stats_detail.setHtml(body)
    def build_setup(self):
        w,box=self.page();box.addWidget(label('Un equipo listo para escuchar','Title'));box.addWidget(label('Prepara una vez. Conserva la misma referencia entre hogares.','Subtitle'))
        c=card();cl=QVBoxLayout(c);cl.setContentsMargins(24,24,24,24)
        cl.addWidget(label('1 · Conectar el equipo','CardTitle'));cl.addWidget(label('Conecta el Wi-Fi a ItAI-Local con la contraseña de tu circuito.\nDHT11: GPIO16 · MQ135 A0: GPIO34 con divisor.','',True))
        row=QHBoxLayout();self.import_equipment_btn=QPushButton('Importar equipo desde el Mac');self.import_equipment_btn.clicked.connect(self.import_equipment);row.addWidget(self.import_equipment_btn)
        self.configure_key_btn=QPushButton('Configurar clave');self.configure_key_btn.clicked.connect(self.configure_key);row.addWidget(self.configure_key_btn);cl.addLayout(row)
        self.check=QPushButton('Comprobar conexión');self.check.clicked.connect(self.check_device);cl.addWidget(self.check);box.addWidget(c)
        c=card();cl=QVBoxLayout(c);cl.setContentsMargins(24,24,24,24);cl.addWidget(label('2 · Referencia para ppm estimados','CardTitle'))
        self.profile_label=label('','',True);cl.addWidget(self.profile_label);self.cal_button=QPushButton('Preparar referencia · 60 segundos');self.cal_button.setObjectName('Primary');self.cal_button.clicked.connect(self.prepare_reference);cl.addWidget(self.cal_button);box.addWidget(c)
        c=card();cl=QVBoxLayout(c);cl.setContentsMargins(24,24,24,24);cl.addWidget(label('3 · Una explicación cercana, en este computador','CardTitle'))
        cl.addWidget(label('Ollama ayuda a ordenar una explicación sencilla de los resultados. Las cifras y las conclusiones se calculan y verifican en Python.','',True))
        self.model=QLineEdit('auto');self.model.setPlaceholderText('auto, llama3:latest o none');cl.addWidget(self.model);cl.addWidget(label('Chat opcional: instala y abre Ollama con un modelo local descargado. Para medir sin LLM, escribe none.','Subtitle',True));box.addWidget(c)
        data_btn=QPushButton('Abrir carpeta de datos y respaldos');data_btn.clicked.connect(lambda:QDesktopServices.openUrl(QUrl.fromLocalFile(str(ROOT))));box.addWidget(data_btn);box.addStretch();self.refresh_profile()
    def import_equipment(self):
        path,_=QFileDialog.getOpenFileName(self,'Importar configuración privada del mismo circuito','','Configuración (*.json)')
        if not path:return
        if QMessageBox.question(self,'Confirmar circuito','¿Vas a usar el mismo MQ135, ESP32 y divisor de voltaje que en el Mac? La referencia solo se debe trasladar al mismo circuito.',QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,QMessageBox.StandardButton.No)!=QMessageBox.StandardButton.Yes:return
        try:
            data=import_transfer(path,ROOT)
            global KEY
            KEY=os.environ.get('ITAI_KEY') or data['api_key'];self.profile=json_read(PROFILE);self.refresh_profile()
            QMessageBox.information(self,'Equipo importado','Configuración importada. Conecta el Wi-Fi a ItAI-Local y pulsa Comprobar conexión. Las configuraciones anteriores quedaron respaldadas.')
        except Exception as e:QMessageBox.information(self,'No se pudo importar',str(e))
    def configure_key(self):
        value,ok=QInputDialog.getText(self,'Clave del equipo','Clave API configurada en tu ESP32 (distinta de la contraseña Wi-Fi):',QLineEdit.EchoMode.Password)
        if not ok:return
        value=value.strip()
        if not value or any(ord(c)<33 or ord(c)>126 for c in value) or len(value)>200:
            QMessageBox.information(self,'Clave inválida','Usa la clave API del firmware.');return
        atomic_json(CONFIG,{'api_key':value})
        global KEY
        KEY=os.environ.get('ITAI_KEY') or value
        QMessageBox.information(self,'Clave guardada','La clave se guardó solamente en este computador.')
    def refresh_profile(self):
        p=self.profile
        if p:self.profile_label.setText(f"Referencia guardada: {p['reference_ppm']:g} ppm ({'medida' if p['reference_kind']=='measured' else 'supuesta'}).\nCircuito: {p['circuit_v']:g} V · factor del divisor: {p['divider_factor']:g}\nFecha: {p['created_utc'][:10]} · identificador {p['id']}\n{p['reference_note']}")
        else:self.profile_label.setText('Aún no hay referencia. Puedes ver temperatura, humedad y voltaje; los ppm aparecerán después de preparar el MQ135.')
    def change_plot(self):self.plot.key=self.choose.currentData();self.plot.update()
    def controls(self,running):
        self.import_equipment_btn.setEnabled(not running);self.configure_key_btn.setEnabled(not running);self.open_visit_btn.setEnabled(not running);self.start.setEnabled(not running);self.stop.setEnabled(running);self.resume.setEnabled(not running);self.cal_button.setEnabled(not running);self.house.setEnabled(not running);self.check.setEnabled(not running)
    def launch(self,resume=None,calibration=None):
        if self.worker and self.worker.isRunning():return
        if not KEY:QMessageBox.information(self,'Preparar equipo','Importa la configuración del Mac o configura la clave en Preparar equipo.');self.navigate(3);return
        self.clear_chat();self.current_result=None;self.demo=False;self.controls(True);self.rows=[];self.plot.set_rows([]);self.status.setText('Conectando al equipo…');self.navigate(0)
        self.worker=Job(self.house.text(),self.profile,resume,calibration,self.model.text().strip() or 'auto')
        self.worker.updated.connect(self.update_state);self.worker.completed.connect(self.completed);self.worker.failed.connect(self.failed);self.worker.finished.connect(lambda:self.controls(False));self.worker.start()
    def begin(self):
        if not re.fullmatch('[A-Za-z0-9_-]{1,40}',self.house.text()):QMessageBox.information(self,'Código del hogar','Usa un código corto, como H001, sin nombres ni direcciones.');return
        self.launch()
    def end_visit(self):
        if self.worker and self.worker.isRunning():self.worker.requestInterruption();self.status.setText('Cerrando y guardando la visita…');self.stop.setEnabled(False)
    def resume_visit(self):
        path=QFileDialog.getExistingDirectory(self,'Elige la carpeta de la visita',str(ROOT/'mediciones'))
        if path:
            meta=json_read(Path(path)/'sesion.json',{});self.house.setText(meta.get('house','RECUPERADA'));self.launch(resume=path)
    def prepare_reference(self):
        d=ReferenceDialog(self)
        if d.exec()==QDialog.DialogCode.Accepted:self.launch(calibration=d.config)
    def check_device(self):
        if not KEY:QMessageBox.information(self,'Preparar equipo','Primero importa el equipo o configura su clave API.');return
        self.check.setEnabled(False);self.checker=ReadJob();self.checker.done.connect(self.device_status);self.checker.failed.connect(self.failed);self.checker.finished.connect(lambda:self.check.setEnabled(True));self.checker.start()
    def device_status(self,status):
        self.connection.setText('●  Equipo conectado');mins=math.ceil(status.get('startup_remaining_ms',0)/60000)
        QMessageBox.information(self,'Equipo conectado',f"Conexión correcta.\nA0 del MQ135: GPIO{status['mq_pin']}\n"+(f'Arranque del MQ135: faltan aproximadamente {mins} minutos.' if mins else 'Periodo de arranque completado.')+('\nHay una sesión activa; usa Recuperar.' if status['active'] else ''))
    def update_state(self,data):
        new_scope=data['meta'].get('session_id',data['meta'].get('house'))
        if self.chat_scope!=new_scope:
            self.clear_chat();self.chat_scope=new_scope;self.current_result=None
        self.chat_context.setText('Contexto: '+data['meta']['house']+(' · datos simulados' if data['meta'].get('demo') else ' · visita actual'))
        self.meta=data['meta'];self.rows=enrich(data['rows'],self.meta.get('mq_profile'));self.status.setText(data['message']);self.connection.setText('●  Equipo conectado' if not data['error'] else '○  Reconectando')
        self.bar.setMaximum(self.meta['target']);self.bar.setValue(len(self.rows));self.count.setText(f"{len(self.rows)} de {self.meta['target']} lecturas · guardadas en este computador")
        elapsed=int(self.rows[-1]['elapsed_ms']/1000) if self.rows else 0;total=int(self.meta.get('duration_ms',1800000)/1000)
        self.clock.setText(f'{elapsed//60:02}:{elapsed%60:02} / {total//60:02}:{total%60:02}')
        self.plot.set_rows(self.rows)
        self.notice.setText(data['error'] or ('Referencia separada de la visita; 24 lecturas para ajustar el modelo.' if self.meta.get('kind')=='referencias' else 'Las categorías siguen el estudio. Los ppm son estimados; temperatura y humedad conservan sus unidades originales.'))
        if self.rows:
            r=self.rows[-1]
            for key in NAMES:
                value=r.get(key);self.values[key].setText('—' if value is None else f'{value:,.0f}' if key=='eco2' else f'{value:.1f}')
                self.badges[key].setText(classify(key,value) if value is not None else ('Referencia pendiente' if key=='eco2' else 'Sin lectura'))
                category=classify(key,value) if value is not None else ''
                warn=any(word in category for word in ('advertencia','elevada','riesgo','fuera'))
                self.badges[key].setStyleSheet('background:#fff0df;color:#9a5d24;border-radius:6px;padding:5px 8px;font-size:12px;' if warn else '')
                self.notes[key].setText('')
            self.notes['eco2'].setText(r['reason']+f" · {r['mq_mv']/1000:.3f} V en GPIO34")
            if r.get('extrapolated'):self.badges['eco2'].setText('Fuera del intervalo del modelo')
            if r['flags']&512:self.notes['humidity'].setText('Fuera de especificación del DHT11')
            if r['flags']&256:self.notes['temperature'].setText('Fuera de especificación del DHT11')
    def completed(self,folder,result):
        self.folder=Path(folder)
        if 'reference' in result:
            self.profile=result['reference'];self.refresh_profile();self.status.setText('Referencia guardada · lista para las próximas visitas');self.navigate(3)
            QMessageBox.information(self,'Referencia guardada','Ya puedes iniciar una visita. Usaremos esta referencia hasta que prepares otra.');return
        self.show_result(result);self.status.setText('Visita guardada · tu resumen está listo');self.navigate(1)
    def failed(self,message):
        self.status.setText('Revisemos la conexión o la preparación');self.notice.setText(message);QMessageBox.information(self,'ItAI',message)
    def open_visit(self):
        path=QFileDialog.getExistingDirectory(self,'Abrir resultados de una visita',str(ROOT/'mediciones'))
        if not path:return
        self.folder=Path(path);result=json_read(self.folder/'analisis.json')
        if not result:QMessageBox.information(self,'Sin informe','Esta visita no tiene todavía informe. Usa Recuperar para completar la descarga.');return
        db=connect_db(self.folder);raw=read_rows(db);db.close()
        if raw:_,result=summarize(raw,result['metadata'])
        self.update_state({'meta':result['metadata'],'rows':raw,'message':'Visita guardada · consulta','error':''});self.show_result(result);self.navigate(1)
    def show_result(self,result):
        self.current_result=result;self.populate_insights(result)
        self.chat_context.setText('Contexto: '+result['metadata']['house']+(' · datos simulados' if result['metadata'].get('demo') else ' · visita seleccionada'))
        self.meta=result['metadata'];self.result_title.setText(self.meta['house']+(' · DEMOSTRACIÓN CON DATOS SIMULADOS' if self.meta.get('demo') else '')+' · '+str(result['quality']['received'])+' lecturas guardadas')
        text=(self.folder/'interpretacion_llm.txt').read_text(encoding='utf-8') if (self.folder/'interpretacion_llm.txt').exists() else 'Tu análisis numérico está listo. El resumen local aún no se generó.'
        if 'LLM NO DISPONIBLE' in text or 'LLM desactivado' in text:
            from itai import llm_catalogue,OPENINGS,CLOSINGS
            text=OPENINGS[0]+'\n\n'+'\n\n'.join(llm_catalogue(result).values())+'\n\n'+CLOSINGS[0]+'\n\nResumen automático de Python; LLM no disponible.'
        else:
            marker='El LLM organiza frases verificadas; las cifras y conclusiones proceden de Python.\n\n'
            if marker in text:text=text.split(marker,1)[1]
        self.narrative.setPlainText(text)
        variables=result['variables'];self.table.setRowCount(len(variables))
        for i,(key,v) in enumerate(variables.items()):
            inf=v['inference'];trend='No evaluable'
            if inf['available']:trend=f"{inf['slope_per_minute']:.3g} [{inf['slope_ci95'][0]:.3g}, {inf['slope_ci95'][1]:.3g}]"
            for j,value in enumerate([(('eCO₂ estimado' if key=='eco2' else NAMES.get(key,'MQ135 · ADC'))+' ('+UNITS.get(key,'cuentas')+')'),fmt(v['mean']),f"{fmt(v['min'])} – {fmt(v['max'])}",str(v['analysis_n']),trend,('<1e-300' if inf['p_slope_holm']==0 else f"{inf['p_slope_holm']:.2g}") if inf['available'] else '—']):self.table.setItem(i,j,QTableWidgetItem(value))
        self.stats.setText('Pendientes por minuto · IC 95 % con corrección HAC. Inferencia exploratoria. Los detalles, p ajustadas y la referencia de ppm están en analisis.json. No representa a todos los hogares ni todo el día.')
    def open_folder(self):
        if self.folder:QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.folder)))
    def load_demo(self):
        from mq135 import calibrate
        import random
        rng=random.Random(11);baseline=[{'sequence':i+1,'mq_mv':1100+rng.uniform(-2,2),'flags':7} for i in range(24)]
        profile=calibrate(baseline,{'circuit_v':5.,'divider_factor':3.,'rl_kohm':None},420,'assumed','Referencia ficticia: demostración visual.',conditioned=True)
        rows=[]
        for i in range(450):
            rows.append({'sequence':i+1,'elapsed_ms':(i+1)*2500,'uptime_ms':2000000+i*2500,'temperature':20.1+.002*i+.14*math.sin(i/23),
                'humidity':55+.023*i+.6*math.sin(i/30),'mq_adc':1900+i//5,'mq_mv':1100+i*.35,'mq_min':1800,'mq_max':2100,'flags':7})
        self.meta={'house':'DEMO · HOGAR 01','target':720,'duration_ms':1800000,'mq_profile':profile,'demo':True}
        self.house.setText('DEMO');self.update_state({'rows':rows,'meta':self.meta,'message':'Demostración · ninguna medición real','error':''})
        _,self.current_result=summarize(rows,self.meta)
        self.count.setText('450 de 720 lecturas simuladas · no guardadas como medición');self.connection.setText('●  Datos simulados');self.notice.setText('Vista de demostración. No se ha conectado ni iniciado una sesión en la ESP32.');self.start.setText('Iniciar visita real')
    def closeEvent(self,event):
        if self.worker and self.worker.isRunning():
            QMessageBox.information(self,'Visita en curso','Pulsa Terminar y espera a que los datos queden guardados antes de cerrar.');event.ignore()
        elif self.chat_worker and self.chat_worker.isRunning():
            self.stop_chat();QMessageBox.information(self,'Respuesta en curso','Estamos deteniendo la respuesta. Cierra la ventana cuando termine.');event.ignore()
        elif self.checker and self.checker.isRunning():event.ignore()
        else:event.accept()

def main():
    app=QApplication(sys.argv);app.setApplicationName('ItAI');app.setWindowIcon(QIcon(str(ASSETS/'icon.png')));app.setStyleSheet(STYLE)
    window=Window(demo='--demo' in sys.argv);window.show()
    if '--screenshot' in sys.argv:
        i=sys.argv.index('--screenshot');path=sys.argv[i+1]
        def snap():window.grab().save(path);app.quit()
        QTimer.singleShot(700,snap)
    sys.exit(app.exec())

if __name__=='__main__':main()
