"""Windows desktop entry point, including a network-free packaged smoke test."""
import json
import os
import sys
import traceback
from pathlib import Path
from runtime_paths import DATA_ROOT

def main():
    if sys.stdout is None or sys.stderr is None:
        log=(DATA_ROOT/'app.log').open('a',encoding='utf-8',buffering=1)
        sys.stdout=log;sys.stderr=log
    try:
        if '--self-test' in sys.argv:
            folder=Path(sys.argv[sys.argv.index('--self-test')+1]);folder.mkdir(parents=True,exist_ok=True)
            os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
            from desktop import QApplication,Window,STYLE
            from analysis import report
            from itai import connect_db,save_batch,read_rows
            from equipment import validate_transfer
            app=QApplication([]);app.setStyleSheet(STYLE);w=Window(demo=True)
            db=connect_db(folder);save_batch(db,w.rows);saved=read_rows(db);db.close()
            result=report(saved,w.meta,folder)
            assert result['quality']['received']==450
            assert result['variables']['temperature']['inference']['available']
            assert result['insights']['events']['humid']['outside_n']>0
            validate_transfer({'format':'itai-equipment-v1','api_key':'smoke-test-only','mq_profile':w.meta['mq_profile']})
            w.folder=folder;w.show_result(result);w.navigate(1);w.show();app.processEvents()
            assert w.grab().save(str(folder/'windows-results.png'))
            w.navigate(3);app.processEvents();assert w.grab().save(str(folder/'windows-setup.png'))
            assert not w.chat_history
            w.close()
            (folder/'passed.json').write_text(json.dumps({'status':'passed','simulated':True,'data_root':str(DATA_ROOT),'readings':len(saved),'checks':['Qt','SQLite','HAC','graphs','equipment-reference']}),encoding='utf-8')
            return 0
        from desktop import main as desktop_main
        desktop_main()
        return 0
    except Exception:
        error=traceback.format_exc();print(error,file=sys.stderr)
        (DATA_ROOT/'startup-error.txt').write_text(error,encoding='utf-8')
        if '--self-test' not in sys.argv and sys.platform=='win32':
            import ctypes
            ctypes.windll.user32.MessageBoxW(None,'ItAI no pudo abrirse. El detalle está en:\n'+str(DATA_ROOT/'startup-error.txt'),'ItAI',0x10)
        return 1

if __name__=='__main__':sys.exit(main())
