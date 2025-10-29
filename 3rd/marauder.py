import requests
import os
import json
from datetime import datetime

REPO_OWNER = "justcallmekoko"
REPO_NAME = "ESP32Marauder"
API_URL = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/releases/latest"
LISTA_JSON = "./3rd/marauder.json"

VERSION_MAP = {
    "guition": "GUITON",
    "s028": "CYD S028R",
    "2usb":"CYD2USB",
    "mini": "Mini",
    "old": "V4",
    "v6_1": "v6.x",
    "v6": "V6",
    "v7_1": "V7.1",
    "v7": "V7",
}

def infer_version_name(filename):
    name = filename.lower()
    for key, version in VERSION_MAP.items():
        if key in name:
            return version
    return None

def carregar_lista_existente():
    if os.path.exists(LISTA_JSON):
        with open(LISTA_JSON, "r") as f:
            return json.load(f)
    return []

def salvar_lista(lista):
    with open(LISTA_JSON, "w") as f:
        json.dump(lista, f, indent=4)

def atualizar_somente_marauder():
    response = requests.get(API_URL)
    if response.status_code != 200:
        raise Exception(f"Erro ao acessar GitHub API: {response.status_code}")

    release = response.json()
    tag = release['tag_name']
    published_iso = release['published_at']
    published_at = datetime.strptime(published_iso, "%Y-%m-%dT%H:%M:%SZ").strftime("%d/%m/%y")
    assets = release['assets']

    versions = []
    for asset in assets:
        if asset['name'].endswith(".bin"):
            version_name = infer_version_name(asset['name'])
            if version_name:
                versions.append({
                    "version": version_name,
                    "published_at": published_at,
                    "file": asset['browser_download_url']
                })

    if not versions:
        print("Nenhum arquivo .bin válido encontrado na última release.")
        return

    nova_entry = {
        "name": f"Marauder {tag}",
        "author": "JustCallMeKoko",
        "description": "A suite of WiFi/Bluetooth offensive and defensive tools for the ESP32. Use Launcher to install",
        "cover": "3183675bd52534e015bcc8ae2ae603a0.png",
        "fid": "CFWP2BWSUATAASEAEN3IAMKMVSL4WFS3",
        "versions": versions
    }

    lista_atual = carregar_lista_existente()

    # Remove entradas antigas do Marauder
    nova_lista = [
        entry for entry in lista_atual
        if not (entry.get("author") == "JustCallMeKoko" or entry.get("name", "").startswith("Marauder"))
    ]

    nova_lista.append(nova_entry)
    salvar_lista(nova_lista)
    print(f"Marauder {tag} atualizado em {LISTA_JSON}, mantendo os demais firmwares.")

if __name__ == "__main__":
    # Atualiza a Lista do Marauder completo
    atualizar_somente_marauder()

    #atualiza a Lista do CYD
    LISTA_JSON = "./3rd/CYD.json"
    VERSION_MAP = {
        "guition": "GUITON",
        "s028": "CYD S028R",
        "2usb":"CYD2USB",
    }
    atualizar_somente_marauder()

    #atualiza a Lista do Phantom
    LISTA_JSON = "./3rd/phantom.json"
    VERSION_MAP = {
        "guition": "Phantom",
    }
    atualizar_somente_marauder()

