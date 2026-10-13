import json
import unittest
from unittest.mock import patch
import numpy as np
from insights import weighted_events
from analysis import summarize
from local_chat import visit_context,build_messages,select_model,chat_stream
from test_itai import row

class InsightTests(unittest.TestCase):
    def test_gap_and_invalid_reading_break_episodes(self):
        e=weighted_events([2500,5000,7500,15000,17500],[65,65,np.nan,65,65],lambda x:x>60)
        self.assertEqual(e['episode_count'],2)
        self.assertEqual(e['outside_n'],4)
        self.assertAlmostEqual(e['estimated_minutes'],10/60)
        self.assertAlmostEqual(e['longest_episode_minutes'],5/60)
    def test_thresholds_and_full_visit_duration(self):
        e=weighted_events(np.arange(1,721)*2500,np.full(720,70.),lambda x:x>60)
        self.assertEqual(e['estimated_minutes'],30)
        self.assertEqual(e['episode_count'],1)
        e=weighted_events([2500,5000],[60,70],lambda x:x>70)
        self.assertEqual(e['outside_n'],0)
    def test_excluded_data_cannot_trigger_action(self):
        _,r=summarize([row(i,temperature=10,humidity=75,flags=4) for i in range(720)],{'target':720,'house':'PRIVATE'})
        ids={p['id'] for p in r['insights']['action_plan']}
        self.assertFalse(ids & {'cold','moisture','combined'})
        self.assertIn('quality_temperature',ids)
    def test_contextual_plan_and_first_last(self):
        _,r=summarize([row(i,temperature=15+i*.001,humidity=72) for i in range(720)],{'target':720,'house':'PRIVATE','demo':True})
        ids={p['id'] for p in r['insights']['action_plan']}
        self.assertTrue({'cold','moisture','combined'}<=ids)
        firstlast=r['insights']['details']['temperature']['first_last']
        self.assertEqual(firstlast['first_n'],120);self.assertEqual(firstlast['last_n'],120)
        self.assertAlmostEqual(firstlast['difference'],.6)
        c=visit_context(r);self.assertNotIn('PRIVATE',json.dumps(c));self.assertTrue(c['simulated'])
        self.assertNotIn('episodes',c['events']['cold'])
        json.dumps(c,allow_nan=False)

class ChatTests(unittest.TestCase):
    def test_role_filter_and_history_budget(self):
        history=[{'role':'assistant','content':'x'*12000} for _ in range(30)]+[{'role':'system','content':'untrusted'}]
        messages=build_messages({},history,'Hola')
        self.assertEqual(sum(m['role']=='system' for m in messages),1)
        self.assertLessEqual(sum(len(m['content']) for m in messages[1:-1]),6000)
        self.assertEqual(messages[-1],{'role':'user','content':'Hola'})
    def test_only_installed_local_model(self):
        with patch('local_chat.request',return_value={'models':[{'name':'remote','remote_host':'https://cloud'},{'name':'local:latest'}]}):
            self.assertEqual(select_model('auto'),'local:latest')
            with self.assertRaises(ValueError):select_model('remote')
    def test_stream_completion_and_premature_end(self):
        class Response:
            def __init__(self,packets):self.packets=packets
            def __enter__(self):return iter(json.dumps(p).encode() for p in self.packets)
            def __exit__(self,*args):pass
        with patch('local_chat.select_model',return_value='local'),patch('local_chat.urllib.request.urlopen',return_value=Response([{'message':{'content':'Hola'}},{'done':True}])):
            self.assertEqual(''.join(chat_stream({},[],'Hola')),'Hola')
        with patch('local_chat.select_model',return_value='local'),patch('local_chat.urllib.request.urlopen',return_value=Response([])):
            with self.assertRaises(RuntimeError):list(chat_stream({},[],'Hola'))
    def test_no_visit_context(self):self.assertFalse(visit_context(None)['visit_available'])


class DesktopChatTests(unittest.TestCase):
    def test_stream_display_and_visit_isolation(self):
        from desktop import QApplication,Window
        app=QApplication.instance() or QApplication([])
        w=Window(demo=True)
        def fake(context,history,question,model,cancelled):
            self.assertTrue(context['simulated'])
            yield 'Hola. ';yield 'Esta visita es simulada.'
        with patch('desktop.chat_stream',fake):
            w.chat_input.setText('¿Qué muestran estos datos?');w.send_chat_question()
            self.assertTrue(w.chat_worker.wait(3000));app.processEvents()
        self.assertEqual(w.chat_history[-1]['content'],'Hola. Esta visita es simulada.')
        self.assertIn('simulada',w.chat_view.toPlainText())
        old=w.chat_token
        w.update_state({'meta':{**w.meta,'house':'OTHER','session_id':'another'},'rows':[], 'message':'Otra visita','error':''})
        w.chat_chunk(old,'STALE');w.chat_done(old,'STALE')
        self.assertEqual(w.chat_history,[]);self.assertIsNone(w.current_result)
        self.assertNotIn('STALE',w.chat_view.toPlainText())
        w.controls(True);self.assertFalse(w.open_visit_btn.isEnabled())
        w.controls(False);w.close()

if __name__=='__main__':unittest.main()
