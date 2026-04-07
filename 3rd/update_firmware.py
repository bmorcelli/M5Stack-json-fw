import base64
import hashlib
import json
import os
from datetime import datetime

import requests

# ============================================================================
# CONFIGURAÇÕES DE FIRMWARE
# ============================================================================

FIRMWARE_CONFIGS = [
    {
        "name": "Marauder",
        "repo_owner": "justcallmekoko",
        "repo_name": "ESP32Marauder",
        "author": "JustCallMeKoko",
        "cover": "3183675bd52534e015bcc8ae2ae603a0.png",
        "description": "A suite of WiFi/Bluetooth offensive and defensive tools for the ESP32. Use Launcher to install",
        "fid_prefix": "Marauder",
        "devices": [
            {"name": "GUITON", "asset_contains": "guition", "json": "phantom.json"},
            {"name": "CYD S028R", "asset_contains": "cyd_2432s028.bin", "json": "CYD.json"},
            {"name": "CYD S028R 2USB", "asset_contains": "cyd_2432s028_2usb.bin", "json": "CYD.json"},
            {"name": "V7", "asset_contains": "marauder_v7.bin", "json": "marauder.json"},
            {"name": "Mini", "asset_contains": "mini.bin", "json": "marauder.json"},
            {"name": "V4", "asset_contains": "old_hardware.bin", "json": "marauder.json"},
            {"name": "V6", "asset_contains": "v6.bin", "json": "marauder.json"},
            {"name": "V6.x", "asset_contains": "v6_1.bin", "json": "marauder.json"},
        ],
    },
    {
        "name": "Launcher",
        "repo_owner": "bmorcelli",
        "repo_name": "Launcher",
        "author": "bmorcelli",
        "cover": "8a0100966905599183f9431ea873058f.gif",
        "description": "With this app you can turn your device into a swiss knife, loading any .bin you have on your SD Card or wirelessly downloading from M5Burner repo or from your computer/smartphone through its WebUI.",
        "fid_prefix": "Launcher",
        "devices": [
            {"name": "T-Deck", "asset_contains": "launcher-lilygo-t-deck.bin", "json": "t-deck.json"},
            {"name": "T-Deck Plus", "asset_contains": "launcher-lilygo-t-deck-plus.bin", "json": "t-deck.json"},
            {"name": "T-Deck Pro", "asset_contains": "launcher-lilygo-t-deck-pro.bin", "json": "t-deck-pro.json"},
            {"name": "T-Embed", "asset_contains": "Launcher-lilygo-t-embed.bin", "json": "t-embed-cc1101.json"},
            {"name": "T-Embed CC1101", "asset_contains": "Launcher-lilygo-t-embed-cc1101.bin", "json": "t-embed-cc1101.json"},
            {"name": "T-HMI", "asset_contains": "Launcher-lilygo-t-hmi.bin", "json": "t-hmi.json"},
            {"name": "T-LoraPager", "asset_contains": "Launcher-lilygo-t-lora-pager.bin", "json": "t-lora-pager.json"},
            {"name": "T-Watch S3", "asset_contains": "Launcher-lilygo-t-watch-s3.bin", "json": "t-watch-s3.json"},
            {"name": "CYD S028R", "asset_contains": "launcher-cyd-2432s028.bin", "json": "CYD.json"},
            {"name": "CYD 2USB", "asset_contains": "launcher-cyd-2-usb.bin", "json": "CYD.json"},
            {"name": "CYD S024R", "asset_contains": "launcher-cyd-2432s024r.bin", "json": "CYD.json"},
            {"name": "CYD W328R", "asset_contains": "launcher-cyd-2432w328r.bin", "json": "CYD.json"},
            {"name": "CYD W328C", "asset_contains": "launcher-cyd-2432w328c.bin", "json": "CYD.json"},
            {"name": "Awok Mini", "asset_contains": "launcher-awok-mini.bin", "json": "marauder.json"},
            {"name": "Awok Touch", "asset_contains": "launcher-awok-touch.bin", "json": "marauder.json"},
            {"name": "Marauder Mini", "asset_contains": "launcher-marauder-mini.bin", "json": "marauder.json"},
            {"name": "Marauder V4", "asset_contains": "launcher-marauder-v4-og.bin", "json": "marauder.json"},
            {"name": "Marauder V6", "asset_contains": "launcher-marauder-v61.bin", "json": "marauder.json"},
            {"name": "Marauder V7", "asset_contains": "launcher-marauder-v7.bin", "json": "marauder.json"},
            {"name": "Phantom S024R", "asset_contains": "launcher-phantom_s024r.bin", "json": "phantom.json"},
            {"name": "Smoochiee V2", "asset_contains": "launcher-smoochiee-board.bin", "json": "smoochiee_v2.json"},
        ],
    },
    {
        "name": "Bruce",
        "repo_owner": "BruceDevices",
        "repo_name": "firmware",
        "author": "pr3y",
        "cover": "41d9e573f8b7aca442af27f54d788a94.gif",
        "description": "VISIT https://bruce.computer\nCHECK OUR GITHUB! supporting CC1101 and NRF24\nhttps://github.com/BruceDevices/firmware\nWiFi - BLE - RFID - RF - GPS - FM - NRF24 - Connect - Others\n### Our wiki: https://github.com/BruceDevices/bruce/wiki ### Discord: https://discord.gg/WJ9XF9czVT",
        "fid_prefix": "Bruce",
        "devices": [
            {"name": "T-Deck", "asset_contains": "Bruce-lilygo-t-deck.bin", "json": "t-deck.json"},
            {"name": "T-Deck Plus", "asset_contains": "Bruce-lilygo-t-deck-pro.bin", "json": "t-deck.json"},
            {"name": "T-Embed", "asset_contains": "Bruce-lilygo-t-embed.bin", "json": "t-embed-cc1101.json"},
            {"name": "T-Embed CC1101", "asset_contains": "Bruce-lilygo-t-embed-cc1101.bin", "json": "t-embed-cc1101.json"},
            {"name": "T-HMI", "asset_contains": "Bruce-lilygo-t-hmi.bin", "json": "t-hmi.json"},
            {"name": "T-LoraPager", "asset_contains": "Bruce-lilygo-t-lora-pager.bin", "json": "t-lora-pager.json"},
            {"name": "T-Watch S3", "asset_contains": "Bruce-lilygo-t-watch-s3.bin", "json": "t-watch-s3.json"},
            {"name": "CYD S028R", "asset_contains": "Bruce-LAUNCHER_cyd-2432s028.bin", "json": "CYD.json"},
            {"name": "CYD 2USB", "asset_contains": "Bruce-LAUNCHER_cyd-2-usb.bin", "json": "CYD.json"},
            {"name": "CYD S024R", "asset_contains": "Bruce-LAUNCHER_cyd-2432s024r.bin", "json": "CYD.json"},
            {"name": "CYD W328R", "asset_contains": "Bruce-LAUNCHER_cyd-2432w328r.bin", "json": "CYD.json"},
            {"name": "CYD W328C", "asset_contains": "Bruce-LAUNCHER_cyd-2432w328c.bin", "json": "CYD.json"},
            {"name": "Awok Mini", "asset_contains": "Bruce-awok-mini.bin", "json": "marauder.json"},
            {"name": "Awok Touch", "asset_contains": "Bruce-awok-touch.bin", "json": "marauder.json"},
            {"name": "Marauder Mini", "asset_contains": "Bruce-LAUNCHER_marauder-mini.bin", "json": "marauder.json"},
            {"name": "Marauder V4", "asset_contains": "Bruce-LAUNCHER_marauder-v4-og.bin", "json": "marauder.json"},
            {"name": "Marauder V6", "asset_contains": "Bruce-LAUNCHER_marauder-v61.bin", "json": "marauder.json"},
            {"name": "Marauder V7", "asset_contains": "Bruce-LAUNCHER_marauder-v7.bin", "json": "marauder.json"},
            {"name": "Phantom S024R", "asset_contains": "Bruce-LAUNCHER_phantom_s024r.bin", "json": "phantom.json"},
            {"name": "Smoochiee V2", "asset_contains": "Bruce-smoochiee-board.bin", "json": "smoochiee_v2.json"},
        ],
    },
    {
        "name": "Ratdeck",
        "repo_owner": "ratspeak",
        "repo_name": "ratdeck",
        "author": "ratspeak",
        "cover": "979129683efffd3ad701c6eb0cb79ce6.png",
        "description": "Standalone Reticulum for T-Deck, based on microReticulum.",
        "fid_prefix": "Ratdeck",
        "devices": [
            {"name": "T-Deck", "asset_contains": "ratdeck-merged.bin", "json": "t-deck.json"},
        ],
    },
    {
        "name": "Pyxis",
        "repo_owner": "torlando-tech",
        "repo_name": "pyxis",
        "author": "torlando-tech",
        "cover": "https://github.com/torlando-tech/pyxis/blob/main/pyxis-icon.svg",
        "description": "An LXMF and LXST client firmware for T-Deck.",
        "fid_prefix": "Ratdeck",
        "devices": [
            {"name": "T-Deck", "asset_contains": "firmware.bin", "json": "t-deck.json"},
        ],
    },
]

