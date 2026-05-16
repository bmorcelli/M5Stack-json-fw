import json
import os
import re
import struct
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

try:
    import requests
except ModuleNotFoundError:
    requests = None


M5BURNER_FIRMWARE_BASE_URL = "https://m5burner.oss-cn-shenzhen.aliyuncs.com/firmware/"

LEGACY_VERSION_FIELDS = [
    "Fs",
    "as",
    "ao",
    "ss",
    "so",
    "s",
    "nb",
    "fs",
    "fo",
    "f",
    "fs2",
    "fo2",
    "f2",
    "invalid",
    "install",
]

PARTITION_TYPE_APP = 0x00
PARTITION_TYPE_DATA = 0x01
PARTITION_SUBTYPE_SPIFFS = 0x82
PARTITION_SUBTYPE_FAT = 0x81

ESP_IMAGE_MAGIC = 0xE9
ESP_IMAGE_MAX_SEGMENTS = 16
ESP_IMAGE_HASH_LEN = 32
PARTITION_MAGIC = b"\xAA\x50"
FULL_FLASH_DUMP_SIZES = {4 << 20, 8 << 20, 16 << 20, 32 << 20}


class FirmwareAnalysisError(Exception):
    pass


class RangeReader:
    def __init__(self, url: str, session: Any = None, timeout: int = 20):
        if session is None:
            if requests is None:
                raise RuntimeError("requests is required for remote firmware analysis")
            session = requests
        self.url = url
        self.session = session
        self.timeout = timeout
        self.content_length: int = 0
        self.etag: Optional[str] = None
        self.last_modified: Optional[str] = None
        self.accept_ranges: bool = False
        self.bytes_downloaded: int = 0

    def head(self) -> None:
        try:
            response = self.session.head(self.url, allow_redirects=True, timeout=self.timeout)
            if response.status_code >= 400:
                return
            self._read_headers(response.headers)
        except Exception:
            return

    def _read_headers(self, headers: Dict[str, str]) -> None:
        content_length = headers.get("Content-Length") or headers.get("content-length")
        if content_length:
            try:
                self.content_length = int(content_length)
            except ValueError:
                self.content_length = 0
        self.etag = headers.get("ETag") or headers.get("etag")
        self.last_modified = headers.get("Last-Modified") or headers.get("last-modified")
        accept_ranges = headers.get("Accept-Ranges") or headers.get("accept-ranges") or ""
        self.accept_ranges = "bytes" in accept_ranges.lower()

    def read_header_chunk(self, size: int) -> bytes:
        """Read the first `size` bytes using a Range request.

        If the server ignores Range and returns 200, streams only up to `size`
        bytes and closes the connection early to avoid downloading the full file.
        Returns up to `size` bytes (may be less for small files).
        """
        if size <= 0:
            return b""
        headers = {"Range": f"bytes=0-{size - 1}"}
        response = self.session.get(self.url, headers=headers, stream=True, timeout=self.timeout)
        if response.status_code == 206:
            data = response.content
        elif response.status_code == 200:
            self._read_headers(response.headers)
            chunks: List[bytes] = []
            collected = 0
            for chunk in response.iter_content(chunk_size=65536):
                if not chunk:
                    continue
                remaining = size - collected
                chunks.append(chunk[:remaining])
                collected += min(len(chunk), remaining)
                if collected >= size:
                    response.close()
                    break
            data = b"".join(chunks)
        else:
            response.raise_for_status()
            data = b""
        self.bytes_downloaded += len(data)
        return data

    def read_full(self) -> bytes:
        """Download the complete file in a single GET request (no Range header)."""
        response = self.session.get(self.url, stream=False, timeout=self.timeout)
        response.raise_for_status()
        if not self.content_length:
            self._read_headers(response.headers)
        data = response.content
        self.bytes_downloaded += len(data)
        return data


def firmware_url(file_value: str) -> str:
    if file_value.startswith("http://") or file_value.startswith("https://"):
        return file_value
    return M5BURNER_FIRMWARE_BASE_URL + file_value


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def normalize_target(item: Dict[str, Any]) -> str:
    source = item.get("category") or item.get("name") or item.get("fid") or "unknown"
    text = str(source).strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text or "unknown"


