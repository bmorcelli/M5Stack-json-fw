"""Export the public M5Burner v1 API and generate the compatible v2 catalog."""

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import threading
import time
from urllib.parse import quote

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from script.firmware_manifest import (  # noqa: E402
    analyze_remote_firmware_batch, copy_preserved_version_fields,
    ensure_install_manifest,
)

API = "https://burner.m5stack.com/api/v1"
OSS = "https://m5burner.oss-cn-shenzhen.aliyuncs.com"
LOCAL = threading.local()
REASON_NO_PUBLIC_FILE = "no_public_binary"
REASON_UNSUPPORTED_FILE = "unsupported_file"


class ApiError(RuntimeError):
    pass


def session():
    if not hasattr(LOCAL, "session"):
        LOCAL.session = requests.Session()
    return LOCAL.session


def api_get(path, **params):
    for attempt in range(4):
        try:
            response = session().get(API + path, params=params, timeout=40)
            response.raise_for_status()
            payload = response.json()
            if payload.get("code") != 200:
                raise ApiError(f"{path}: {payload}")
            return payload
        except (requests.RequestException, ValueError, ApiError) as exc:
            if attempt == 3:
                raise ApiError(f"Cannot read {path}: {exc}") from exc
            time.sleep(2 ** attempt)


def paginate(path, page_size=50, **params):
    rows, seen = [], set()
    page = 1
    while True:
        payload = api_get(path, pageNum=page, pageSize=page_size, **params)
        batch = payload["rows"]
        signature = json.dumps(batch, sort_keys=True)
        if batch and signature in seen:
            raise ApiError(f"Repeated page in {path}: {page}")
        seen.add(signature)
        rows.extend(batch)
        if len(rows) >= int(payload["total"]):
            return rows
        if not batch:
            raise ApiError(f"Incomplete pagination in {path}: {len(rows)}/{payload['total']}")
        page += 1


def is_public_file(client, url):
    if not url.startswith("https://"):
        url = OSS + "/firmware/" + quote(url, safe="")
    for attempt in range(4):
        response = None
        try:
            response = client.get(url, headers={"Range": "bytes=0-0"}, stream=True, timeout=30)
            if response.status_code in (200, 206):
                return True
            if response.status_code in (403, 404):
                return False
            raise ApiError(f"Binary HTTP {response.status_code}: {url}")
        except (requests.RequestException, ApiError) as exc:
            if attempt == 3:
                raise ApiError(f"Cannot verify {url}: {exc}") from exc
            time.sleep(2 ** attempt)
        finally:
            if response is not None:
                response.close()


def has_binary_file(name):
    return bool(name) and name.lower().endswith((".bin", "file"))


def binary_candidates(firmware, version):
    """The community ID rule is a candidate, never an assumed download URL."""
    candidates = []
    for field in ("binUrl", "binFileUrl", "downloadUrl"):
        value = version.get(field)
        if value and value.startswith("https://"):
            candidates.append((value, "api"))
    user = (firmware.get("developer") or {}).get("userId")
    vid = str(version["versionId"])
    if user and vid.isdigit():
        candidates.append((f"{OSS}/community/{user}/{firmware['firmwareId']}/{vid}/bin/{int(vid) + 1}.bin", "community_id_candidate"))
    name = version.get("binFileName")
    if name:
        candidates.append((OSS + "/firmware/" + quote(name, safe=""), "legacy_filename"))
    return candidates


def resolve_binary(firmware, version):
    result = {"fileName": version.get("binFileName"), "url": None, "verified": False}
    if not has_binary_file(result["fileName"]):
        result["reason"] = REASON_UNSUPPORTED_FILE
        return result
    candidates = binary_candidates(firmware, version)
    # Imported files normally retain their original hashed filename.
    if str(version["versionId"]).startswith("189730"):
        candidates.sort(key=lambda candidate: candidate[1] != "legacy_filename")
    for url, method in candidates:
        if is_public_file(session(), url):
            result.update(url=url, verified=True, resolution=method)
            return result
    result.update(reason=REASON_NO_PUBLIC_FILE, candidates=[url for url, _ in candidates])
    return result


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    os.replace(temp, path)


