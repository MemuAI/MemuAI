import hashlib
import io
import json
import lzma
import os
from pathlib import Path
import tempfile
import unittest
import zlib
from unittest.mock import patch
from installer import payload

class Package(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
    def tearDown(self):self.temp.cleanup()

    def sources(self, data, count=3):
        sources=[]
        for i in range(count):
            folder=self.root/f'source-{i}';folder.mkdir(exist_ok=True)
            (folder/'shared.dll').write_bytes(data)
            sources.append((f'c{i}',folder,f'App{i}'))
        return sources

    def pack(self,sources,cache=None):
        stream=io.BytesIO();stream.write(b'STUB')
        index=payload.build_payload(sources,{},stream,preset=1,workers=2,cache_dir=cache)
        file=self.root/'setup.exe';file.write_bytes(stream.getvalue())
        return file,index

    def legacy(self, data, version=1, file_rows=None, block_size=None, block_csize=None):
        compressed=lzma.compress(data)
        index={'format':version,'meta':{},'dirs':[],
               'files':file_rows or [{'p':'App/file.bin','c':'app','s':len(data),'h':hashlib.sha256(data).hexdigest(),'b':0,'o':0}],
               'blocks':[{'o':0,'c':block_csize or len(compressed),'s':len(data) if block_size is None else block_size,'f':'plain'}]}
        encoded=zlib.compress(json.dumps(index).encode())
        path=self.root/'legacy.exe'
        path.write_bytes(b'STUB'+compressed+encoded+payload.TRAILER.pack(payload.MAGIC,len(compressed)+len(encoded),len(encoded),version,0,payload.MAGIC_END))
        return path

    def test_identical_libraries_are_stored_once_and_every_component_extracts(self):
        data=os.urandom(2*payload.CHUNK+91)
        path,index=self.pack(self.sources(data))
        self.assertEqual(index.meta['deduplicated_bytes'],2*len(data))
        self.assertEqual(index.meta['stored_size'],len(data))
        self.assertEqual(len({(e.block,e.offset) for e in index.files}),1)
        factory=payload.tempfile.SpooledTemporaryFile;spools=[]
        def tracked(*args,**kwargs):
            spool=factory(*args,**kwargs);spools.append(spool);return spool
        with patch.object(payload.tempfile,'SpooledTemporaryFile',tracked),payload.Payload(path) as package:
            files={entry.path:b''.join(chunks) for entry,chunks in package.iter_files(['c0','c1','c2'])}
        self.assertEqual(len(files),3);self.assertTrue(all(value==data for value in files.values()))
        self.assertTrue(any(getattr(spool,'_rolled',False) for spool in spools))
        self.assertTrue(all(spool.closed for spool in spools))
        with payload.Payload(path) as package:
            only={entry.path:b''.join(chunks) for entry,chunks in package.iter_files(['c2'])}
        self.assertEqual(only,{'App2/shared.dll':data})

    def test_empty_and_repeated_files_can_share_an_offset(self):
        sources=self.sources(b'',2)
        for _,folder,_ in sources:(folder/'second.dll').write_bytes(b'data')
        path,index=self.pack(sources)
        with payload.Payload(path) as package:
            result={entry.path:b''.join(chunks) for entry,chunks in package.iter_files(index.components())}
        self.assertEqual(len(result),4)
        self.assertEqual(result['App0/shared.dll'],b'');self.assertEqual(result['App1/second.dll'],b'data')

    def test_old_format_one_remains_readable_and_unknown_formats_are_refused(self):
        data=b'old payload'
        with payload.Payload(self.legacy(data)) as package:
            self.assertEqual(package.read_stub(),b'STUB')
            self.assertEqual(b''.join(next(package.iter_files(['app']))[1]),data)
        with self.assertRaises(payload.PayloadError):payload.Payload(self.legacy(data,version=99))

    def test_corrupt_cached_blocks_are_rebuilt_instead_of_shipped(self):
        sources=self.sources(b'correct data'*100,2);cache=self.root/'cache'
        self.pack(sources,cache)
        for file in cache.glob('*.xz'):file.write_bytes(b'bad cache')
        path,_=self.pack(sources,cache)
        with payload.Payload(path) as package:
            for entry,chunks in package.iter_files(['c0','c1']):self.assertEqual(b''.join(chunks),b'correct data'*100)

    def test_decoding_is_bounded_and_cancellation_does_not_report_corruption(self):
        path=self.legacy(b'x'*(10*payload.CHUNK))
        with payload.Payload(path) as package:
            chunks=list(package._block_stream(package.index.blocks[0]))
            self.assertLessEqual(max(map(len,chunks)),payload.CHUNK)
        cancelled=False
        path,index=self.pack(self.sources(b'x'*(3*payload.CHUNK),2))
        with payload.Payload(path) as package:
            stream=package.iter_files(index.components(),cancelled=lambda:cancelled)
            _,chunks=next(stream);first=next(chunks);self.assertLessEqual(len(first),payload.CHUNK)
            cancelled=True
            self.assertEqual(list(chunks),[]);self.assertEqual(list(stream),[])

    def test_unconsumed_entries_still_have_their_checksum_checked(self):
        path,index=self.pack(self.sources(b'body',2))
        with payload.Payload(path) as package:
            package.index.files[0].sha256='f'*64
            with self.assertRaises(payload.PayloadError):list(package.iter_files(index.components()))

    def test_malformed_index_types_and_ranges_raise_payload_error(self):
        base={'format':1,'meta':{},'files':[],'blocks':[],'dirs':[]}
        bad=[None,[],{**base,'format':True},{**base,'files':{}},{**base,'meta':[]},
             {**base,'dirs':[['app']]}, {**base,'blocks':[{'o':-1,'c':5,'s':0,'f':'plain'}]},
             {**base,'files':[{'p':'a','c':'app','s':0,'h':'invalid','b':0,'o':0}]}]
        for value in bad:
            with self.subTest(value=value),self.assertRaises(payload.PayloadError):payload.Index.from_bytes(zlib.compress(json.dumps(value).encode()))
        with self.assertRaises(payload.PayloadError):payload.Payload(self.legacy(b'data',block_csize=9999))
        with self.assertRaises(payload.PayloadError):payload.Payload(self.legacy(b'data',block_size=1))

    def test_windows_unsafe_names_and_noncanonical_paths_are_refused(self):
        for value in ['a/./b','CON','lpt1.txt','a/trailing.','a/trailing ','nul.dll','a/\x00b','a/one?','a/one|']:
            with self.subTest(value=value),self.assertRaises(payload.PayloadError):payload.safe_relpath(value)
        self.assertEqual(payload.safe_relpath('App/valid-file.dll'),'App/valid-file.dll')

if __name__=='__main__':unittest.main()