def detect_esp(first_bytes: bytes) -> str:
    for token, value in (
        (b"esp32p4", "p4"),
        (b"esp32s2", "s2"),
        (b"esp32s3", "s3"),
        (b"esp32c3", "c3"),
        (b"esp32c5", "c5"),
        (b"esp32c61", "c61"),
        (b"esp32c6", "c6"),
        (b"esp32h2", "h2"),
        (b"esp32e22", "e22"),
    ):
        if token in first_bytes:
            return value
    return "32"


def align_up(value: int, alignment: int) -> int:
    return (value + alignment - 1) // alignment * alignment


def parse_esp_image_size(read_at, source_offset: int, file_size: int = 0) -> int:
    header = read_at(source_offset, 8)
    if len(header) < 8 or header[0] != ESP_IMAGE_MAGIC:
        raise FirmwareAnalysisError(f"ESP image not found at 0x{source_offset:X}")

    segment_count = header[1]
    if segment_count == 0 or segment_count > ESP_IMAGE_MAX_SEGMENTS:
        raise FirmwareAnalysisError(f"Invalid ESP segment count: {segment_count}")

    hash_appended = bool(header[7] & 0x01)
    cursor = source_offset + 8
    if file_size and cursor > file_size:
        raise FirmwareAnalysisError("Truncated ESP image header")

    for _ in range(segment_count):
        segment_header = read_at(cursor, 8)
        if len(segment_header) < 8:
            raise FirmwareAnalysisError("Truncated ESP segment header")
        _, data_len = struct.unpack("<II", segment_header)
        cursor += 8
        if file_size and (data_len > file_size or cursor > file_size - data_len):
            raise FirmwareAnalysisError("ESP segment exceeds file size")
        cursor += data_len

    image_end = align_up(cursor, 16) + 1
    if hash_appended:
        image_end += ESP_IMAGE_HASH_LEN
    image_end = align_up(image_end, 16)

    if image_end <= source_offset:
        raise FirmwareAnalysisError("Invalid ESP image size")
    if file_size and image_end > file_size:
        raise FirmwareAnalysisError("ESP image exceeds file size")
    return image_end - source_offset


def parse_partition_table(data: bytes, table_offset: int = 0x8000) -> List[Dict[str, Any]]:
    partitions: List[Dict[str, Any]] = []
    for offset in range(0, len(data) - 31, 32):
        entry = data[offset:offset + 32]
        if entry[:2] == b"\xFF\xFF":
            break
        if entry[:2] != PARTITION_MAGIC:
            if offset == 0:
                return []
            break

        part_type = entry[2]
        subtype = entry[3]
        part_offset, size = struct.unpack("<II", entry[4:12])
        label = entry[12:28].split(b"\x00", 1)[0].decode("ascii", errors="ignore")
        partitions.append(
            {
                "type_id": part_type,
                "subtype_id": subtype,
                "offset": part_offset,
                "size": size,
                "label": label,
                "table_offset": table_offset + offset,
            }
        )
    return partitions


def partition_type_name(type_id: int) -> str:
    if type_id == PARTITION_TYPE_APP:
        return "app"
    if type_id == PARTITION_TYPE_DATA:
        return "data"
    return f"0x{type_id:02x}"


def partition_subtype_name(type_id: int, subtype_id: int) -> str:
    if type_id == PARTITION_TYPE_APP:
        if subtype_id == 0x00:
            return "factory"
        if 0x10 <= subtype_id <= 0x1F:
            return "ota"
        if subtype_id == 0x20:
            return "test"
    if type_id == PARTITION_TYPE_DATA:
        if subtype_id == PARTITION_SUBTYPE_FAT:
            return "fat"
        if subtype_id == PARTITION_SUBTYPE_SPIFFS:
            return "spiffs"
        if subtype_id == 0x00:
            return "ota"
        if subtype_id == 0x01:
            return "phy"
        if subtype_id == 0x02:
            return "nvs"
    return f"0x{subtype_id:02x}"


def build_app_partition(source_offset: int, image_size: int, partition_size: int) -> Dict[str, Any]:
    return {
        "type": "app",
        "subtype": "ota",
        "label": "auto",
        "role": "firmware",
        "size": partition_size,
        "source_offset": source_offset,
        "copy_size": image_size,
        "required": True,
    }


