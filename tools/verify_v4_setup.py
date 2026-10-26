"""Inspect the actual Setup stub and UI extracted from the final installer."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import pefile
from PyInstaller.archive.readers import CArchiveReader
from app.version import APP_VERSION, version_tuple
from installer.payload import Payload
from tools.verify_v4_components import signature

def main():
    setup=ROOT/'installer'/f'MemuAI-Setup-{APP_VERSION}.exe'
    with pefile.PE(str(setup),fast_load=True) as pe:
        pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY['IMAGE_DIRECTORY_ENTRY_RESOURCE']])
        info=pe.VS_FIXEDFILEINFO[0]
        assert (info.FileVersionMS>>16,info.FileVersionMS&65535,info.FileVersionLS>>16,info.FileVersionLS&65535)==version_tuple()
    with Payload(setup) as container:
        stub=container.read_stub()
        assert container.meta['version']==APP_VERSION
    build=(ROOT/'build').resolve()
    with tempfile.TemporaryDirectory(prefix='v4-stub-proof-',dir=build) as temporary:
        case=Path(temporary).resolve();assert case.parent==build and case.name.startswith('v4-stub-proof-')
        path=case/'stub.exe';path.write_bytes(stub)
        archive=CArchiveReader(str(path));pyz=archive.open_embedded_archive('PYZ.pyz')
        modules=['installer.main','installer.relaunch','installer.engine','installer.winplat','installer.bridge','installer.payload','installer.products']
        for module in modules:
            source=ROOT/(module.replace('.','/')+'.py')
            assert signature(pyz.extract(module))==signature(compile(source.read_bytes(),str(source),'exec',dont_inherit=True)),module
        count=0
        for source in (ROOT/'installer/ui').rglob('*'):
            if not source.is_file() or source.name=='mock.js' or '__pycache__' in source.parts:continue
            key='installer_ui/'+source.relative_to(ROOT/'installer/ui').as_posix()
            stored=next((name for name in archive.toc if name.replace('\\','/')==key),None)
            assert stored is not None,key
            assert archive.extract(stored)==source.read_bytes(),key
            count+=1
        assert not any(name.replace('\\','/').endswith('installer_ui/mock.js') for name in archive.toc)
        assert 'psutil' in pyz.toc
        assert any('psutil' in name and name.endswith('.pyd') for name in archive.toc)
    result={'version':APP_VERSION,'modules':modules,'ui_files':count,'stub_sha256':hashlib.sha256(stub).hexdigest()}
    (ROOT/'build/v4-setup-proof.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print('PASS final Setup PE, actual embedded installer code/UI, and CWD detection dependency')

if __name__=='__main__':main()
