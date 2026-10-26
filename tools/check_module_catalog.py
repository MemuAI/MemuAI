"""Validate catalogue packages offline; --write refreshes checksums and bundled seed.

Run from any directory. No module JavaScript is executed and no user data is read.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import module_catalog as catalog, modules_host as host

def check(*, write: bool = False, root: Path = ROOT) -> dict:
    folder = root / "module-catalog"
    index = json.loads((folder / "index.json").read_text(encoding="utf-8"))
    packages = {}
    for entry in index.get("modules", []):
        path = folder / str(entry.get("package", {}).get("path", ""))
        if not path.resolve().is_relative_to(folder.resolve()) or path.suffix != ".json" or path.is_symlink():
            raise ValueError("Package path must stay inside module-catalog: " + str(path))
        raw = path.read_bytes()
        if write:
            entry["package"].update(sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw))
        elif entry["package"].get("sha256") != hashlib.sha256(raw).hexdigest() or entry["package"].get("bytes") != len(raw):
            raise ValueError("Checksum/size mismatch: " + str(path))
        packages[entry["package"]["path"]] = raw.decode("utf-8")
    normalized = catalog.normalize_index(index, catalog.DEFAULT_SOURCE)
    with tempfile.TemporaryDirectory(prefix="memu-catalog-validate-") as temporary:
        package_root = Path(temporary)
        for entry in normalized["modules"]:
            payload = json.loads(packages[entry["package"]["path"]])
            catalog.validate_package(payload, entry)
            created = host.import_package(package_root, payload)
            if created["id"] != entry["id"]:
                raise ValueError("Importer changed the package ID")
    seed = {"schema": 1, "index": index, "packages": packages}
    seed_path = root / "app" / "module_catalog_seed.json"
    if write:
        (folder / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        seed_path.write_text(json.dumps(seed, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    elif json.loads(seed_path.read_text(encoding="utf-8")) != seed:
        raise ValueError("Bundled seed is stale; run tools/check_module_catalog.py --write")
    return {"modules": len(normalized["modules"]), "bytes": sum(len(v.encode("utf-8")) for v in packages.values())}

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="Refresh hashes, sizes and the bundled fallback")
    args = parser.parse_args()
    print(json.dumps(check(write=args.write)))
