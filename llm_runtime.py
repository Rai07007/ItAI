"""Shared resource limits for local inference on small laptops."""
from contextlib import contextmanager
import threading

SMALL_MODEL='qwen2.5:0.5b'
_LOCK=threading.Lock()

def lightweight(model):return model.split('/')[-1]==SMALL_MODEL

def generation_options(model,chat=False):
    if lightweight(model):
        return {'temperature':.1 if chat else 0,'num_ctx':4096,'num_predict':256 if chat else 400,'num_thread':2,'num_gpu':0}
    return {'temperature':.15 if chat else 0,'num_predict':900 if chat else 500,**({'num_ctx':8192} if chat else {})}

@contextmanager
def inference_slot(cancelled=lambda:False):
    acquired=False
    try:
        while not acquired:
            if cancelled():yield False;return
            acquired=_LOCK.acquire(timeout=.2)
        if cancelled():yield False;return
        yield True
    finally:
        if acquired:_LOCK.release()
