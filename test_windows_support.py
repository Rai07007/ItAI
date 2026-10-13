import json
from pathlib import Path
import tempfile
import unittest
from runtime_paths import data_root
from equipment import validate_transfer,import_transfer
from test_mq_desktop import profile

class WindowsSupportTests(unittest.TestCase):
    def test_user_data_is_not_in_program_files(self):
        result=data_root(platform='win32',environ={'LOCALAPPDATA':'C:/Users/Prueba/AppData/Local'},home='C:/Users/Prueba',source='C:/Program Files/ItAI')
        self.assertEqual(result,Path('C:/Users/Prueba/AppData/Local/ItAI'))
    def test_source_mac_keeps_existing_data_location(self):
        self.assertEqual(data_root(platform='darwin',environ={},source='/existing/itai'),Path('/existing/itai'))
    def test_import_preserves_profile_and_previous_configuration(self):
        with tempfile.TemporaryDirectory(prefix='itai_ñ_') as td:
            root=Path(td);(root/'equipment.json').write_text('{"api_key":"previous"}',encoding='utf-8')
            p=profile();source=root/'transfer.json'
            source.write_text(json.dumps({'format':'itai-equipment-v1','api_key':'test-key','mq_profile':p,'circuit_settings':{'circuit_v':5,'divider_factor':3}}),encoding='utf-8')
            import_transfer(source,root)
            self.assertEqual(json.loads((root/'mq_reference.json').read_text(encoding='utf-8')),p)
            previous=next((root/'equipment-backups').glob('*/equipment.json'))
            self.assertEqual(json.loads(previous.read_text(encoding='utf-8'))['api_key'],'previous')
    def test_invalid_import_does_not_replace_configuration(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);(root/'equipment.json').write_text('{"api_key":"previous"}',encoding='utf-8')
            p=profile();p['r0_over_rl']*=2
            source=root/'bad.json';source.write_text(json.dumps({'format':'itai-equipment-v1','api_key':'new','mq_profile':p}),encoding='utf-8')
            with self.assertRaises(ValueError):import_transfer(source,root)
            self.assertEqual(json.loads((root/'equipment.json').read_text(encoding='utf-8'))['api_key'],'previous')
            self.assertFalse((root/'mq_reference.json').exists())
    def test_header_injection_rejected(self):
        with self.assertRaises(ValueError):validate_transfer({'format':'itai-equipment-v1','api_key':'abc\r\nX-Header: value'})

if __name__=='__main__':unittest.main()