def data_partition_from_legacy(
    subtype: str,
    label: str,
    source_offset: Optional[int],
    size: Optional[int],
) -> Optional[Dict[str, Any]]:
    if not source_offset or not size:
        return None
    return {
        "type": "data",
        "subtype": subtype,
        "label": label,
        "role": subtype,
        "size": size,
        "source_offset": source_offset,
        "copy_size": size,
        "required": True,
    }


def build_install_from_legacy(version: Dict[str, Any], item: Dict[str, Any], warnings: Optional[List[str]] = None) -> Optional[Dict[str, Any]]:
    if version.get("invalid"):
        return None

    fs = int(version.get("Fs") or 0)
    source_offset = int(version.get("ao") or 0)
    app_size = int(version.get("as") or 0)
    if app_size <= 0 and fs > source_offset:
        app_size = fs - source_offset
    if app_size <= 0:
        return None

    file_format = "app" if version.get("nb") or source_offset == 0 else "merged"
    partitions = [build_app_partition(source_offset, app_size, app_size)]

    spiffs = data_partition_from_legacy("spiffs", "spiffs", version.get("so"), version.get("ss"))
    if version.get("s") and spiffs:
        partitions.append(spiffs)

    fat1 = data_partition_from_legacy("fat", "sys", version.get("fo"), version.get("fs"))
    if version.get("f") and fat1:
        partitions.append(fat1)

    fat2 = data_partition_from_legacy("fat", "vfs", version.get("fo2"), version.get("fs2"))
    if version.get("f2") and fat2:
        partitions.append(fat2)

    return {
        "schema": 1,
        "format": file_format,
        "target": normalize_target(item),
        "app": {
            "source_offset": source_offset,
            "image_size": app_size,
            "partition_size": app_size,
            "label_policy": "next_app",
            "subtype_policy": "next_ota",
        },
        "partitions": partitions,
        "analysis": {
            "method": "legacy_fields",
            "confidence": "legacy",
            "warnings": warnings or ["Manifest generated from legacy fields; app image_size may equal partition/file size."],
        },
    }


def apply_legacy_fields_from_partitions(version: Dict[str, Any], partitions: List[Dict[str, Any]], content_length: int) -> None:
    version["Fs"] = content_length
    version["s"] = 0
    version["f"] = 0
    version["f2"] = 0

    app_set = False
    fat_count = 0
    for part in partitions:
        type_id = part["type_id"]
        subtype_id = part["subtype_id"]
        offset = int(part["offset"])
        size = int(part["size"])
        if type_id == PARTITION_TYPE_APP and not app_set:
            version["ao"] = offset
            version["as"] = min(size, max(content_length - offset, 0)) if content_length else size
            app_set = True
        elif type_id == PARTITION_TYPE_DATA and subtype_id == PARTITION_SUBTYPE_SPIFFS:
            if not content_length or content_length >= offset + size:
                version["s"] = 1
                version["so"] = offset
                version["ss"] = size
        elif type_id == PARTITION_TYPE_DATA and subtype_id == PARTITION_SUBTYPE_FAT:
            if content_length and content_length < offset + size:
                continue
            fat_count += 1
            if fat_count == 1:
                version["f"] = 1
                version["fo"] = offset
                version["fs"] = size
            elif fat_count == 2:
                version["f2"] = 1
                version["fo2"] = offset
                version["fs2"] = size


def build_install_from_partition_table(
    version: Dict[str, Any],
    item: Dict[str, Any],
    partitions: List[Dict[str, Any]],
    read_at,
    content_length: int,
    warnings: List[str],
    image_size: int = 0,
) -> Dict[str, Any]:
    app_parts = [p for p in partitions if p["type_id"] == PARTITION_TYPE_APP]
    if not app_parts:
        raise FirmwareAnalysisError("Partition table has no app partition")
    app_part = app_parts[0]
    source_offset = int(app_part["offset"])
    partition_size = int(app_part["size"])

    if not image_size:
        image_size = parse_esp_image_size(read_at, source_offset, content_length)
    if image_size > partition_size:
        warnings.append("ESP image is larger than declared app partition.")
        partition_size = image_size

    manifest_partitions = [build_app_partition(source_offset, image_size, partition_size)]
    for part in partitions:
        if part["type_id"] == PARTITION_TYPE_APP:
            continue
        subtype = partition_subtype_name(part["type_id"], part["subtype_id"])
        if subtype not in ("fat", "spiffs"):
            continue
        offset = int(part["offset"])
        size = int(part["size"])
        has_payload = not content_length or content_length >= offset + size
        manifest_partitions.append(
            {
                "type": partition_type_name(part["type_id"]),
                "subtype": subtype,
                "label": part.get("label") or subtype,
                "role": subtype,
                "size": size,
                "source_offset": offset if has_payload else None,
                "copy_size": size if has_payload else 0,
                "required": True,
            }
        )

    return {
        "schema": 1,
        "format": "merged",
        "target": normalize_target(item),
        "app": {
            "source_offset": source_offset,
            "image_size": image_size,
            "partition_size": partition_size,
            "label_policy": "next_app",
            "subtype_policy": "next_ota",
        },
        "partitions": manifest_partitions,
        "analysis": {
            "method": "partition_table",
            "confidence": "exact",
            "warnings": warnings,
        },
    }