# ============================================================================
# FUNÇÕES UTILITÁRIAS
# ============================================================================

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
HEADERS = {
    "Authorization": f"token {GITHUB_TOKEN}",
    "Accept": "application/vnd.github+json"
}

def generate_fid(fw_prefix: str, device_name: str) -> str:
    """Gera um fid estável a partir do nome do firmware e dispositivo."""
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


def _get_github_headers():
    """Retorna headers com autenticação GitHub se disponível."""
    github_token = os.getenv("GITHUB_TOKEN")
    headers = {}
    if github_token:
        headers["Authorization"] = f"token {github_token}"
        headers["Accept"] = "application/vnd.github+json"
    return headers


def fetch_all_releases(repo_owner: str, repo_name: str):
    """Busca todas as releases de um repositório."""
    releases = []
    url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/releases"
    headers = _get_github_headers()
    while url:
        resp = requests.get(url, params={"per_page": 100}, headers=headers)
        if resp.status_code != 200:
            raise Exception(f"Erro ao acessar GitHub API: {resp.status_code}")
        releases.extend(resp.json())
        url = _parse_next_link(resp.headers.get("Link"))
    return releases


def atualizar_firmware(fw_config: dict):
    """Atualiza todos os devices de um firmware."""
    repo_owner = fw_config["repo_owner"]
    repo_name = fw_config["repo_name"]
    author = fw_config["author"]
    github_url = f"https://github.com/{repo_owner}/{repo_name}"
    cover = fw_config["cover"]
    description = fw_config["description"]
    fid_prefix = fw_config["fid_prefix"]
    devices = fw_config["devices"]

    print(f"\n{'=' * 60}")
    print(f"Processando {fw_config['name']}...")
    print(f"{'=' * 60}")

    releases = fetch_all_releases(repo_owner, repo_name)
    devices_by_json = {}
    for device in devices:
        devices_by_json.setdefault(device["json"], []).append(device)

    for json_filename, json_devices in devices_by_json.items():
        json_path = os.path.join(os.path.dirname(__file__), json_filename)
        lista = _load_json_file(json_path)

        # Trabalha apenas com os fids declarados no config para este JSON.
        # Isso evita apagar entradas de outros firmwares ou dispositivos que
        # não estejam sendo processados aqui.
        expected_fids = {generate_fid(fid_prefix, device["name"]) for device in json_devices}

        existing_entries = {
            entry.get("fid"): entry
            for entry in lista
            if entry.get("fid") in expected_fids
            and entry.get("github") == github_url
            and entry.get("author") == author
        }

        # Remove entradas antigas desses dispositivos para reescrevê-las no fim
        # com a lista combinada de versões.
        lista = [entry for entry in lista if entry.get("fid") not in expected_fids]

        for device in json_devices:
            fid = generate_fid(fid_prefix, device["name"])
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

            combined_versions = []
            if fid in existing_entries:
                combined_versions.extend(existing_entries[fid].get("versions", []))
            combined_versions.extend(new_versions)

            if not combined_versions:
                print(f"  {device['name']}: Nenhuma versão disponível")
                continue

            # Manter apenas as últimas 10 versões
            combined_versions.sort(key=lambda v: v["published_at"], reverse=True)
            combined_versions = combined_versions[:10]
            firmware_display_name = f"{fw_config['name']} ({device['name']})"

            new_entry = {
                "name": firmware_display_name,
                "author": author,
                "description": description,
                "cover": cover,
                "github": github_url,
                "fid": fid,
                "versions": combined_versions,
            }

            lista.append(new_entry)
            if new_versions:
                print(f"  {device['name']}: +{len(new_versions)} versão(ões) em {json_filename}")
            else:
                print(f"  {device['name']}: Nenhuma versão nova")

        _save_json_file(json_path, lista)


if __name__ == "__main__":
    for fw_config in FIRMWARE_CONFIGS:
        try:
            atualizar_firmware(fw_config)
        except Exception as e:
            print(f"Erro ao processar {fw_config['name']}: {e}")

    print(f"\n{'=' * 60}")
    print("Processo concluído!")
    print(f"{'=' * 60}")
