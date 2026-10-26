"""Catalogue integrity, safe installs, updates, rollback and stale actions; no network."""
from __future__ import annotations
import asyncio
import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app import module_catalog as catalog, modules_host as host

class CatalogTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory(prefix="memu-catalog-test-")
        self.root=Path(self.temporary.name)
        seed=catalog._seed()
        self.index=copy.deepcopy(seed["index"])
        self.raw=seed["packages"][self.index["modules"][0]["package"]["path"]].encode()
        self.entry=self.index["modules"][0]
        self.calls=[]

    def tearDown(self):
        self.temporary.cleanup()

    async def fetch(self,url,limit):
        self.calls.append(url)
        return json.dumps(self.index,ensure_ascii=False).encode() if url.endswith("index.json") else self.raw

    async def listing(self,refresh=False):
        return await catalog.catalogue(self.root,refresh=refresh,fetch=self.fetch)

    async def install(self,replace=False):
        return await catalog.install(self.root,self.entry["id"],self.entry["package"]["sha256"],catalog.DEFAULT_SOURCE,fetch=self.fetch,replace_local=replace)

    def update(self,version="1.1.0"):
        payload=json.loads(self.raw)
        payload["manifest"]["version"]=version
        payload["files"]["main.js"] += "\n// updated source\n"
        self.raw=json.dumps(payload,ensure_ascii=False).encode()
        self.entry["version"]=version
        self.entry["package"].update(bytes=len(self.raw),sha256=hashlib.sha256(self.raw).hexdigest())

    async def test_install_update_rollback_preserves_user_data_and_settings(self):
        await self.listing()
        result=await self.install()
        self.assertTrue(result["disabled"])
        module_id=self.entry["id"]
        self.assertFalse(host.load_package(self.root,module_id)["enabled"])
        self.assertIn("ru",host.load_package(self.root,module_id)["i18n"])
        host.save_persistent(self.root,module_id,{"tasks":[{"text":"keep me","done":False}]})
        host.configure_module(self.root,module_id,enabled=True,hidden=True,slot="composer")
        before=(self.root/"persist"/(module_id+".json")).read_bytes()
        self.update()
        await self.listing(True)
        self.assertEqual((await self.listing())["modules"][0]["status"],"update")
        result=await self.install()
        self.assertTrue(result["backup"])
        row=next(row for row in host.list_modules(self.root) if row["id"]==module_id)
        self.assertFalse(row["enabled"])
        self.assertTrue(row["launcher"]["hidden"])
        self.assertEqual(row["launcher"]["slot"],"composer")
        self.assertEqual((self.root/"persist"/(module_id+".json")).read_bytes(),before)
        self.assertEqual(host.load_package(self.root,module_id)["version"],"1.1.0")
        catalog.rollback(self.root,module_id)
        self.assertEqual(host.load_package(self.root,module_id)["version"],"1.0.0")
        self.assertEqual((self.root/"persist"/(module_id+".json")).read_bytes(),before)

    async def test_checksum_and_stale_source_reject_without_installing(self):
        await self.listing()
        self.raw+=b" "
        with self.assertRaisesRegex(catalog.CatalogError,"SHA-256"):
            await self.install()
        self.assertFalse((self.root/"custom"/self.entry["id"]).exists())
        catalog.set_source(self.root,"https://example.com/index.json")
        with self.assertRaisesRegex(catalog.CatalogError,"source changed"):
            await self.install()

    async def test_local_edit_needs_explicit_replacement_and_is_retained(self):
        await self.install()
        code=self.root/"custom"/self.entry["id"]/"main.js"
        code.write_text("// local custom code",encoding="utf-8")
        self.update()
        await self.listing(True)
        self.assertTrue((await self.listing())["modules"][0]["local_modified"])
        with self.assertRaisesRegex(catalog.CatalogError,"local edits"):
            await self.install()
        await self.install(True)
        self.assertTrue(any((folder/"main.js").read_text(encoding="utf-8")=="// local custom code" for folder in catalog._backups(self.root,self.entry["id"])))

    async def test_conflicting_local_module_is_never_replaced(self):
        host.import_package(self.root,json.loads(self.raw))
        self.assertEqual((await self.listing())["modules"][0]["status"],"conflict")
        with self.assertRaisesRegex(catalog.CatalogError,"cannot be replaced"):
            await self.install(True)

    async def test_offline_seed_is_installable_and_custom_source_has_no_seed(self):
        async def offline(*args):raise OSError("offline fixture")
        listing=await catalog.catalogue(self.root,fetch=offline)
        self.assertEqual(listing["mode"],"bundled")
        await catalog.install(self.root,self.entry["id"],self.entry["package"]["sha256"],catalog.DEFAULT_SOURCE,fetch=offline)
        catalog.set_source(self.root,"https://example.com/index.json")
        with self.assertRaises(catalog.CatalogError):
            await catalog.catalogue(self.root,fetch=offline)

    async def test_permission_mismatch_and_unsafe_paths(self):
        normalized=catalog.normalize_index(self.index,catalog.DEFAULT_SOURCE)["modules"][0]
        for permissions in [["storage","chat"],[{}],"storage"]:
            payload=json.loads(self.raw);payload["manifest"]["permissions"]=permissions
            with self.assertRaises(catalog.CatalogError):catalog.validate_package(payload,normalized)
        for path in ["../escape.json","https://evil.example/package.json","packages/%2e%2e/escape.json","packages\\escape.json"]:
            with self.assertRaises(catalog.CatalogError):catalog._package_url(catalog.DEFAULT_SOURCE,path)
        for url in ["http://example.com/index.json","https://user:pass@example.com/a.json","https://localhost/a.json","https://example.com:8443/a.json"]:
            with self.assertRaises(catalog.CatalogError):catalog._url(url)
        self.assertEqual(catalog._url("https://github.com/MemuAI/MemuAI/blob/main/module-catalog/index.json"),catalog.DEFAULT_SOURCE)

    async def test_stale_profile_guard_prevents_commit(self):
        await self.listing()
        def stale():raise catalog.CatalogError("profile changed",409)
        with self.assertRaises(catalog.CatalogError):
            await catalog.install(self.root,self.entry["id"],self.entry["package"]["sha256"],catalog.DEFAULT_SOURCE,fetch=self.fetch,guard=stale)
        self.assertFalse((self.root/"custom"/self.entry["id"]).exists())

    async def test_prepare_is_valid_and_excludes_saved_values(self):
        await self.install()
        host.save_persistent(self.root,self.entry["id"],{"tasks":[{"text":"PRIVATE-SAVED-VALUE"}]})
        result=catalog.contribution(self.root,self.entry["id"],{"author":"Example author","license":"MIT","tags":"tasks, offline"})
        self.assertNotIn("PRIVATE-SAVED-VALUE",result["package_text"])
        entry=catalog.normalize_index({"schema":1,"modules":[result["entry"]]},catalog.DEFAULT_SOURCE)["modules"][0]
        raw=result["package_text"].encode()
        self.assertEqual(entry["package"]["sha256"],hashlib.sha256(raw).hexdigest())
        catalog.validate_package(json.loads(raw),entry)

    async def test_interrupted_update_is_recovered_without_resurrecting_deleted_modules(self):
        await self.install()
        module_id=self.entry["id"]
        live=self.root/"custom"/module_id
        backup_root=self.root/"catalog-backups"/module_id
        backup_root.mkdir(parents=True)
        name="0000000000001-"+"a"*32
        backup=backup_root/name
        os.replace(live,backup)
        pending=catalog._pending(self.root,module_id)
        pending.parent.mkdir(parents=True,exist_ok=True)
        pending.write_text(json.dumps({"id":module_id,"backup":name,"sha256":"f"*64,"was_enabled":True}),encoding="utf-8")
        catalog.recover_pending(self.root)
        self.assertTrue(live.is_dir())
        self.assertTrue(host.load_package(self.root,module_id)["enabled"])
        self.assertFalse(pending.exists())

        os.replace(live,backup)
        catalog.recover_pending(self.root)
        self.assertFalse(live.exists())

    async def test_failed_swap_restores_code_enabled_state_and_data(self):
        await self.install()
        module_id=self.entry["id"]
        live=self.root/"custom"/module_id
        host.configure_module(self.root,module_id,enabled=True)
        before=(live/"main.js").read_bytes()
        self.update();await self.listing(True)
        replace=os.replace
        def fail_staged(source,target):
            if ".catalog-stage-" in str(source) and str(target)==str(live):
                raise OSError("simulated interrupted filesystem operation")
            return replace(source,target)
        with patch("app.module_catalog.os.replace",side_effect=fail_staged):
            with self.assertRaises(OSError):await self.install()
        self.assertEqual((live/"main.js").read_bytes(),before)
        self.assertTrue(host.load_package(self.root,module_id)["enabled"])

    def test_repository_and_seed_are_consistent(self):
        from tools.check_module_catalog import check
        self.assertGreater(check()["modules"],0)

if __name__=="__main__":unittest.main()
