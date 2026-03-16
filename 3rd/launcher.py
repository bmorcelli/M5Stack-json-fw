import base64
import hashlib
import json
import os
from datetime import datetime

import requests

REPO_OWNER = "bmorcelli"
REPO_NAME = "Launcher"
COVER_IMAGE = "8a0100966905599183f9431ea873058f.gif"
GITHUB_URL = f"https://github.com/{REPO_OWNER}/{REPO_NAME}"
AUTHOR = "bmorcelli"
DESCRIPTION = "With this app you can turn your device into a swiss knife, loading any .bin you have on your SD Card or wirelessly downloading from M5Burner repo or from your computer/smartphone through its WebUI."


# Cada entrada representa um dispositivo/variant e o arquivo bin que aparece no release.
# O script cria/atualiza uma entrada separada para cada item e mantém apenas as últimas 10 versões.
DEVICE_MAP = [
    # T-Deck
    {"name": "T-Deck", "asset_contains": "launcher-lilygo-t-deck.bin", "json": "t-deck.json"},
    {"name": "T-Deck Plus", "asset_contains": "launcher-lilygo-t-deck-plus.bin", "json": "t-deck.json"},
    {"name": "T-Deck Pro", "asset_contains": "launcher-lilygo-t-deck-pro.bin", "json": "t-deck-pro.json"},
    
    # T-Embed
    {"name": "T-Embed", "asset_contains": "Launcher-lilygo-t-embed.bin", "json": "t-embed-cc1101.json"},
    {"name": "T-Embed CC1101", "asset_contains": "Launcher-lilygo-t-embed-cc1101.bin", "json": "t-embed-cc1101.json"},

    # T-HMI
    {"name": "T-HMI", "asset_contains": "Launcher-lilygo-t-hmi.bin", "json": "t-hmi.json"},
    {"name": "T-LoraPager", "asset_contains": "Launcher-lilygo-t-lora-pager.bin", "json": "t-lora-pager.json"},
    {"name": "T-Watch S3", "asset_contains": "Launcher-lilygo-t-watch-s3.bin", "json": "t-watch-s3.json"},

    # CYD
    {"name": "CYD S028R", "asset_contains": "launcher-cyd-2432s028.bin", "json": "CYD.json"},
    {"name": "CYD 2USB", "asset_contains": "launcher-cyd-2-usb.bin", "json": "CYD.json"},
    {"name": "CYD S024R", "asset_contains": "launcher-cyd-2432s024r.bin", "json": "CYD.json"},
    {"name": "CYD W328R", "asset_contains": "launcher-cyd-2432w328r.bin", "json": "CYD.json"},
    {"name": "CYD W328C", "asset_contains": "launcher-cyd-2432w328c.bin", "json": "CYD.json"},

    # Marauder / Awok
    {"name": "Awok Mini", "asset_contains": "launcher-awok-mini.bin", "json": "marauder.json"},
    {"name": "Awok Touch", "asset_contains": "launcher-awok-touch.bin", "json": "marauder.json"},
    {"name": "Marauder Mini", "asset_contains": "launcher-marauder-mini.bin", "json": "marauder.json"},
    {"name": "Marauder V4", "asset_contains": "launcher-marauder-v4-og.bin", "json": "marauder.json"},
    {"name": "Marauder V6", "asset_contains": "launcher-marauder-v61.bin", "json": "marauder.json"},
    {"name": "Marauder V7", "asset_contains": "launcher-marauder-v7.bin", "json": "marauder.json"},

    # Phantom
    {"name": "Phantom S024R", "asset_contains": "launcher-phantom_s024r.bin", "json": "phantom.json"},

    # Smoochiee
    {"name": "Smoochiee V2", "asset_contains": "launcher-smoochiee-board.bin", "json": "smoochiee_v2.json"},
]

def generate_fid(name: str, existing_fid: str = None) -> str:
    """Gera um fid estável a partir do nome do dispositivo ou usa o fid existente."""
    if existing_fid:
        return existing_fid

    digest = hashlib.sha1(("Launcher" + name).encode("utf-8")).digest()
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
    url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/releases"
    while url:
        resp = requests.get(url, params={"per_page": 100})
        if resp.status_code != 200:
            raise Exception(f"Erro ao acessar GitHub API: {resp.status_code}")
        releases.extend(resp.json())
        url = _parse_next_link(resp.headers.get("Link"))
    return releases


def atualizar_json_por_arquivo(json_filename: str, devices: list, releases: list):
    json_path = os.path.join(os.path.dirname(__file__), json_filename)
    lista = _load_json_file(json_path)

    existing_entries = {
        entry.get("fid"): entry
        for entry in lista
        if entry.get("github") == GITHUB_URL or entry.get("author") == AUTHOR
    }

    # Remove entradas que vamos reescrever (para evitar duplicates)
    lista = [entry for entry in lista if entry.get("fid") not in existing_entries]

    for device in devices:
        existing_entry = None
        # Tente reaproveitar uma entrada existente do mesmo dispositivo (mesmo nome ou mesmo asset)
        for entry in existing_entries.values():
            if entry.get("name") == device["name"]:
                existing_entry = entry
                break
            for version in entry.get("versions", []):
                if device["asset_contains"].lower() in version.get("file", "").lower():
                    existing_entry = entry
                    break
            if existing_entry:
                break

        fid = generate_fid(device["name"], existing_entry.get("fid") if existing_entry else None)
        existing_versions = {v["version"] for v in (existing_entry.get("versions") if existing_entry else [])}

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
        if existing_entry:
            combined_versions.extend(existing_entry.get("versions", []))
        combined_versions.extend(new_versions)

        # Manter apenas as últimas 10 versões (mais recentes primeiro)
        combined_versions.sort(key=lambda v: v.get("published_at", ""), reverse=True)
        combined_versions = combined_versions[:10]

        new_entry = {
            "name": "Launcher " + device["name"],
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
    releases = fetch_all_releases()  # Buscar releases uma vez por execução
    devices_by_json = {}
    for d in DEVICE_MAP:
        devices_by_json.setdefault(d["json"], []).append(d)

    for json_file, devices in devices_by_json.items():
        atualizar_json_por_arquivo(json_file, devices, releases)
