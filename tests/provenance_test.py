"""Local, offline provenance scan: pure byte-level heuristics, no network.

Builds small PNG/JPEG-shaped fixtures by hand (no Pillow dependency) so the
tests stay fast and self-contained.
"""
from __future__ import annotations

import asyncio
import base64
import struct
import unittest
import zlib
from unittest.mock import patch

from app import provenance as p


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)


def _make_png(text_chunks=()) -> bytes:
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", 8, 8, 8, 6, 0, 0, 0)
    out = sig + _png_chunk(b"IHDR", ihdr)
    for keyword, text in text_chunks:
        out += _png_chunk(b"tEXt", keyword.encode("latin-1") + b"\x00" + text.encode("latin-1"))
    out += _png_chunk(b"IEND", b"")
    return out


class ProvenanceScanTests(unittest.TestCase):
    def test_stable_diffusion_style_parameters_are_found(self) -> None:
        png = _make_png([("parameters", "a photo of a cat\nSteps: 20, Sampler: Euler a, Seed: 12345")])
        result = p.scan(png, "cat.png")
        self.assertEqual(result["format"], "PNG")
        self.assertTrue(result["signals"]["generation_parameters_found"])
        self.assertIn("Stable Diffusion", result["claimed_generator"])

    def test_a_plain_png_with_no_metadata_finds_nothing(self) -> None:
        result = p.scan(_make_png([]), "plain.png")
        self.assertEqual(result["claimed_generator"], "")
        self.assertEqual(result["findings"], [])
        self.assertFalse(result["signals"]["c2pa_jumbf_hint"])

    def test_a_named_generator_in_png_text_is_recognised(self) -> None:
        result = p.scan(_make_png([("Software", "OpenAI gpt-image-1")]), "gen.png")
        self.assertEqual(result["claimed_generator"], "OpenAI gpt-image")

    def test_c2pa_jumbf_markers_in_a_jpeg_app11_segment_are_flagged(self) -> None:
        payload = b"jumb" + b"c2pa.manifest" + b"claim_generator: OpenAI gpt-image-1 c2pa.assertions urn:c2pa:foo"
        segment = b"\xff\xeb" + struct.pack(">H", len(payload) + 2) + payload
        jpeg = b"\xff\xd8" + segment + b"\xff\xd9"
        result = p.scan(jpeg, "img.jpg")
        self.assertEqual(result["format"], "JPEG")
        self.assertTrue(result["signals"]["c2pa_jumbf_hint"])
        self.assertIn("OpenAI", result["claimed_generator"])

    def test_iptc_digital_source_type_is_extracted_from_raw_xmp_text(self) -> None:
        xmp = (b"http://ns.adobe.com/xap/1.0/\x00<?xpacket begin?><rdf:RDF>Iptc4xmpExt:DigitalSourceType>"
               b"http://cv.iptc.org/newscodes/digitalsourcetype/trainedAlgorithmicMedia</x>")
        jpeg = b"\xff\xd8\xff\xe1" + struct.pack(">H", len(xmp) + 2) + xmp + b"\xff\xd9"
        result = p.scan(jpeg, "img2.jpg")
        self.assertEqual(result["signals"]["iptc_digital_source_type"], "trainedAlgorithmicMedia")
        self.assertTrue(result["ai_digital_source_type"])

    def test_pixel_bytes_do_not_count_as_metadata_or_credentials(self) -> None:
        png = _make_png([])
        suspicious_pixels = b"C2PA c2pa.manifest OpenAI gpt-image trainedAlgorithmicMedia"
        image = png[:-12] + _png_chunk(b"IDAT", suspicious_pixels) + png[-12:]
        result = p.scan(image, "pixels.png")
        self.assertFalse(result["signals"]["c2pa_jumbf_hint"])
        self.assertEqual(result["claimed_generator"], "")
        self.assertFalse(result["ai_digital_source_type"])

    def test_compressed_png_text_is_decoded_with_a_size_limit(self) -> None:
        png = _make_png([])
        payload = b"parameters\x00\x00" + zlib.compress(b"Steps: 20, Seed: 123")
        image = png[:-12] + _png_chunk(b"zTXt", payload) + png[-12:]
        self.assertTrue(p.scan(image)["signals"]["generation_parameters_found"])
        bomb = png[:-12] + _png_chunk(b"zTXt", b"parameters\x00\x00" + zlib.compress(b"x" * 70_000)) + png[-12:]
        self.assertFalse(p.scan(bomb)["signals"]["generation_parameters_found"])

    def test_invalid_png_chunk_is_not_scanned(self) -> None:
        png = _make_png([("Software", "OpenAI gpt-image-1")])
        broken = bytearray(png)
        broken[-17] ^= 1
        self.assertEqual(p.scan(bytes(broken))["claimed_generator"], "")

    def test_webp_metadata_and_c2pa_chunk_are_read(self) -> None:
        def chunk(kind, payload):
            return kind + struct.pack("<I", len(payload)) + payload + (b"\x00" if len(payload) & 1 else b"")

        body = b"WEBP" + chunk(b"XMP ", b"OpenAI gpt-image") + chunk(b"C2PA", b"manifest")
        webp = b"RIFF" + struct.pack("<I", len(body)) + body
        result = p.scan(webp, "image.webp")
        self.assertEqual(result["format"], "WEBP")
        self.assertEqual(result["claimed_generator"], "OpenAI gpt-image")
        self.assertTrue(result["signals"]["c2pa_jumbf_hint"])

    def test_bytes_after_image_end_are_not_scanned(self) -> None:
        c2pa = b"C2PA" + struct.pack("<I", 8) + b"manifest"
        webp_body = b"WEBP"
        webp = b"RIFF" + struct.pack("<I", len(webp_body)) + webp_body + c2pa
        self.assertFalse(p.scan(webp)["signals"]["c2pa_jumbf_hint"])
        app11 = b"\xff\xeb" + struct.pack(">H", 12) + b"jumb c2pa "
        jpeg = b"\xff\xd8\xff\xd9" + app11
        self.assertFalse(p.scan(jpeg)["signals"]["c2pa_jumbf_hint"])

    def test_result_always_carries_the_honesty_caveats_and_portals(self) -> None:
        result = p.scan(b"not an image at all", "x.bin", language="en")
        self.assertEqual(result["format"], "unknown")
        self.assertGreaterEqual(len(result["caveats"]), 3)
        self.assertTrue(any("proves nothing" in c or "ничего не доказывает" in c for c in result["caveats"]))
        self.assertTrue(any("synthid" in c.lower() for c in result["caveats"]))
        self.assertTrue(result["portals"])

    def test_russian_language_selects_russian_caveats(self) -> None:
        result = p.scan(_make_png([]), "x.png", language="ru")
        self.assertIn("ничего не доказывает", " ".join(result["caveats"]))

    def test_scan_never_raises_on_garbage_input(self) -> None:
        for blob in (b"", b"\x00\x01\x02", b"\xff\xd8\xff\xeb\xff\xff" + b"\x00" * 5, os_urandom(256)):
            result = p.scan(blob, "junk")
            self.assertIn("format", result)


def os_urandom(n: int) -> bytes:
    import os
    return os.urandom(n)


class ProvenanceRouteTests(unittest.TestCase):
    def test_upload_requires_valid_base64_data_url(self) -> None:
        from app import proxy_tools_api as api

        async def invoke(data):
            with patch.object(api, "_auth", return_value=None), patch.object(
                api, "_json_object", return_value=({"data": data}, None)
            ):
                return await api.detector_provenance(object())

        bad = asyncio.run(invoke("data:image/png;base64,%%%"))
        self.assertEqual(bad.status_code, 400)
        good = asyncio.run(invoke("data:image/png;base64," + base64.b64encode(_make_png()).decode()))
        self.assertEqual(good["format"], "PNG")


if __name__ == "__main__":
    unittest.main()