def analyze_remote_firmware(version: Dict[str, Any], item: Dict[str, Any], session: Any = None) -> Dict[str, Any]:
    if version.get("invalid"):
        return version

    url = firmware_url(str(version["file"]))
    reader = RangeReader(url, session=session)
    reader.head()
    warnings: List[str] = []

    # Step 1: download the first 0x9000 bytes for header + partition table analysis.
    try:
        header_chunk = reader.read_header_chunk(0x9000)
    except Exception as exc:
        version["invalid"] = True
        version["analysis_error"] = f"Failed to read firmware header: {type(exc).__name__}: {exc}"
        if reader.content_length:
            version["Fs"] = reader.content_length
        return version

    if not reader.content_length:
        reader.content_length = int(version.get("Fs") or 0)
    if reader.content_length:
        version["Fs"] = reader.content_length

    if len(header_chunk) < 4 and header_chunk[:1] != bytes([ESP_IMAGE_MAGIC]):
        version["invalid"] = True
        return version

    if "esp" not in item:
        item["esp"] = detect_esp(header_chunk)

    table_data = header_chunk[0x8000:0x9000] if len(header_chunk) >= 0x9000 else b""
    partitions = parse_partition_table(table_data, 0x8000)
    # Discard garbage: if app partition offset >= file size, there's no real table here.
    if partitions and reader.content_length:
        app_parts = [p for p in partitions if p["type_id"] == PARTITION_TYPE_APP]
        if app_parts and int(app_parts[0]["offset"]) >= reader.content_length:
            partitions = []

    try:
        if partitions:
            app_part = next(p for p in partitions if p["type_id"] == PARTITION_TYPE_APP)
            source_offset = int(app_part["offset"])
            partition_size = int(app_part["size"])
            declared_app_size = int(version.get("as") or partition_size)

            _is_full_flash = reader.content_length in FULL_FLASH_DUMP_SIZES
            _has_payload_after_app = bool(
                reader.content_length and reader.content_length > source_offset + declared_app_size
            )
            if _has_payload_after_app and not _is_full_flash:
                full_data = reader.read_full()
                def read_at(offset: int, size: int) -> bytes:
                    return full_data[offset:offset + size]
                try:
                    image_size = parse_esp_image_size(read_at, source_offset, len(full_data))
                except FirmwareAnalysisError as exc:
                    # Segment walk failed (e.g. full flash dump with fragmented data).
                    # Fall back to partition_size as a safe upper bound.
                    warnings.append(f"ESP segment parse failed, using partition_size: {exc}")
                    image_size = partition_size
            elif _is_full_flash:
                # Full flash dump: download the whole file, walk ESP image segments to find
                # the real firmware end (strips 0xFF padding), then align to 0x10000 so the
                # launcher creates an optimally-sized partition without dead space.
                full_data = reader.read_full()
                def read_at(offset: int, size: int) -> bytes:  # type: ignore[no-redef]
                    return full_data[offset:offset + size]
                try:
                    image_size = parse_esp_image_size(read_at, source_offset, len(full_data))
                except FirmwareAnalysisError as exc:
                    warnings.append(f"ESP segment parse failed on full flash, using partition_size: {exc}")
                    image_size = partition_size
            else:
                # File fits within partition: image_size = bytes from app start to file end.
                image_size = max(reader.content_length - source_offset, 0) if reader.content_length else partition_size
                def read_at(offset: int, size: int) -> bytes:  # type: ignore[no-redef]
                    return header_chunk[offset:offset + size]

            apply_legacy_fields_from_partitions(version, partitions, reader.content_length)
            version["as"] = image_size
            version["install"] = build_install_from_partition_table(
                version,
                item,
                partitions,
                read_at,
                reader.content_length,
                warnings,
                image_size=image_size,
            )
        else:
            # No partition table — app-only or merged binary without table at 0x8000.
            if header_chunk[:1] == bytes([ESP_IMAGE_MAGIC]):
                source_offset = 0
                version["nb"] = True
            else:
                source_offset = 0x10000

            image_size = max(reader.content_length - source_offset, 0) if reader.content_length else 0
            version["ao"] = source_offset
            version["as"] = image_size
            version["s"] = int(version.get("s") or 0)
            version["f"] = int(version.get("f") or 0)
            version["f2"] = int(version.get("f2") or 0)
            version["install"] = build_install_from_legacy(
                version,
                item,
                warnings=["No partition table found; manifest generated from file size and detected app offset."],
            )
            if version["install"]:
                version["install"]["app"]["image_size"] = image_size
                version["install"]["partitions"][0]["copy_size"] = image_size
                version["install"]["analysis"]["method"] = "file_size"
                version["install"]["analysis"]["confidence"] = "partial"
    except Exception as exc:
        warnings.append(str(exc))
        legacy_manifest = build_install_from_legacy(version, item, warnings=warnings)
        if legacy_manifest:
            version["install"] = legacy_manifest

    if version.get("install"):
        version["install"]["analysis"]["bytes_downloaded_for_analysis"] = reader.bytes_downloaded
        version["install"]["analysis"]["analyzed_at"] = now_iso()
        if reader.etag:
            version["install"]["analysis"]["etag"] = reader.etag
        if reader.last_modified:
            version["install"]["analysis"]["last_modified"] = reader.last_modified

    return version


