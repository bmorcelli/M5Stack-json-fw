import requests
import zipfile
import io
import os
import json
from datetime import datetime

# Configurações
SOURCE_REPO = "jaylikesbunda/Ghost_ESP"
TARGET_REPO = "bmorcelli/M5Stack-json-fw"
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
FILES_TO_PROCESS = [
    "AwokMini.zip", "Crowtech_LCD.zip", "CYD2USB.zip", "CYD2USB2.4Inch.zip",
    "CYD2USB2.4Inch_C.zip", "CYDDualUSB.zip", "CYDMicroUSB.zip", "MarauderV6_AwokDual.zip", "LilyGo-TEmbedC1101.zip", "LilyGo-T-Deck.zip"
]

LISTA_MARAUDER = "./3rd/marauder.json"
LISTA_CYD = "./3rd/CYD.json"
LISTA_PHANTOM = "./3rd/phantom.json"
LISTA_TEMBED = "./3rd/t-embed-cc1101.json"
LISTA_TDECK = "./3rd/t-deck.json"

HEADERS = {
    "Authorization": f"token {GITHUB_TOKEN}",
    "Accept": "application/vnd.github+json"
}

def get_latest_release(repo):
    url = f"https://api.github.com/repos/{repo}/releases/latest"
    r = requests.get(url)
    r.raise_for_status()
    return r.json()

def download_and_extract_bin(asset_url, zip_name):
    r = requests.get(asset_url)
    r.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        with z.open("Ghost_ESP_IDF.bin") as bin_file:
            bin_data = bin_file.read()
            bin_name = zip_name.replace(".zip", ".bin")
            with open(bin_name, "wb") as f:
                f.write(bin_data)
            return bin_name

def atualizar_lista_json(path, binaries, version, published_date):
    if os.path.exists(path):
        with open(path, "r") as f:
            lista = json.load(f)
    else:
        lista = []

    # Remove entradas antigas Ghost
    lista = [entry for entry in lista if not entry.get("name", "").lower().startswith("ghost")]

    nova_entry = {
        "name": f"Ghost {version}",
        "author": "jaylikesbunda",
        "versions": [
            {
                "version": name.replace(".bin", ""),
                "published_at": published_date,
                "file": f"https://github.com/bmorcelli/M5Stack-json-fw/releases/download/GhostESP/{name}"
            } for name in binaries
        ]
    }

    lista.append(nova_entry)

    with open(path, "w") as f:
        json.dump(lista, f, indent=4)

def main():
    source_release = get_latest_release(SOURCE_REPO)
    version = source_release["tag_name"]
    published_at = datetime.strptime(source_release["published_at"], "%Y-%m-%dT%H:%M:%SZ").strftime("%d/%m/%y")

    # Verificar se a versão já existe no JSON principal
    if os.path.exists(LISTA_MARAUDER):
        with open(LISTA_MARAUDER, "r") as f:
            lista = json.load(f)
        ghost_entry = next((entry for entry in lista if entry.get("name", "").lower() == f"ghost {version}".lower()), None)
        if ghost_entry:
            print(f"Versão Ghost {version} já presente. Nenhuma ação necessária.")
            return

    # Download + extração
    all_binaries = []
    binaries_cyd = []
    binary_phantom = None
    binary_tembed = None
    binary_tdeck = None

    for asset in source_release["assets"]:
        if asset["name"] in FILES_TO_PROCESS:
            print(f"Processando {asset['name']}...")
            bin_path = download_and_extract_bin(asset["browser_download_url"], asset["name"])
            if asset["name"] == "LilyGo-TEmbedC1101.zip":
                binary_tembed = bin_path
            elif asset["name"] == "LilyGo-T-Deck.zip":
                binary_tdeck = bin_path
            else:
                all_binaries.append(bin_path)

                if asset["name"].startswith("CYD"):
                    binaries_cyd.append(bin_path)
                    if asset["name"] == "CYD2USB2.4Inch.zip":
                        binary_phantom = bin_path


    # Atualizar JSONs
    atualizar_lista_json(LISTA_MARAUDER, all_binaries, version, published_at)
    if binaries_cyd:
        atualizar_lista_json(LISTA_CYD, binaries_cyd, version, published_at)
    if binary_phantom:
        atualizar_lista_json(LISTA_PHANTOM, [binary_phantom], version, published_at)
    if binary_tembed:
        atualizar_lista_json(LISTA_TEMBED, [binary_tembed], version, published_at)
    if binary_tdeck:
        atualizar_lista_json(LISTA_TDECK, [binary_tdeck], version, published_at)

if __name__ == "__main__":
    main()
