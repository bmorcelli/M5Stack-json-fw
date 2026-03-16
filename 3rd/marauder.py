import base64
import hashlib
import json
import os
from datetime import datetime

import requests

REPO_OWNER = "justcallmekoko"
REPO_NAME = "ESP32Marauder"
API_URL = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/releases"
# Mapa de dispositivos -> onde buscar o arquivo bin no release -> JSON de destino.
# Cada dispositivo terá seu próprio "fid" e uma lista de versões que corresponde às releases do GitHub.
DEVICE_MAP = [
    {
        "name": "GUITON",
        "asset_contains": "guition",
        "json": "phantom.json",
    },
    {
        "name": "CYD S028R",
        "asset_contains": "cyd_2432s028.bin",
        "json": "CYD.json",
    },
    {
        "name": "CYD S028R 2USB",
        "asset_contains": "cyd_2432s028_2usb.bin",
        "json": "CYD.json",
    },
    {
        "name": "V7",
        "asset_contains": "marauder_v7.bin",
        "json": "marauder.json",
    },
    {
        "name": "Mini",
        "asset_contains": "mini.bin",
        "json": "marauder.json",
    },
    {
        "name": "V4",
        "asset_contains": "old_hardware.bin",
        "json": "marauder.json",
    },
    {
        "name": "V6",
        "asset_contains": "v6.bin",
        "json": "marauder.json",
    },
    {
        "name": "V6.x",
        "asset_contains": "v6_1.bin",
        "json": "marauder.json",
    },
]

COVER_IMAGE = "3183675bd52534e015bcc8ae2ae603a0.png"
GITHUB_URL = f"https://github.com/{REPO_OWNER}/{REPO_NAME}"
AUTHOR = "JustCallMeKoko"
DESCRIPTION = "A suite of WiFi/Bluetooth offensive and defensive tools for the ESP32. Use Launcher to install"


def generate_fid(name: str) -> str:
    """Gera um fid estável a partir do nome do dispositivo."""
    digest = hashlib.sha1(name.encode("utf-8")).digest()
    b32 = base64.b32encode(digest).decode("ascii").rstrip("=")
    return "CFW" + b32[:29]


def _load_json_file(path: str):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def _save_json_file(path: str, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)


def _parse_next_link(link_header: str):
    if not link_header:
        return None
    parts = [p.strip() for p in link_header.split(",")]
    for part in parts:
        if "rel=\"next\"" in part:
            url = part.split(";")[0].strip()
            if url.startswith("<") and url.endswith(">"):
                return url[1:-1]
    return None


def fetch_all_releases():
    releases = []
    url = API_URL
    while url:
        resp = requests.get(url, params={"per_page": 100})
        if resp.status_code != 200:
            raise Exception(f"Erro ao acessar GitHub API: {resp.status_code}")
        releases.extend(resp.json())
        url = _parse_next_link(resp.headers.get("Link"))
    return releases


def atualizar_json_por_arquivo(json_filename: str, devices: list):
    json_path = os.path.join(os.path.dirname(__file__), json_filename)
    lista = _load_json_file(json_path)

    # Encontre entradas existentes que correspondam ao mesmo projeto (mesmo Github/Author)
    existing_entries = {
        entry.get("fid"): entry
        for entry in lista
        if entry.get("github") == GITHUB_URL or entry.get("author") == AUTHOR
    }

    # Remove essas entradas para que possamos reescrever com fids por dispositivo.
    lista = [entry for entry in lista if entry.get("fid") not in existing_entries]

    releases = fetch_all_releases()

    for device in devices:
        fid = generate_fid(device["name"])
        existing_versions = {
            v["version"] for v in existing_entries.get(fid, {}).get("versions", [])
        }

        new_versions = []
        for rel in releases:
            is_release = not rel.get("prerelease", False) and not rel.get("draft", False)
            if not is_release:
                continue
            tag = rel.get("tag_name")
            published_at = rel.get("published_at", "")[:10]

            matching_asset = None
            for asset in rel.get("assets", []):
                if device["asset_contains"].lower() in asset.get("name", "").lower():
                    matching_asset = asset
                    break

            if not matching_asset:
                continue

            if tag in existing_versions:
                continue

            new_versions.append(
                {
                    "version": tag,
                    "published_at": published_at,
                    "file": matching_asset.get("browser_download_url"),
                }
            )

        if not new_versions:
            print(f"Nenhuma versão nova encontrada para '{device['name']}'.")
            continue

        combined_versions = []
        if fid in existing_entries:
            combined_versions.extend(existing_entries[fid].get("versions", []))
        combined_versions.extend(new_versions)

        # Manter apenas as últimas 10 versões, ordenadas por data de publicação (mais recente primeiro)
        combined_versions.sort(key=lambda v: v["published_at"], reverse=True)
        combined_versions = combined_versions[:10]

        new_entry = {
            "name": f"Marauder {device['name']}",
            "author": AUTHOR,
            "description": DESCRIPTION,
            "cover": COVER_IMAGE,
            "github": GITHUB_URL,
            "fid": fid,
            "versions": combined_versions,
        }

        lista.append(new_entry)
        print(f"Atualizado {device['name']} em {os.path.basename(json_path)} (+{len(new_versions)} versões).")

    _save_json_file(json_path, lista)


if __name__ == "__main__":
    # Agrupar dispositivos por arquivo JSON para não sobrescrever entradas umas das outras.
    devices_by_json = {}
    for d in DEVICE_MAP:
        devices_by_json.setdefault(d["json"], []).append(d)

    for json_file, devices in devices_by_json.items():
        atualizar_json_por_arquivo(json_file, devices)

