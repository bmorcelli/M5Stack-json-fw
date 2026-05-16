import struct
import unittest

from script.firmware_manifest import (
    analyze_remote_firmware,
    build_install_from_legacy,
    parse_esp_image_size,
    parse_partition_table,
)


class FakeResponse:
    def __init__(self, status_code, content=b"", headers=None):
        self.status_code = status_code
        self.content = content
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeSession:
    def __init__(self, content, accept_ranges=True, etag='"test-etag"'):
        self.content = content
        self.accept_ranges = accept_ranges
        self.etag = etag
        self.get_calls = []

    def head(self, url, allow_redirects=True, timeout=20):
        headers = {
            "Content-Length": str(len(self.content)),
            "ETag": self.etag,
        }
        if self.accept_ranges:
            headers["Accept-Ranges"] = "bytes"
        return FakeResponse(200, headers=headers)

    def get(self, url, headers=None, stream=True, timeout=20):
        range_header = (headers or {}).get("Range")
        self.get_calls.append(range_header)
        if range_header and range_header.startswith("bytes="):
            start_text, end_text = range_header[len("bytes="):].split("-", 1)
            start = int(start_text)
            end = int(end_text)
            return FakeResponse(206, self.content[start:end + 1], {
                "Content-Length": str(end - start + 1),
                "Content-Range": f"bytes {start}-{end}/{len(self.content)}",
            })
        return FakeResponse(200, self.content, {"Content-Length": str(len(self.content))})


class NotFoundSession:
    def head(self, url, allow_redirects=True, timeout=20):
        return FakeResponse(404)

    def get(self, url, headers=None, stream=True, timeout=20):
        return FakeResponse(404)