def read_json(path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def listing_identity(row):
    """Content changes invalidate checkpoints; live counters do not."""
    fields = ("firmwareId", "firmwareName", "firmwareDescription", "sourceType",
              "coverUrl", "uploadedAt", "updatedAt", "categories", "supportedDevices")
    result = {key: row.get(key) for key in fields}
    result["developer"] = {key: (row.get("developer") or {}).get(key)
                           for key in ("userId", "nickname", "avatarUrl")}
    result["currentVersion"] = {key: (row.get("currentVersion") or {}).get(key)
                                for key in ("versionId", "versionName", "versionDescription", "publishTime")}
    return result


def fetch_firmware(row, force=False, previous=None):
    # Checkpoints allow interrupted initial crawls to resume without losing work.
    path = ROOT / "v3/.cache" / (str(row["firmwareId"]) + ".json")
    cached = read_json(path, {})
    candidate = cached.get("firmware") or previous
    if not force and candidate and listing_identity(candidate) == listing_identity(row):
        # Retry unresolved files; a previous 403/404 may become accessible later.
        for version in candidate["versions"]:
            if version["binary"].get("reason") == REASON_NO_PUBLIC_FILE:
                version["binary"] = resolve_binary(candidate, version)
        candidate["statistics"] = row.get("statistics", {})
        atomic_json(path, {"listing": row, "firmware": candidate})
        return candidate
    fid = row["firmwareId"]
    firmware = api_get(f"/firmwares/{fid}")["data"]
    versions = api_get(f"/firmwares/{fid}/versions", scope="PUBLIC")["data"]
    for version in versions:
        version["binary"] = resolve_binary(firmware, version)
    firmware["versions"] = versions
    atomic_json(path, {"listing": row, "firmware": firmware})
    return firmware


def fetch_catalog(workers=8, force=False):
    previous = read_json(ROOT / "v3/all_device_firmware.json", {})
    previous_by_id = {f["firmwareId"]: f for f in previous.get("firmwares", [])}
    devices = api_get("/devices")["data"]
    categories = api_get("/firmware-categories")["data"]
    developers = paginate("/developers")
    rankings = {scope: api_get(f"/firmwares/rankings/{scope}", limit=10)["data"]
                for scope in ("weekly", "all-time")}
    rows = paginate("/firmwares", sort="LATEST")
    ids = [row["firmwareId"] for row in rows]
    if len(ids) != len(set(ids)):
        raise ApiError("Duplicate firmware IDs: catalog changed during pagination; rerun")
    firmwares = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(fetch_firmware, row, force, previous_by_id.get(row["firmwareId"])) for row in rows]
        for future in as_completed(futures):
            try:
                firmwares.append(future.result())
            except Exception:
                for pending in futures:
                    pending.cancel()
                raise
            if len(firmwares) % 50 == 0:
                print(f"Collected {len(firmwares)}/{len(rows)} firmwares", flush=True)
    if not firmwares:
        raise ApiError("Refusing to replace catalogs with an empty API response")
    return {"schemaVersion": 3, "source": API,
            "fetchedAt": datetime.now(timezone.utc).isoformat(),
            "devices": devices, "firmwareCategories": categories,
            "developers": developers, "rankings": rankings,
            "firmwares": sorted(firmwares, key=lambda f: str(f["firmwareId"]))}


def parse_description(value):
    if not value:
        return ""
    try:
        parsed = json.loads(value)
        if isinstance(parsed, dict) and isinstance(parsed.get("content"), str):
            return parsed["content"].strip()
    except (ValueError, TypeError):
        pass
    return str(value).strip()


def device_category(device):
    if isinstance(device, dict):
        return device.get("deviceSlug") or device_category(device.get("deviceCode", ""))
    return str(device).lower().replace("_", "-")


