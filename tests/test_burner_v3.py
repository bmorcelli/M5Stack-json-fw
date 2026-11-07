import unittest
from copy import deepcopy
from unittest.mock import Mock, patch

import requests

from v3 import update_json as updater


def firmware():
    return {
        "firmwareId": "2116203305757151233", "firmwareName": " CyberKey ",
        "firmwareDescription": '{"format":"markdown","content":"Description"}',
        "coverUrl": "https://example.com/cover.png", "developer": {"userId": "123", "nickname": "Alice"},
        "supportedDevices": [{"deviceSlug": "cardputer"}, {"deviceSlug": "cardputer-adv"}],
        "versions": [{"versionId": "2117748756900839425", "versionName": "1.3.0",
                      "publishTime": "2026-11-04T10:43:34+08:00", "binFileName": "CyberKey-complete.bin",
                      "binary": {"verified": True, "url": "https://example.com/file.bin"}}],
    }


class CatalogTests(unittest.TestCase):
    def test_full_urls_exact_slugs_and_shared_fid(self):
        row = firmware()
        items, unresolved = updater.build_catalog([row])
        self.assertFalse(unresolved)
        self.assertEqual([i["category"] for i in items], ["cardputer", "cardputer-adv"])
        self.assertEqual({i["fid"] for i in items}, {row["firmwareId"]})
        self.assertEqual(len({i["_id"] for i in items}), 2)
        self.assertEqual(items[0]["cover"], row["coverUrl"])
        self.assertEqual(items[0]["versions"][0]["file"], "https://example.com/file.bin")
        self.assertEqual(items[0]["description"], "Description")
        self.assertEqual(row["firmwareName"], " CyberKey ")

    def test_version_device_overrides_firmware(self):
        row = firmware()
        row["versions"][0]["supportedDevices"] = [{"deviceSlug": "sticks3"}]
        items, _ = updater.build_catalog([row])
        self.assertEqual(items[0]["category"], "sticks3")
        self.assertEqual(items[0]["_id"], row["firmwareId"])

    def test_unresolved_versions_remain_in_source(self):
        row = firmware()
        row["versions"][0]["binary"] = {"verified": False, "url": None, "reason": "no_public_binary"}
        items, unresolved = updater.build_catalog([row])
        self.assertEqual(items, [])
        self.assertEqual(unresolved[0]["versionId"], row["versions"][0]["versionId"])
        self.assertEqual(len(row["versions"]), 1)

    def test_versions_sorted_by_publication(self):
        row = firmware()
        version = deepcopy(row["versions"][0])
        version.update(versionId="2", versionName="1.0", publishTime="2025-01-01T00:00:00+08:00")
        row["versions"].append(version)
        items, _ = updater.build_catalog([row])
        self.assertEqual([v["version"] for v in items[0]["versions"]], ["1.0", "1.3.0"])

    def test_metadata_migrates_only_for_same_binary_and_text(self):
        items, _ = updater.build_catalog([firmware()])
        old = deepcopy(items)
        old[0]["versions"][0].update(s=1, install={"target": "old"})
        old[0].update(description_en="English", esp="s3")
        updater.preserve_metadata(items, old)
        self.assertEqual(items[0]["versions"][0]["s"], 1)
        self.assertEqual(items[0]["versions"][0]["install"]["target"], "cardputer")
        self.assertEqual(old[0]["versions"][0]["install"]["target"], "old")
        self.assertEqual(items[0]["description_en"], "English")
        items[0]["description"] = "Changed"
        del items[0]["description_en"]
        updater.preserve_metadata(items, old)
        self.assertNotIn("description_en", items[0])

    def test_legacy_filename_matches_full_url(self):
        self.assertEqual(updater.file_key("a.bin"), updater.file_key(updater.OSS + "/firmware/a.bin"))


class ApiTests(unittest.TestCase):
    def test_listing_identity_ignores_counters_and_permissions(self):
        row = firmware()
        changed = deepcopy(row)
        changed.update(statistics={"downloadCount": 50}, editable=False)
        self.assertEqual(updater.listing_identity(row), updater.listing_identity(changed))
        changed["updatedAt"] = "2026-11-05"
        self.assertNotEqual(updater.listing_identity(row), updater.listing_identity(changed))

    @patch.object(updater, "api_get")
    def test_pagination_uses_total_not_requested_page_size(self, get):
        get.side_effect = [{"rows": [1, 2], "total": 3}, {"rows": [3], "total": 3}]
        self.assertEqual(updater.paginate("/firmwares", page_size=50), [1, 2, 3])
        self.assertEqual(get.call_args.kwargs["pageNum"], 2)

    @patch.object(updater, "api_get")
    def test_incomplete_pagination_fails(self, get):
        get.side_effect = [{"rows": [1], "total": 3}, {"rows": [], "total": 3}]
        with self.assertRaises(updater.ApiError):
            updater.paginate("/firmwares")

    @patch.object(updater.time, "sleep")
    def test_probe_retries_transient_errors_and_closes_streams(self, sleep):
        client = Mock()
        failed, success = Mock(status_code=503), Mock(status_code=206)
        client.get.side_effect = [requests.ConnectionError(), failed, success]
        self.assertTrue(updater.is_public_file(client, "https://example.com/fw.bin"))
        failed.close.assert_called_once()
        success.close.assert_called_once()

    @patch.object(updater.time, "sleep")
    def test_persistent_failure_is_not_missing_file(self, sleep):
        client = Mock()
        client.get.side_effect = requests.Timeout()
        with self.assertRaises(updater.ApiError):
            updater.is_public_file(client, "https://example.com/fw.bin")

    @patch.object(updater, "is_public_file", return_value=False)
    def test_id_rule_requires_validation(self, probe):
        row = firmware()
        version = row["versions"][0]
        result = updater.resolve_binary(row, version)
        self.assertFalse(result["verified"])
        self.assertIsNone(result["url"])
        self.assertIn("/2117748756900839426.bin", result["candidates"][0])
        self.assertEqual(len(result["candidates"]), 2)

    @patch.object(updater, "is_public_file")
    def test_sources_are_not_treated_as_binaries(self, probe):
        row = firmware()
        row["versions"][0]["binFileName"] = "source.ino"
        self.assertEqual(updater.resolve_binary(row, row["versions"][0])["reason"], updater.REASON_UNSUPPORTED_FILE)
        probe.assert_not_called()


if __name__ == "__main__":
    unittest.main()
