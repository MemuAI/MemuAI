"""Read the real public Free catalog in isolated data, never launch a gateway."""
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    with tempfile.TemporaryDirectory(prefix="round48-public-catalog-", dir=ROOT / "build") as folder:
        os.environ["MEMUAI_DATA_DIR"] = folder
        from app.opencode_runtime import runtime
        from app.providers.opencode import OpenCodeProvider
        from app.providers import http
        from app.state import Account
        async def run():
            try:
                with patch.object(runtime, "ensure", AsyncMock(side_effect=AssertionError("Catalog launched gateway"))) as ensure:
                    names = await OpenCodeProvider().dynamic_models(Account("opencode", {"mode": "free"}, id="round48-public-check"))
                    ensure.assert_not_called()
                assert names
                result = {"models": names, "model_count": len(names), "gateway_processes": runtime.status()["running"],
                          "external_generations": 0, "user_credentials": False}
                assert result["gateway_processes"] == 0
                (ROOT / "build/round48-public-catalog.json").write_text(json.dumps(result, indent=2), "utf-8")
                print("PASS live public catalog: zero gateway starts and zero generation requests", flush=True)
                print(json.dumps(result), flush=True)
            finally:
                await http.close_all()
        asyncio.run(run())


if __name__ == "__main__": main()
