import json
from pathlib import Path
import tempfile
import unittest
import numpy as np
from analysis import classify, inference, summarize, report
from itai import connect_db, save_batch, read_rows, validate_llm_order, collect
from unittest.mock import patch
from types import SimpleNamespace
import urllib.error


def row(i,**kw):
    r=dict(sequence=i+1,elapsed_ms=(i+1)*2500,uptime_ms=2000000+(i+1)*2500,temperature=20+i*.001,
        humidity=50+i*.01,mq_adc=1200+i,mq_mv=1000,mq_min=1190+i,mq_max=1210+i,flags=7)
    r.update(kw);return r

class AnalysisTests(unittest.TestCase):
    def test_paper_boundaries(self):
        for value,label in [(15.9,'riesgo por frío'),(16,'advertencia'),(18,'aceptable'),(26,'aceptable'),(32,'advertencia'),(32.1,'riesgo por calor')]:
            self.assertEqual(classify('temperature',value),label)
        for value,label in [(19.9,'fuera del intervalo del paper'),(20,'advertencia por resequedad'),(30,'óptimo'),(60,'óptimo'),(70,'advertencia por exceso'),(90,'humedad elevada'),(90.1,'fuera del intervalo del paper')]:
            self.assertEqual(classify('humidity',value),label)
        self.assertEqual(classify('eco2',999),'aceptable');self.assertEqual(classify('eco2',1000),'advertencia')
        self.assertEqual(classify('eco2',2000),'advertencia');self.assertEqual(classify('eco2',2001),'riesgo')
    def test_hac_recovers_known_trend(self):
        rng=np.random.default_rng(5);x=np.arange(720)/24;y=18+.1*x+rng.normal(0,.15,720)
        result=inference(x,y)
        self.assertTrue(result['available']);self.assertAlmostEqual(result['slope_per_minute'],.1,delta=.005)
        self.assertLess(result['p_slope'],.001)
    def test_missing_not_concatenated(self):
        x=np.arange(100)/24;y=x.copy();y[40:60]=np.nan
        self.assertFalse(inference(x,y)['available'])
    def test_constant_not_significant(self):
        self.assertFalse(inference(np.arange(100)/24,np.ones(100))['available'])
    def test_flags_preserved_and_excluded(self):
        rows=[row(i) for i in range(100)]
        rows[0]=row(0,flags=7|16);rows[1]=row(1,flags=7|64);rows[2]=row(2,flags=7|512,humidity=88)
        df,result=summarize(rows,{'house':'TEST','target':720})
        self.assertEqual(result['variables']['humidity']['raw_n'],100)
        self.assertEqual(result['variables']['humidity']['analysis_n'],99)
        self.assertEqual(result['variables']['mq_adc']['analysis_n'],98)
        self.assertFalse(result['eco2']['available'])
    def test_sqlite_duplicates_resume_conflict(self):
        with tempfile.TemporaryDirectory() as td:
            db=connect_db(Path(td));save_batch(db,[row(0),row(1)]);save_batch(db,[row(1)]);db.close()
            db=connect_db(Path(td));self.assertEqual(len(read_rows(db)),2)
            with self.assertRaises(ValueError):save_batch(db,[row(1,temperature=99)])
            db.close()
    def test_report_all_invalid_no_nan(self):
        with tempfile.TemporaryDirectory() as td:
            rows=[row(i,temperature=None,humidity=None,flags=4|16|64) for i in range(3)]
            result=report(rows,{'house':'TEST','target':720},td)
            self.assertIsNone(result['environmental_fraction']['percent'])
            json.loads((Path(td)/'analisis.json').read_text(encoding='utf-8'),parse_constant=lambda s:self.fail(s))
            self.assertTrue((Path(td)/'series.png').is_file())

    def test_llm_cannot_publish_invented_text(self):
        catalogue={'fact':'MQ: ADC relativo','scope':'No evaluado'}
        self.assertEqual(validate_llm_order('{"order":["scope","fact"]}',catalogue),['scope','fact'])
        self.assertEqual(validate_llm_order('{"order":["fact","fact"]}',catalogue),['fact','scope'])
        self.assertEqual(validate_llm_order('{"order":["fact"]}',catalogue),['fact','scope'])
        for value in ['{"order":["fact","inventado"]}','{"order":[]}']:
            with self.assertRaises(ValueError):validate_llm_order(value,catalogue)

    def test_full_capture_recovers_network_interruption(self):
        from urllib.parse import urlparse,parse_qs
        with tempfile.TemporaryDirectory() as td:
            device={'id':'','lost':False}
            readings=[row(i) for i in range(720)]
            def fake_request(base,path,key='',data=None,timeout=5):
                if path=='/start':device['id']=data['id']
                status={'protocol':2,'boot_id':123,'session_id':device['id'],'active':False,'count':720,'target':720}
                if path in ('/status','/start'):return status
                query=parse_qs(urlparse(path).query);after=int(query['after'][0])
                if after==32 and not device['lost']:
                    device['lost']=True;raise urllib.error.URLError('WiFi interrumpido en ensayo')
                return {'session_id':device['id'],'boot_id':123,'samples':readings[after:after+32]}
            args=SimpleNamespace(resume=None,house='TEST',device='http://fake',key='key',no_browser=True,port=8765,model='none')
            with patch('itai.ROOT',Path(td)),patch('itai.request',side_effect=fake_request),patch('itai.time.sleep'):
                collect(args)
            folder=next((Path(td)/'mediciones').iterdir())
            db=connect_db(folder);self.assertEqual(len(read_rows(db)),720);db.close()
            self.assertEqual(json.loads((folder/'sesion.json').read_text(encoding='utf-8'))['completion'],'completa')
            self.assertTrue(device['lost'])
            import hashlib
            hashes=json.loads((folder/'sha256.json').read_text(encoding='utf-8'))
            for name,expected in hashes.items():self.assertEqual(hashlib.sha256((folder/name).read_bytes()).hexdigest(),expected)

if __name__=='__main__':unittest.main()
