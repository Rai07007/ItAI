import json
import unittest
from unittest.mock import patch
from llm_runtime import SMALL_MODEL,generation_options,inference_slot
from local_chat import light_messages,chat_stream,visit_context
from analysis import summarize
from test_itai import row

class SmallModelTests(unittest.TestCase):
    def test_short_context_keeps_units_and_evidence_without_identity(self):
        _,result=summarize([row(i,humidity=72) for i in range(720)],{'house':'PRIVATE_HOUSE','target':720})
        history=[{'role':'system','content':'bad'},{'role':'assistant','content':'x'*3000}]
        messages=light_messages(visit_context(result),history,'¿Qué debería revisar?')
        value=json.dumps(messages,ensure_ascii=False)
        self.assertNotIn('PRIVATE_HOUSE',value);self.assertNotIn('bad',value)
        self.assertIn('Humedad',value);self.assertIn('estimados',value)
        self.assertLess(len(value),10000)
        self.assertLessEqual(len(messages[1]['content']),400)
    def test_cpu_limits_and_unload_in_request(self):
        payload=[]
        class Response:
            def __enter__(self):return iter([b'{"message":{"content":"Hola"}}',b'{"done":true}'])
            def __exit__(self,*args):pass
        def fake(req,timeout):payload.append(json.loads(req.data));return Response()
        with patch('local_chat.select_model',return_value=SMALL_MODEL),patch('local_chat.urllib.request.urlopen',side_effect=fake):
            self.assertEqual(''.join(chat_stream({},[],'Hola',SMALL_MODEL)),'Hola')
        self.assertEqual(payload[0]['keep_alive'],0)
        self.assertEqual(payload[0]['options']['num_ctx'],4096)
        self.assertEqual(payload[0]['options']['num_thread'],2)
        self.assertEqual(payload[0]['options']['num_gpu'],0)
    def test_cancelled_wait_and_exception_release(self):
        with inference_slot() as first:
            self.assertTrue(first)
            with inference_slot(lambda:True) as second:self.assertFalse(second)
        with self.assertRaises(RuntimeError):
            with inference_slot():raise RuntimeError('test')
        with inference_slot() as acquired:self.assertTrue(acquired)
    def test_long_question_is_not_silently_truncated(self):
        with self.assertRaises(ValueError):light_messages({},[],'x'*1201)
    def test_large_model_profile_unchanged(self):
        self.assertEqual(generation_options('llama3:latest',chat=True)['num_ctx'],8192)

if __name__=='__main__':unittest.main()
