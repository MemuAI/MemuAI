"""Install the actual payload over a synthetic 3.8.2 install, using a fake registry.

All files are under a generated build directory. No UI, real shortcuts,
registry writes or installed applications are launched or modified.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from app.version import APP_VERSION
from installer.engine import Engine, Options, SetupError
from installer.payload import Payload
from installer import products
from tests.installer_engine_test import FakePlatform, make_setup

def main():
    build=(ROOT/'build').resolve()
    setup=ROOT/'installer'/f'MemuAI-Setup-{APP_VERSION}.exe'
    with tempfile.TemporaryDirectory(prefix='v4-upgrade-',dir=build) as temporary:
        case=Path(temporary).resolve()
        assert case.parent==build and case.name.startswith('v4-upgrade-')
        app=case/'Installed';platform=FakePlatform(case/'platform')
        options=Options(str(app),products.SUITE.component_ids(),[],'en',launch=False)
        with Payload(make_setup(case,'3.8.2')) as old:
            previous=Options(str(app),['proxy','memuai','memucode'],[],'en',launch=False)
            assert Engine(products.SUITE,platform,old).install(previous)['ok']
        (app/'personal-note.txt').write_text('Keep this file',encoding='utf-8')
        data=Path(platform.env('APPDATA'))/'MemuAI'/'memuai'/'chats.json'
        data.parent.mkdir(parents=True,exist_ok=True);data.write_text('{"test":"preserved"}',encoding='utf-8')
        old_state=(app/'install.json').read_bytes()
        helper=None
        if os.name=='nt':
            from installer.winplat import WindowsPlatform
            platform.directory_users=WindowsPlatform().directory_users
            ready=case/'ready';release=case/'release'
            script="from pathlib import Path;import sys,time;Path(sys.argv[1]).write_text('ready');"+'\nwhile not Path(sys.argv[2]).exists():time.sleep(.1)'
            helper=subprocess.Popen([sys.executable,'-c',script,str(ready),str(release)],cwd=app/'MemuAI',creationflags=subprocess.CREATE_NO_WINDOW)
            deadline=time.monotonic()+20
            while not ready.exists() and time.monotonic()<deadline:time.sleep(.1)
            assert ready.exists(),'CWD helper failed'
        try:
            with Payload(setup) as current:
                upgrade=Engine(products.SUITE,platform,current)
                assert upgrade.detect().version=='3.8.2'
                assert upgrade.install_kind(upgrade.detect())=='upgrade'
                if helper:
                    try:upgrade.install(options,force_close=True)
                    except SetupError as exc:assert exc.code=='directory_in_use',exc
                    else:raise AssertionError('External CWD holder not detected')
                    assert helper.poll() is None,'External process was terminated'
                    assert (app/'install.json').read_bytes()==old_state
                    assert not list(app.glob('.~stage-*'))
                    release.touch();helper.wait(timeout=20)
                    print('PASS native Windows CWD lock: blocked before extraction, old install preserved',flush=True)
                assert upgrade.install(options)['ok']
            state=json.loads((app/'install.json').read_text('utf-8'))
            assert state['version']==APP_VERSION
            for component in products.SUITE.components:
                exe=Path(component.exe.replace('\\','/'))
                installed=app/exe
                original=ROOT/component.source/exe.name
                assert hashlib.sha256(installed.read_bytes()).digest()==hashlib.sha256(original.read_bytes()).digest(),component.id
            assert data.read_text('utf-8')=='{"test":"preserved"}'
            assert (app/'personal-note.txt').read_text('utf-8')=='Keep this file'
            assert not platform.launched
            print('PASS actual full payload upgrade 3.8.2 -> '+APP_VERSION+': all five program EXEs match dist; user files/data preserved; fake registry only',flush=True)

            entry=next(c for c in products.SUITE.components if c.id=='memuai')
            executable=app/Path(entry.exe.replace('\\','/'))
            executable.write_bytes(executable.read_bytes()+b'OLD-LOCAL-BUILD')
            with Payload(setup) as current:
                reinstall=Engine(products.SUITE,platform,current)
                assert reinstall.install_kind(reinstall.detect())=='reinstall'
                assert reinstall.install(options)['ok']
            source=ROOT/entry.source/executable.name
            assert hashlib.sha256(executable.read_bytes()).digest()==hashlib.sha256(source.read_bytes()).digest()
            assert data.read_text('utf-8')=='{"test":"preserved"}'
            assert (app/'personal-note.txt').read_text('utf-8')=='Keep this file'
            assert not platform.launched
            print('PASS same-version '+APP_VERSION+' reinstall replaces older owned program files and preserves user data',flush=True)
        finally:
            if helper:
                release.touch();helper.wait(timeout=20)

if __name__=='__main__':main()
