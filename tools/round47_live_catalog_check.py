"""Real pinned installer + Free model discovery; isolated data and no generation."""
from __future__ import annotations
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


async def main():
    with tempfile.TemporaryDirectory(prefix="round47-runtime-",dir=ROOT/"build") as data:
        os.environ["MEMUAI_DATA_DIR"]=str(Path(data)/"data")
        from app.opencode_runtime import GatewayRuntime
        from app import opencode_runtime
        from app.providers.opencode import OpenCodeProvider
        from app.providers import http
        from app.state import Account
        runtime=GatewayRuntime(Path(data)/"gateway")
        try:
            runtime.install()
            for _ in range(180):
                state=await asyncio.to_thread(runtime.status)
                if not state["installing"]:break
                await asyncio.sleep(.5)
            assert state["installed"],state
            account=Account("opencode",{"mode":"free"},id="round47-live-test")
            with patch.object(opencode_runtime,"runtime",runtime):
                models=await OpenCodeProvider().dynamic_models(account)
                assert models
            result={"version":state["version"],"installed":True,"models":models,
                    "running_during_check":runtime.status()["running"],"license_present":bool(list(Path(data).glob("gateway/*/LICENSE"))),
                    "generation_performed":False}
        finally:
            await asyncio.to_thread(runtime.shutdown)
            await http.close_all()
        result["running_after_shutdown"]=runtime.status()["running"]
        result["temporary_credentials_remaining"]=bool(list(Path(data).glob("gateway/runs/*/config.json")))
        assert not result["running_after_shutdown"] and not result["temporary_credentials_remaining"]
        (ROOT/"build/round47-live-catalog.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
        print(json.dumps(result,ensure_ascii=False))


if __name__=="__main__":asyncio.run(main())