def build_catalog(firmwares):
    items, unresolved = [], []
    for firmware in firmwares:
        fid = str(firmware["firmwareId"])
        grouped = {}
        for version in sorted(firmware["versions"], key=lambda v: (v.get("publishTime") or "", str(v["versionId"]))):
            binary = version["binary"]
            if not binary.get("verified") or not binary.get("url"):
                unresolved.append({"firmwareId": fid, "versionId": version["versionId"],
                                   "name": firmware["firmwareName"], **binary})
                continue
            devices = version.get("supportedDevices") or firmware.get("supportedDevices") or []
            if not devices:
                unresolved.append({"firmwareId": fid, "versionId": version["versionId"], "reason": "no_supported_device"})
            for slug in sorted({device_category(d) for d in devices} - {""}):
                grouped.setdefault(slug, []).append({
                    "version": version["versionName"], "version_id": str(version["versionId"]),
                    "published_at": (version.get("publishTime") or "")[:10], "file": binary["url"],
                })
        for slug, versions in grouped.items():
            items.append({"_id": fid if len(grouped) == 1 else f"{fid}:{slug}", "fid": fid,
                          "name": firmware["firmwareName"].strip(),
                          "description": parse_description(firmware.get("firmwareDescription")),
                          "cover": firmware.get("coverUrl") or "", "category": slug,
                          "tags": [c["categoryName"] for c in firmware.get("categories", [])],
                          "author": (firmware.get("developer") or {}).get("nickname", ""),
                          "github": firmware.get("sourceUrl") or "",
                          "download": (firmware.get("statistics") or {}).get("downloadCount", 0),
                          "versions": versions})
    return sorted(items, key=lambda i: (i["name"], i["category"], i["fid"])), unresolved


def file_key(value):
    return value if value.startswith("https://") else OSS + "/firmware/" + quote(value, safe="")


def preserve_metadata(items, old_items):
    by_file = {}
    for item in old_items:
        for version in item.get("versions", []):
            key = file_key(version["file"])
            previous = by_file.get(key)
            if previous is None or len(version) > len(previous[1]):
                by_file[key] = (item, version)
    for item in items:
        for version in item["versions"]:
            old = by_file.get(file_key(version["file"]))
            if not old:
                continue
            old_item, old_version = old
            copy_preserved_version_fields(version, deepcopy(old_version))
            # Keep binary-specific chip detection; regenerate target from the new slug.
            if old_item.get("esp"):
                item["esp"] = old_item["esp"]
            if version.get("install"):
                version["install"]["target"] = item["category"]
            for field in ("name", "description"):
                if item[field] == old_item.get(field) and old_item.get(field + "_en"):
                    item[field + "_en"] = old_item[field + "_en"]
                    item[field + "_src"] = old_item.get(field + "_src", old_item[field])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-workers", type=int, default=8)
    parser.add_argument("--force-download-all", action="store_true")
    parser.add_argument("--from-snapshot", action="store_true", help="Rebuild v2 from the saved v3 snapshot without crawling")
    parser.add_argument("--skip-analysis", action="store_true", help="Preserve existing analysis and defer analysis of new binaries")
    args = parser.parse_args()
    if args.max_workers < 1:
        parser.error("--max-workers must be positive")
    raw_path = ROOT / "v3/all_device_firmware.json"
    v2_path = ROOT / "v2/all_device_firmware.json"
    previous_raw = read_json(raw_path, None)
    raw = previous_raw if args.from_snapshot else fetch_catalog(args.max_workers, args.force_download_all)
    if raw is None:
        parser.error("No v3 snapshot exists")
    items, unresolved = build_catalog(raw["firmwares"])
    old = read_json(v2_path, [])
    if not args.force_download_all:
        preserve_metadata(items, old)
    tasks = []
    for item in items:
        for version in item["versions"]:
            if "s" not in version and "invalid" not in version:
                tasks.append((item, version))
            else:
                ensure_install_manifest(version, item)
    errors = []
    processed = 0
    if tasks and not args.skip_analysis:
        result = analyze_remote_firmware_batch(tasks, max_workers=args.max_workers)
        errors = result["errors"]
        processed = len(tasks) - len(errors)
        for item, version in tasks:
            if "s" in version or version.get("invalid"):
                ensure_install_manifest(version, item)
    # Only commit when binaries were actually processed; metadata-only drift
    # (download counts, ranking/developer reordering) is saved locally but
    # does not trigger a commit by itself.
    changed = processed > 0
    # No catalog is replaced until the complete API crawl and transformation succeed.
    atomic_json(raw_path, raw)
    atomic_json(ROOT / "v3/unresolved_binaries.json", sorted(unresolved, key=lambda v: (v["firmwareId"], v["versionId"])))
    atomic_json(ROOT / "v3/analysis_errors.json", errors)
    atomic_json(v2_path, items)
    if os.environ.get("GITHUB_ENV"):
        with open(os.environ["GITHUB_ENV"], "a", encoding="utf-8") as stream:
            stream.write(f"FILES_ADDED={str(changed).lower()}\n")
    print(f"Saved {len(raw['firmwares'])} firmwares; {len(items)} device entries; {len(unresolved)} unresolved binaries; {len(errors)} analysis errors")
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
