from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("MEMUAI_DATA_DIR", str(Path(tempfile.gettempdir()) / "memu-round60-catalog-unused"))
from app import module_catalog as catalog
from tests import module_catalog_test as baseline, module_catalog_api_test as api_baseline

class PackageInspection(unittest.IsolatedAsyncioTestCase):
    setUp=baseline.CatalogTest.setUp
    tearDown=baseline.CatalogTest.tearDown
    fetch=baseline.CatalogTest.fetch
    listing=baseline.CatalogTest.listing
    install=baseline.CatalogTest.install
    async def test_preview_verifies_all_files_without_installing_or_importing(self):
        with patch.object(catalog.host, 'import_package', side_effect=AssertionError('inspection may not import code')):
            value=await catalog.preview(self.root,self.entry['id'],self.entry['package']['sha256'],catalog.DEFAULT_SOURCE,fetch=self.fetch)
        self.assertIn('manifest.json',value['files'])
        self.assertIn('main.js',value['files'])
        self.assertEqual(value['package_text'].encode(),self.raw)
        self.assertEqual(value['sha256'],hashlib.sha256(self.raw).hexdigest())
        self.assertFalse((self.root/'custom').exists())
        self.assertFalse((self.root/'state.json').exists())

    async def test_preview_rejects_stale_checksum_malformed_json_and_size_mismatch(self):
        with self.assertRaises(catalog.CatalogError):
            await catalog.preview(self.root,self.entry['id'],'0'*64,catalog.DEFAULT_SOURCE,fetch=self.fetch)
        await self.listing()
        self.raw+=b' '
        with self.assertRaisesRegex(catalog.CatalogError,'SHA-256'):
            await catalog.preview(self.root,self.entry['id'],self.entry['package']['sha256'],catalog.DEFAULT_SOURCE,fetch=self.fetch)
        self.assertFalse((self.root/'custom').exists())
        self.raw=b'['*1500+b'0'+b']'*1500
        self.entry['package'].update(sha256=hashlib.sha256(self.raw).hexdigest(),bytes=len(self.raw))
        await self.listing(True)
        with self.assertRaises(catalog.CatalogError):
            await catalog.preview(self.root,self.entry['id'],self.entry['package']['sha256'],catalog.DEFAULT_SOURCE,fetch=self.fetch)

    async def test_downloaded_preview_preserves_utf8_bom_and_verified_checksum(self):
        self.raw=b'\xef\xbb\xbf'+self.raw
        self.entry['package'].update(sha256=hashlib.sha256(self.raw).hexdigest(),bytes=len(self.raw))
        value=await catalog.preview(self.root,self.entry['id'],self.entry['package']['sha256'],catalog.DEFAULT_SOURCE,fetch=self.fetch)
        self.assertEqual(value['package_text'].encode('utf-8'),self.raw)

    async def test_preview_allows_reading_a_module_that_requires_a_future_permission(self):
        value=json.loads(self.raw)
        value['manifest']['permissions'].append('future-access')
        self.raw=json.dumps(value,ensure_ascii=False).encode()
        self.entry['permissions']=value['manifest']['permissions']
        self.entry['package'].update(sha256=hashlib.sha256(self.raw).hexdigest(),bytes=len(self.raw))
        result=await catalog.preview(self.root,self.entry['id'],self.entry['package']['sha256'],catalog.DEFAULT_SOURCE,fetch=self.fetch)
        self.assertIn('future-access',result['files']['manifest.json'])
        with self.assertRaises(catalog.CatalogError):await self.install()

    async def test_source_switch_during_preview_discards_downloaded_content(self):
        async def fetch(url,limit):
            data=await self.fetch(url,limit)
            if not url.endswith('index.json'):catalog.set_source(self.root,'https://example.com/index.json')
            return data
        with self.assertRaisesRegex(catalog.CatalogError,'source changed'):
            await catalog.preview(self.root,self.entry['id'],self.entry['package']['sha256'],catalog.DEFAULT_SOURCE,fetch=fetch)

    async def test_corrupt_cache_timestamp_or_mode_is_refetched(self):
        for checked,mode in [('not-a-time','remote'),(float('inf'),'remote'),(10**20,'remote'),(1,None)]:
            with self.subTest(checked=checked,mode=mode):
                (self.root/'catalog-cache.json').write_text(json.dumps({'source':catalog.DEFAULT_SOURCE,'index':self.index,'checked':checked,'mode':mode}),encoding='utf-8')
                self.calls.clear();await self.listing()
                self.assertIn(catalog.DEFAULT_SOURCE,self.calls)

    async def test_cached_listing_still_checks_profile_guard(self):
        await self.listing()
        def guard():raise catalog.CatalogError('stale profile',409)
        with self.assertRaisesRegex(catalog.CatalogError,'stale profile'):
            await catalog.catalogue(self.root,fetch=self.fetch,guard=guard)

    def test_source_guard_blocks_write_and_invalid_url_is_not_silently_truncated(self):
        def guard():raise catalog.CatalogError('stale profile',409)
        with self.assertRaises(catalog.CatalogError):catalog.set_source(self.root,'https://example.com/index.json',guard=guard)
        self.assertFalse((self.root/'catalog-settings.json').exists())
        for source in ['https://127.0.0.1/index.json','https://[::1]/index.json','https://example.com/'+('a'*2100)]:
            with self.subTest(source=source[:40]),self.assertRaises(catalog.CatalogError):catalog._url(source)
        self.assertEqual(catalog._url('https://[2606:4700:4700::1111]/index.json'),'https://[2606:4700:4700::1111]/index.json')

    def test_oversized_checksum_and_conflicting_contribution_declarations_reject(self):
        entry=copy.deepcopy(self.index)
        entry['modules'][0]['package']['sha256']+='x'
        with self.assertRaises(catalog.CatalogError):catalog.normalize_index(entry,catalog.DEFAULT_SOURCE)
        payload=json.loads(self.raw)
        payload['contributes']={}
        payload['manifest']['contributes']={'skills':[]}
        normalized=catalog.normalize_index(self.index,catalog.DEFAULT_SOURCE)['modules'][0]
        with self.assertRaisesRegex(catalog.CatalogError,'declarations disagree'):
            catalog.validate_package(payload,normalized)

    def test_schema_and_app_requirement_are_not_coerced_or_truncated(self):
        for version in [True,1.0,'1']:
            bad=copy.deepcopy(self.index);bad['schema']=version
            with self.subTest(schema=version),self.assertRaises(catalog.CatalogError):catalog.normalize_index(bad,catalog.DEFAULT_SOURCE)
        bad=copy.deepcopy(self.index);bad['modules'][0]['min_app_version']='4.'+'0'*40
        with self.assertRaises(catalog.CatalogError):catalog.normalize_index(bad,catalog.DEFAULT_SOURCE)
        for permissions in [['future-'+('x'*200)],['chat']*1000]:
            bad=copy.deepcopy(self.index);bad['modules'][0]['permissions']=permissions
            with self.subTest(permissions=len(permissions)),self.assertRaises(catalog.CatalogError):catalog.normalize_index(bad,catalog.DEFAULT_SOURCE)

    async def test_corrupt_local_fingerprint_is_reported_as_modified(self):
        await self.install()
        folder=self.root/'custom'/self.entry['id']
        marker=catalog._manifest_marker(folder)
        marker['files']['main.js']='damaged fingerprint'
        (folder/'.catalog.json').write_text(json.dumps(marker),encoding='utf-8')
        self.assertTrue((await self.listing())['modules'][0]['local_modified'])

class InspectionAPI(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=api_baseline.CatalogAPI.asyncSetUp
    asyncTearDown=api_baseline.CatalogAPI.asyncTearDown
    async def test_source_view_endpoint_returns_validated_text_without_installation(self):
        listing=(await self.client.get('/api/pg/module-catalog?profile_id='+self.profile)).json()
        row=listing['modules'][0]
        response=await self.client.post('/api/pg/module-catalog/preview',json={'id':row['id'],'sha256':row['package']['sha256'],'source':listing['source'],'profile_id':self.profile})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['profile_id'],self.profile)
        self.assertIn('main.js',response.json()['files'])
        self.assertFalse((self.root/'custom').exists())
        recursive=await self.client.post('/api/pg/module-catalog/preview',content=b'['*1500+b'0'+b']'*1500)
        self.assertEqual(recursive.status_code,400)
        self.profile='switched'
        self.assertEqual((await self.client.post('/api/pg/module-catalog/preview',json={'profile_id':'test-profile'})).status_code,409)

if __name__=='__main__':unittest.main()
