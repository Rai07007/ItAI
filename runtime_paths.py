"""Keep user data outside an installed/frozen application's resources."""
import os
import sys
from pathlib import Path

ASSETS = Path(__file__).resolve().parent

def data_root(platform=None, environ=None, home=None, source=None):
    platform = platform or sys.platform
    environ = os.environ if environ is None else environ
    home = Path.home() if home is None else Path(home)
    if environ.get('ITAI_DATA_DIR'):
        return Path(environ['ITAI_DATA_DIR']).expanduser().resolve()
    if platform == 'win32':
        return Path(environ.get('LOCALAPPDATA') or home/'AppData'/'Local')/'ItAI'
    # Preserve the existing Mac source distribution's storage layout.
    if not getattr(sys, 'frozen', False):
        return Path(source) if source else ASSETS
    if platform == 'darwin':
        return home/'Library'/'Application Support'/'ItAI'
    return Path(environ.get('XDG_DATA_HOME') or home/'.local'/'share')/'ItAI'

DATA_ROOT = data_root()
DATA_ROOT.mkdir(parents=True, exist_ok=True)
