"""Load each shipped DuckDB native engine with no site-packages or desktop UI."""
from pathlib import Path
import json
import os
import subprocess
import sys
import tempfile
import marshal
from PyInstaller.archive.readers import CArchiveReader

ROOT = Path(__file__).resolve().parents[1]
PROGRAM = r'''
import json, os, sys, marshal, importlib.abc, importlib.util
from pathlib import Path
folder, target, modules = map(Path, sys.argv[1:])
libraries = os.add_dll_directory(str(folder)) if os.name == 'nt' else None
sys.path.insert(0, str(folder))
manifest=json.loads((modules/'manifest.json').read_text())
class ShippedModules(importlib.abc.MetaPathFinder,importlib.abc.Loader):
    def find_spec(self,fullname,path=None,target=None):
        if fullname in manifest:
            return importlib.util.spec_from_loader(fullname,self,is_package=manifest[fullname])
    def create_module(self,spec):return None
    def exec_module(self,module):
        filename=modules/(module.__name__+'.code')
        module.__file__=str(filename)
        code=marshal.loads(filename.read_bytes())
        if code is not None:exec(code,module.__dict__)
sys.meta_path.insert(0,ShippedModules())
import _duckdb
import duckdb
from app.memudb_duckdb import DuckWorkspace
assert Path(_duckdb.__file__).resolve().is_relative_to(folder.resolve())
config={'enable_external_access':'false','autoload_known_extensions':'false',
        'autoinstall_known_extensions':'false','allow_community_extensions':'false'}
connection=duckdb.connect(str(target),config=config)
connection.execute('CREATE TABLE evidence(id INTEGER PRIMARY KEY, value DECIMAL(10,2))')
connection.execute('INSERT INTO evidence VALUES(1,42.50)')
connection.close()
connection=_duckdb.connect(str(target),read_only=True,config=config)
assert str(connection.execute('SELECT value FROM evidence WHERE id=1').fetchone()[0]) == '42.50'
try:
    connection.execute('DELETE FROM evidence')
except Exception:
    pass
else:
    raise AssertionError('Read-only connection accepted a write')
connection.close()
workspace=DuckWorkspace();owner=('packaged-test','memudb')
try:
    session=workspace.open(str(target),owner)['id']
    workspace.configure(session,owner,False,confirmed=True)
    preview=workspace.preview(session,owner,[{'sql':'UPDATE evidence SET value=51.25 WHERE id=1'}])
    assert workspace.query(session,owner,'SELECT value FROM evidence')['rows']==[['42.50']]
    workspace.apply(session,owner,preview['preview_id'],confirmed=True)
    assert workspace.query(session,owner,'SELECT value FROM evidence')['rows']==[['51.25']]
finally:workspace.close_all()
print(json.dumps({'version':_duckdb.__version__,'engine':str(Path(_duckdb.__file__).name),'read_write_and_readonly':True,'packaged_python_adapter_preview_apply':True}))
'''

def main():
    results = {}
    with tempfile.TemporaryDirectory(prefix='v4-duck-native-', dir=ROOT/'build') as folder:
        for app in ('MemuAI','MemuAIProxy'):
            internal = ROOT/'dist'/app/'_internal'
            source=ROOT/'dist'/app
            package=source/(app+'.pkg')
            archive=CArchiveReader(str(package if package.is_file() else source/(app+'.exe'))).open_embedded_archive('PYZ.pyz')
            modules=Path(folder)/app;modules.mkdir()
            manifest={}
            for name,info in archive.toc.items():
                if name in {'app','app.memudb','app.memudb_duckdb','duckdb'} or name.startswith('duckdb.'):
                    (modules/(name+'.code')).write_bytes(marshal.dumps(archive.extract(name)))
                    manifest[name]=bool(info[0])
            (modules/'manifest.json').write_text(json.dumps(manifest),encoding='utf-8')
            result = subprocess.run([sys.executable,'-I','-S','-c',PROGRAM,str(internal),str(Path(folder)/(app+'.duckdb')),str(modules)],
                cwd=folder,capture_output=True,text=True,timeout=45,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
            if result.returncode:
                raise RuntimeError(f'{app}: {result.stdout}\n{result.stderr}')
            results[app] = json.loads(result.stdout)
    (ROOT/'build/v4-duckdb-native-proof.json').write_text(json.dumps(results,indent=2),encoding='utf-8')
    print(json.dumps(results))

if __name__ == '__main__':
    main()
