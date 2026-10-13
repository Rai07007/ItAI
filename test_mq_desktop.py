import math
from pathlib import Path
import tempfile
import unittest
import numpy as np
from mq135 import calibrate,convert,normalized_rs
from analysis import summarize

CIRCUIT={'circuit_v':5.,'divider_factor':3.,'rl_kohm':None}
def baseline():return [{'sequence':i+1,'mq_mv':1100.,'flags':7} for i in range(24)]
def profile():return calibrate(baseline(),CIRCUIT,420,'assumed','Prueba unitaria: referencia supuesta',conditioned=True)

class ConversionTests(unittest.TestCase):
    def test_reference_maps_back_to_its_ppm(self):
        p=profile();self.assertAlmostEqual(convert(1100,p,7)['eco2'],420,places=8)
    def test_known_power_law(self):
        p=profile();target=1500
        norm=p['r0_over_rl']*(target/p['a'])**(1/p['b'])
        mv=1000*p['circuit_v']/(norm+1)/p['divider_factor']
        self.assertAlmostEqual(convert(mv,p,7)['eco2'],target,places=8)
    def test_gemini_ratio_conflict(self):
        self.assertAlmostEqual(116.602*3.6**-2.769,3.35972998,places=6)
    def test_wrong_divider_or_rail_no_fabricated_ppm(self):
        self.assertIsNone(convert(2000,profile(),7)['eco2'])
        self.assertIsNone(convert(0,profile(),7)['eco2'])
        self.assertIsNone(convert(1100,profile(),7|32)['eco2'])
        self.assertIsNone(convert(1100,profile(),7|16)['eco2'])
    def test_reference_requires_real_preparation_and_samples(self):
        for rows in [baseline()[:5],[dict(r,flags=7|16) for r in baseline()]]:
            with self.assertRaises(ValueError):calibrate(rows,CIRCUIT,420,'assumed','test',conditioned=True)
        with self.assertRaises(ValueError):calibrate(baseline(),CIRCUIT,420,'assumed','test',conditioned=False)
    def test_unstable_reference_rejected(self):
        rows=[dict(r,mq_mv=900 if i%2 else 1300) for i,r in enumerate(baseline())]
        with self.assertRaises(ValueError):calibrate(rows,CIRCUIT,420,'assumed','unstable test',conditioned=True)
    def test_rl_cancels_for_same_unchanged_circuit(self):
        p=profile();q={**p,'rl_kohm':1};r={**p,'rl_kohm':10}
        self.assertEqual(convert(1200,q,7)['eco2'],convert(1200,r,7)['eco2'])
    def test_profile_absent_stays_missing(self):self.assertIsNone(convert(1100,None,7)['eco2'])
    def test_profile_snapshot_analyzed(self):
        rows=[dict(sequence=i+1,elapsed_ms=(i+1)*2500,temperature=20.,humidity=50.,mq_adc=1800,mq_mv=1100+i*.1,flags=7|64) for i in range(100)]
        df,r=summarize(rows,{'house':'TEST','target':720,'mq_profile':profile()})
        self.assertTrue(r['eco2']['available']);self.assertEqual(r['variables']['eco2']['raw_n'],100)
        self.assertTrue(r['variables']['eco2']['inference']['available'])
        self.assertAlmostEqual(df.eco2.iloc[0],420,places=6)


class DesktopCaptureTests(unittest.TestCase):
    def test_native_worker_recovers_all_rows(self):
        from unittest.mock import patch
        from urllib.parse import urlparse,parse_qs
        import urllib.error
        import json
        from desktop import Job
        from itai import connect_db,read_rows
        from test_itai import row
        with tempfile.TemporaryDirectory() as td:
            device={'id':'','lost':False}
            records=[row(i) for i in range(720)]
            def fake(base,path,key='',data=None,timeout=5):
                if path=='/start':device['id']=data['id']
                if path in ('/start','/status'):
                    return {'protocol':2,'boot_id':42,'session_id':device['id'],'count':720 if device['id'] else 0,'active':False}
                after=int(parse_qs(urlparse(path).query)['after'][0])
                if after==32 and not device['lost']:device['lost']=True;raise urllib.error.URLError('simulated disconnection')
                return {'session_id':device['id'],'boot_id':42,'samples':records[after:after+32]}
            done=[];errors=[];job=Job('TEST_NATIVE',model='none');job.completed.connect(lambda folder,result:done.append((folder,result)));job.failed.connect(errors.append)
            with patch('desktop.ROOT',Path(td)),patch('desktop.request',side_effect=fake),patch.object(Job,'msleep'):
                job.run()
            self.assertEqual(errors,[]);self.assertEqual(len(done),1)
            folder=Path(done[0][0]);db=connect_db(folder);self.assertEqual(len(read_rows(db)),720);db.close()
            self.assertEqual(json.loads((folder/'sesion.json').read_text())['completion'],'completa')
            self.assertTrue(device['lost'])
    def test_pending_buffer_not_overwritten(self):
        from unittest.mock import patch
        from desktop import Job
        with tempfile.TemporaryDirectory() as td:
            request_mock=lambda *a,**k:{'protocol':2,'boot_id':42,'session_id':'unsaved','count':20,'active':False}
            errors=[];job=Job('TEST',model='none');job.failed.connect(errors.append)
            with patch('desktop.ROOT',Path(td)),patch('desktop.request',side_effect=request_mock) as req:
                job.run()
            self.assertEqual(req.call_count,1);self.assertIn('pendientes',errors[0])

if __name__=='__main__':unittest.main()
