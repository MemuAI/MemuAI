"""Read-only proof of 4.0 versions, shipped code and feature assets."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import sys
import types

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import build
import pefile
from PyInstaller.archive.readers import CArchiveReader

def signature(code):
    return (code.co_code,tuple(signature(v) if isinstance(v,types.CodeType) else v for v in code.co_consts),
            code.co_names,code.co_varnames,code.co_freevars,code.co_cellvars,code.co_argcount,
            code.co_posonlyargcount,code.co_kwonlyargcount,code.co_flags,code.co_exceptiontable,code.co_linetable)

def verify():
    result={"version":build.app_version(),"executables":[],"code":{},"assets":{}}
    for key,target in build.TARGETS.items():
        if key=='proxy_only':continue
        name=target['name'];path=ROOT/'dist'/name/(name+'.exe')
        with pefile.PE(str(path),fast_load=True) as pe:
            pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY['IMAGE_DIRECTORY_ENTRY_RESOURCE']])
            info=pe.VS_FIXEDFILEINFO[0]
            for major,minor in [(info.FileVersionMS,info.FileVersionLS),(info.ProductVersionMS,info.ProductVersionLS)]:
                assert (major>>16,major&65535,minor>>16,minor&65535)==build.version_tuple(build.app_version()),name
        result['executables'].append({'name':name,'bytes':path.stat().st_size})
    features=['app.teams','app.team_runtime','app.teams_api','app.memudb','app.memudb_duckdb','app.memudb_api','app.module_catalog','app.module_catalog_api','app.modules_host','app.model_contract','app.schedule_runtime']
    groups={'MemuAI':['app.version','app.pgdata','app.playground_api','app.canvas','app.model_catalog','app.model_capabilities','app.model_catalog_api',*features],
            'MemuAIProxy':['app.version','app.admin_api','app.canvas','app.model_catalog','app.model_capabilities',*features],
            'MemuNote':['app.version','app.model_contract','app.memunote.ai','app.memunote.slides_api','app.memunote.server'],
            'MemuCode':['app.version','app.cli.main'],'MemuFiles':['app.version','app.memufiles.server'],'memu':['app.version']}
    for name,modules in groups.items():
        folder=ROOT/'dist'/name;package=folder/(name+'.pkg')
        pyz=CArchiveReader(str(package if package.is_file() else folder/(name+'.exe'))).open_embedded_archive('PYZ.pyz')
        for module in modules:
            assert module in pyz.toc,(name,module,'missing')
            source=ROOT/(module.replace('.','/')+'.py')
            expected=compile(source.read_bytes(),str(source),'exec',dont_inherit=True)
            assert signature(pyz.extract(module))==signature(expected),(name,module,'stale code')
        result['code'][name]=modules
        if name in {'MemuAI','MemuAIProxy'}:
            assert 'duckdb' in pyz.toc,(name,'DuckDB Python API missing')
            assert list((folder/'_internal').rglob('_duckdb*.pyd')),(name,'DuckDB native engine missing')
        if name in {'MemuNote','MemuFiles','MemuCode'}:
            assert not list((folder/'_internal').rglob('_duckdb*.pyd')),(name,'Unused DuckDB native engine bundled')
    for name,relative in [('MemuAI','app/pgstatic'),('MemuAI','app/module_builtins'),('MemuAI','app/docs'),
                          ('MemuAI','app/memunote/static/js/slides'),('MemuAI','app/memunote/static/slides.css'),
                          ('MemuAI','app/module_catalog_seed.json'),('MemuAIProxy','app/static'),('MemuAIProxy','app/pgstatic'),
                          ('MemuAIProxy','app/module_builtins'),('MemuAIProxy','app/module_catalog_seed.json'),
                          ('MemuNote','app/memunote/static'),('MemuCode','app/docs')]:
        source=ROOT/relative;packaged=ROOT/'dist'/name/'_internal'/relative
        files=[source] if source.is_file() else [p for p in source.rglob('*') if p.is_file() and '__pycache__' not in p.parts]
        for path in files:
            target=packaged if source.is_file() else packaged/path.relative_to(source)
            assert target.is_file(),str(target)
            assert hashlib.sha256(path.read_bytes()).digest()==hashlib.sha256(target.read_bytes()).digest(),str(target)+' stale asset'
        result['assets'][name+'/'+relative]=len(files)
    source=ROOT/'dist/memu/memu.exe';embedded=ROOT/'dist/MemuAI/MemuCLI/memu.exe'
    assert source.read_bytes()==embedded.read_bytes(),'Embedded memu CLI differs'
    (ROOT/'build/v4-components-proof.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False))

if __name__=='__main__':verify()