def ensure_install_manifest(version: Dict[str, Any], item: Dict[str, Any]) -> None:
    if "install" in version or version.get("invalid"):
        return
    manifest = build_install_from_legacy(version, item)
    if manifest:
        version["install"] = manifest


def copy_preserved_version_fields(new_version: Dict[str, Any], old_version: Dict[str, Any]) -> None:
    for field in LEGACY_VERSION_FIELDS:
        if field in old_version:
            new_version[field] = old_version[field]


def load_analysis_cache(path: str) -> Dict[str, Any]:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


_thread_local = threading.local()


def _get_thread_session() -> Any:
    if not hasattr(_thread_local, "session") or _thread_local.session is None:
        if requests is None:
            raise RuntimeError("requests is required for remote firmware analysis")
        _thread_local.session = requests.Session()
    return _thread_local.session


def analyze_remote_firmware_batch(
    tasks: List[Tuple[Dict[str, Any], Dict[str, Any]]],
    max_workers: int = 4,
) -> Dict[str, Any]:
    """Analyze a list of (item, version) tuples in parallel using a thread pool.

    Each worker reuses a thread-local requests.Session for connection pooling.
    Returns a dict with keys: files_added (int), errors (list of dicts).
    """
    files_added = 0
    errors: List[Dict[str, Any]] = []
    done = 0
    total = len(tasks)
    counter_lock = threading.Lock()

    def _worker(item: Dict[str, Any], version: Dict[str, Any]) -> None:
        nonlocal files_added, done
        name = item.get("name", "?")
        ver = version.get("version", "?")
        print(f"[>>>] {name} - {ver}", flush=True)
        try:
            session = _get_thread_session()
            analyze_remote_firmware(version, item, session=session)
            with counter_lock:
                done += 1
                if not version.get("invalid"):
                    files_added += 1
            method = version.get("install", {}).get("analysis", {}).get("method", "?") if "install" in version else "invalid"
            print(f"[{done}/{total}] {name} - {ver} - {method}", flush=True)
        except Exception as exc:
            with counter_lock:
                done += 1
            print(f"[{done}/{total}] {name} - {ver} - ERRO: {exc}", flush=True)
            errors.append({
                "item": name,
                "version": ver,
                "file": version.get("file", "?"),
                "error": str(exc),
            })

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_worker, item, version): (item, version) for item, version in tasks}
        for future in as_completed(futures):
            future.result()  # propagate unexpected exceptions from _worker

    return {"files_added": files_added, "errors": errors}