def esp_image(segment_data=b"ABCD", hash_appended=False):
    header = bytearray(8)
    header[0] = 0xE9
    header[1] = 1
    header[7] = 1 if hash_appended else 0
    image = bytes(header)
    image += struct.pack("<II", 0x3F400020, len(segment_data))
    image += segment_data
    checksum_offset = ((len(image) + 1 + 15) // 16) * 16 - 1
    image += b"\x00" * (checksum_offset - len(image))
    image += b"\xEF"
    if hash_appended:
        image += b"H" * 32
    return image


def partition_entry(type_id, subtype_id, offset, size, label):
    label_bytes = label.encode("ascii")[:16].ljust(16, b"\x00")
    return b"\xAA\x50" + bytes([type_id, subtype_id]) + struct.pack("<II", offset, size) + label_bytes + b"\x00" * 4


class FirmwareManifestTests(unittest.TestCase):
    def test_parse_esp_image_size(self):
        image = esp_image(b"12345678")
        size = parse_esp_image_size(lambda offset, length: image[offset:offset + length], 0)
        self.assertEqual(size, 48)

    def test_parse_partition_table(self):
        table = (
            partition_entry(0x00, 0x10, 0x10000, 0x200000, "ota_0")
            + partition_entry(0x01, 0x81, 0x210000, 0x100000, "sys")
            + b"\xFF" * 32
        )
        partitions = parse_partition_table(table)
        self.assertEqual(len(partitions), 2)
        self.assertEqual(partitions[0]["offset"], 0x10000)
        self.assertEqual(partitions[1]["label"], "sys")

    def test_build_install_from_legacy(self):
        version = {
            "Fs": 0x300000,
            "ao": 0x10000,
            "as": 0x200000,
            "s": 0,
            "f": 1,
            "fo": 0x210000,
            "fs": 0x100000,
            "f2": 0,
        }
        manifest = build_install_from_legacy(version, {"category": "cardputer"})
        self.assertEqual(manifest["format"], "merged")
        self.assertEqual(manifest["app"]["source_offset"], 0x10000)
        self.assertEqual(manifest["partitions"][1]["label"], "sys")
        self.assertEqual(manifest["analysis"]["confidence"], "legacy")

    def test_analyze_remote_direct_app(self):
        content = esp_image(b"abcd")
        session = FakeSession(content)
        version = {"version": "1", "file": "firmware.bin"}
        item = {"category": "stickc"}

        analyze_remote_firmware(version, item, session=session)

        self.assertEqual(version["Fs"], len(content))
        self.assertTrue(version["nb"])
        self.assertEqual(version["install"]["format"], "app")
        self.assertEqual(version["install"]["app"]["source_offset"], 0)
        self.assertEqual(version["install"]["analysis"]["method"], "file_size")

    def test_analyze_remote_merged_partition_table(self):
        image = esp_image(b"abcdefgh")
        content = bytearray(0x320000)
        content[0x8000:0x8000 + 96] = (
            partition_entry(0x00, 0x10, 0x10000, 0x200000, "ota_0")
            + partition_entry(0x01, 0x81, 0x210000, 0x100000, "sys")
            + b"\xFF" * 32
        )
        content[0x10000:0x10000 + len(image)] = image
        session = FakeSession(bytes(content))
        version = {"version": "1", "file": "https://example.test/fw.bin"}
        item = {"category": "cardputer"}

        analyze_remote_firmware(version, item, session=session)

        self.assertEqual(version["ao"], 0x10000)
        self.assertEqual(version["as"], 48)
        self.assertEqual(version["f"], 1)
        self.assertEqual(version["install"]["analysis"]["method"], "partition_table")
        self.assertEqual(version["install"]["app"]["image_size"], 48)
        self.assertEqual(version["install"]["app"]["partition_size"], 0x200000)

    def test_analyze_remote_full_flash_dump_measures_app_image(self):
        image = esp_image(b"abcdefgh")
        content = bytearray(4 << 20)
        content[0x8000:0x8000 + 64] = (
            partition_entry(0x00, 0x10, 0x10000, 0x3F0000, "ota_0")
            + b"\xFF" * 32
        )
        content[0x10000:0x10000 + len(image)] = image
        session = FakeSession(bytes(content))
        version = {"version": "1", "file": "https://example.test/flash_dump.bin"}
        item = {"category": "cardputer"}

        analyze_remote_firmware(version, item, session=session)

        self.assertEqual(session.get_calls, ["bytes=0-36863", None])
        self.assertEqual(version["ao"], 0x10000)
        self.assertEqual(version["as"], 48)
        self.assertEqual(version["install"]["app"]["image_size"], 48)
        self.assertEqual(version["install"]["app"]["partition_size"], 0x3F0000)

    def test_analyze_remote_measures_when_payload_exists_after_declared_app_size(self):
        image = esp_image(b"abcdefgh")
        content = bytearray(0x1C0000)
        content[0x8000:0x8000 + 96] = (
            partition_entry(0x00, 0x10, 0x10000, 0x200000, "ota_0")
            + partition_entry(0x01, 0x81, 0x180000, 0x40000, "sys")
            + b"\xFF" * 32
        )
        content[0x10000:0x10000 + len(image)] = image
        content[0x180000:0x1C0000] = b"D" * 0x40000
        session = FakeSession(bytes(content))
        version = {
            "version": "1",
            "file": "https://example.test/merged_with_data.bin",
            "as": 0x120000,
        }
        item = {"category": "cardputer"}

        analyze_remote_firmware(version, item, session=session)

        self.assertEqual(session.get_calls, ["bytes=0-36863", None])
        self.assertEqual(version["as"], 48)
        self.assertEqual(version["install"]["app"]["image_size"], 48)
        self.assertEqual(version["install"]["app"]["partition_size"], 0x200000)

    def test_analyze_remote_404_marks_invalid(self):
        version = {"version": "1", "file": "missing.bin"}
        item = {"category": "cardputer"}

        analyze_remote_firmware(version, item, session=NotFoundSession())

        self.assertTrue(version["invalid"])
        self.assertIn("analysis_error", version)
        self.assertNotIn("install", version)


if __name__ == "__main__":
    unittest.main()
