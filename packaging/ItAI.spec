# Build on Windows x64. A folder bundle keeps shared-library notices accessible.
from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules,copy_metadata
root=Path(SPECPATH).parent
metadata=[]
for name in ['numpy','scipy','pandas','matplotlib','statsmodels','PySide6-Essentials','shiboken6']:
    metadata += copy_metadata(name)
a=Analysis([str(root/'windows_entry.py')],pathex=[str(root)],
    binaries=[],datas=[(str(root/'icon.png'),'.'),(str(root/'WINDOWS.md'),'.')]+metadata,
    hiddenimports=collect_submodules('statsmodels'),
    hookspath=[],hooksconfig={'matplotlib':{'backends':['Agg']}},runtime_hooks=[],
    excludes=['PyQt5','PyQt6','PySide2','tkinter','IPython','pytest'],noarchive=False)
pyz=PYZ(a.pure)
exe=EXE(pyz,a.scripts,[],exclude_binaries=True,name='ItAI',debug=False,
    bootloader_ignore_signals=False,strip=False,upx=False,console=False,
    icon=str(root/'icon.png'))
coll=COLLECT(exe,a.binaries,a.datas,strip=False,upx=False,name='ItAI')
