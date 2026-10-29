"""Catalogue prices keep model variants, vendor IDs and tiny positive rates."""
import unittest
from unittest.mock import patch

from app import model_capabilities as caps


class ModelCatalogIdentity(unittest.TestCase):
    def test_positive_rates_are_never_rounded_into_free_prices(self):
        self.assertGreater(caps._price("0.00000001"), 0)
        self.assertGreater(caps._per_million("0.00000000000001"), 0)
        self.assertIsNone(caps._per_million("1e308"))
        self.assertIsNone(caps._num(10 ** 1000))

    def test_partial_modality_lists_use_compact_output_fallback(self):
        result = caps.normalize_openrouter({"id": "m", "architecture": {"input_modalities": ["text", "image"], "modality": "text->image"},
                                            "pricing": {"overrides": True}})
        self.assertEqual(result["in"], ["text", "image"])
        self.assertEqual(result["out"], ["image"])

    def test_malformed_models_dev_metadata_does_not_discard_other_models(self):
        for value in (True, 1, "unknown", {}, None):
            with self.subTest(value=value):
                result = caps.normalize_models_dev("fixture", {}, {"id": "model", "reasoning_options": value})
                self.assertEqual(result["reasoning_efforts"], [])
        for models in ([], True, 1, "unknown", None):
            rows = caps.models_dev_rows({"bad": {"models": models}, "good": {"models": {"m": {"id": "model"}}}})
            self.assertEqual([row["id"] for row in rows], ["good/model"])

    def test_paid_free_extended_and_exact_vendor_ids_keep_their_prices(self):
        rows = [caps.normalize_openrouter({"id": identity, "pricing": {"prompt": str(price / 1e6), "completion": str(price / 1e6)}})
                for identity, price in (("a/model", 1), ("a/model:free", 0), ("a/model:extended", 5), ("other/model", 8))]
        catalog = caps._Catalog()
        with patch.object(caps, "_read_json", return_value={"rows": rows}), patch.object(caps, "_module_rows", return_value=[]), patch.object(caps, "_mtime", return_value=1):
            catalog.ensure()
        for identity, price in (("a/model", 1), ("a/model:free", 0), ("a/model:extended", 5), ("other/model", 8), ("model:free", 0)):
            with self.subTest(identity=identity):
                self.assertEqual(catalog.match(identity)["openrouter"][0]["price"]["input"], price)
        self.assertEqual(catalog.match("model:unlisted"), {})


if __name__ == "__main__":
    unittest.main()
