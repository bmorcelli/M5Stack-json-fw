import base64
import email.utils
import hashlib
import json
import os
from datetime import datetime, timezone
from urllib.parse import urljoin
from urllib.request import Request, urlopen


CONFIG_URL = "https://meshcore.co.uk/configurator/config.json"
BASE_URL = "https://meshcore.co.uk"
CONFIGURATOR_URL = "https://meshcore.co.uk/configurator/"

FIRMWARE_CONFIG = {
    "name": "MeshOS",
    "author": "meshcore",
    "cover": "https://meshcore.co.uk/img/lora.svg",
    "github": "https://meshcore.co.uk/configurator/",
    "description": "Easy to use, smartphone-like MeshCore experience",
    "fid_prefix": "MeshOS",
    "devices": [
        {
            "name": "T-Deck",
            "config_device": "LilyGo T-Deck",
            "json": "t-deck.json",
        },
        {
            "name": "T-LoraPager",
            "config_device": "LilyGo T-Lora Pager",
            "json": "t-lora-pager.json",
        },
        {
            "name": "T-Display P4 AMOLED",
            "config_device": "LilyGo T-Display P4",
            "title_contains": "AMOLED",
            "json": "t-display-p4.json",
        },
        {
            "name": "T-Display P4 LCD",
            "config_device": "LilyGo T-Display P4",
            "title_contains": "LCD",
            "json": "t-display-p4.json",
        },
    ],
}


def generate_fid(fw_prefix: str, device_name: str) -> str:
    combined = fw_prefix + device_name
    digest = hashlib.sha1(combined.encode("utf-8")).digest()
    b32 = base64.b32encode(digest).decode("ascii").rstrip("=")
    return "CFW" + b32[:29]


def _load_json_file(path: str):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def _save_json_file(path: str, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)


def _published_date_from_headers(headers) -> str:
    last_modified = headers.get("Last-Modified")
    if last_modified:
        try:
            parsed = email.utils.parsedate_to_datetime(last_modified)
            return parsed.astimezone(timezone.utc).strftime("%Y-%m-%d")
        except (TypeError, ValueError):
            pass
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def fetch_meshcore_config():
    request = Request(CONFIG_URL, headers={"User-Agent": "M5Stack-json-fw/meshOS"})
    with urlopen(request, timeout=30) as response:
        body = response.read().decode("utf-8")
        return json.loads(body), _published_date_from_headers(response.headers)


def _iter_matching_firmware(config_data: dict, device_config: dict):
    title_contains = device_config.get("title_contains", "").lower()

    for config_device in config_data.get("device", []):
        if config_device.get("name") != device_config["config_device"]:
            continue

        for firmware in config_device.get("firmware", []):
            if firmware.get("role") != "meshos":
                continue
            if title_contains and title_contains not in firmware.get("title", "").lower():
                continue
            yield firmware


def _version_key(version: str):
    cleaned = version.lower().lstrip("v")
    parts = []
    for part in cleaned.replace("-", ".").split("."):
        if part.isdigit():
            parts.append((1, int(part)))
        else:
            parts.append((0, part))
    return parts


def _select_file(files: list):
    preferred_types = ("flash-wipe", "flash-update", "download")
    for file_type in preferred_types:
        for item in files:
            name = item.get("name", "")
            if item.get("type") == file_type and name.endswith(".bin"):
                return item

    for item in files:
        if item.get("name", "").endswith(".bin"):
            return item
    return None


def _build_file_url(config_data: dict, file_name: str) -> str:
    static_path = config_data.get("staticPath", "/firmware").strip("/")
    return urljoin(CONFIGURATOR_URL, f"{static_path}/{file_name}")


def collect_versions(config_data: dict, device_config: dict, published_at: str):
    versions = []
    seen = set()

    for firmware in _iter_matching_firmware(config_data, device_config):
        for version, version_data in firmware.get("version", {}).items():
            selected_file = _select_file(version_data.get("files", []))
            if not selected_file:
                continue

            file_url = _build_file_url(config_data, selected_file["name"])
            key = (version, file_url)
            if key in seen:
                continue
            seen.add(key)

            versions.append(
                {
                    "version": version,
                    "published_at": published_at,
                    "file": file_url,
                }
            )

    versions.sort(key=lambda item: _version_key(item["version"]), reverse=True)
    return versions[:10]


def atualizar_meshos():
    config_data, published_at = fetch_meshcore_config()
    devices_by_json = {}
    for device in FIRMWARE_CONFIG["devices"]:
        devices_by_json.setdefault(device["json"], []).append(device)

    for json_filename, json_devices in devices_by_json.items():
        json_path = os.path.join(os.path.dirname(__file__), "database", json_filename)
        lista = _load_json_file(json_path)

        expected_fids = {
            generate_fid(FIRMWARE_CONFIG["fid_prefix"], device["name"])
            for device in json_devices
        }
        lista = [entry for entry in lista if entry.get("fid") not in expected_fids]

        for device in json_devices:
            versions = collect_versions(config_data, device, published_at)
            if not versions:
                print(f"  {device['name']}: Nenhuma versao disponivel")
                continue

            entry = {
                "name": f"{FIRMWARE_CONFIG['name']} ({device['name']})",
                "author": FIRMWARE_CONFIG["author"],
                "description": FIRMWARE_CONFIG["description"],
                "cover": FIRMWARE_CONFIG["cover"],
                "github": FIRMWARE_CONFIG["github"],
                "fid": generate_fid(FIRMWARE_CONFIG["fid_prefix"], device["name"]),
                "versions": versions,
            }
            lista.append(entry)
            print(f"  {device['name']}: {len(versions)} versao(oes) em {json_filename}")

        _save_json_file(json_path, lista)


if __name__ == "__main__":
    try:
        atualizar_meshos()
    except Exception as exc:
        print(f"Erro ao processar MeshOS: {exc}")
        raise

    print("\nProcesso concluido!")
