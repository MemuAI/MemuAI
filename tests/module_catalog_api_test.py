"""HTTP contracts for the catalogue, with temporary stores and no network."""
from __future__ import annotations
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import httpx
from fastapi import FastAPI
from app import module_catalog as catalog, module_catalog_api as api

class CatalogAPI(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary=tempfile.TemporaryDirectory(prefix="memu-catalog-api-")
        self.root=Path(self.temporary.name)
        self.profile="test-profile"
        self.profiles=patch.object(api.pgdata,"load_profiles",side_effect=lambda:{"active_id":self.profile})
        self.profiles.start()
        self.seed=catalog._seed()
        async def fetch(url,limit):
            if url.endswith("index.json"):return json.dumps(self.seed["index"],ensure_ascii=False).encode()
            return next(iter(self.seed["packages"].values())).encode()
        app=FastAPI()
        api.register(app,root_fn=lambda:self.root,fetch=fetch)
        self.client=httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://test")

    async def asyncTearDown(self):
        await self.client.aclose()
        self.profiles.stop()
        self.temporary.cleanup()

    async def test_listing_install_authoring_export_and_error_contract(self):
        listing=(await self.client.get("/api/pg/module-catalog?profile_id=test-profile")).json()
        self.assertEqual(listing["profile_id"],self.profile)
        row=listing["modules"][0]
        installed=await self.client.post("/api/pg/module-catalog/install",json={"id":row["id"],"source":listing["source"],"sha256":row["package"]["sha256"],"profile_id":self.profile})
        self.assertEqual(installed.status_code,200,installed.text)
        self.assertTrue(installed.json()["disabled"])
        authoring=(await self.client.get("/api/pg/module-catalog/authoring?profile_id=test-profile")).json()
        self.assertEqual([v["id"] for v in authoring["modules"]],[row["id"]])
        exported=await self.client.post("/api/pg/module-catalog/prepare",json={"id":row["id"],"author":"Test","license":"MIT","profile_id":self.profile})
        self.assertEqual(exported.status_code,200,exported.text)
        self.assertTrue(exported.json()["package_text"])
        rollback=await self.client.post("/api/pg/module-catalog/rollback",json={"id":row["id"],"profile_id":self.profile})
        self.assertEqual(rollback.status_code,404)
        self.assertIn("error",rollback.json())

    async def test_stale_profiles_invalid_json_and_oversized_body_reject(self):
        self.assertEqual((await self.client.get("/api/pg/module-catalog?profile_id=old")).status_code,409)
        response=await self.client.post("/api/pg/module-catalog/source",json={"profile_id":"old","source":"https://example.com/index.json"})
        self.assertEqual(response.status_code,409)
        self.assertFalse((self.root/"catalog-settings.json").exists())
        self.assertEqual((await self.client.post("/api/pg/module-catalog/source",content="{")).status_code,400)
        self.assertEqual((await self.client.post("/api/pg/module-catalog/source",json=[])).status_code,400)
        self.assertEqual((await self.client.post("/api/pg/module-catalog/source",content=b" "*70000)).status_code,413)

if __name__=="__main__":unittest.main()
