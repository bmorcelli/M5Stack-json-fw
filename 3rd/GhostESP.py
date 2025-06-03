import requests
import zipfile
import io
import os
import json
from datetime import datetime

# Configurações
SOURCE_REPO = "jaylikesbunda/Ghost_ESP"
TARGET_REPO = "bmorcelli/M5Stack-json-fw"
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")  # Secrets do GitHub Actions
FILES_TO_PROCESS = [
    "AwokMini.zip", "Crowtech_LCD.zip", "CYD2USB.zip", "CYD2USB2.4Inch.zip",
    "CYD2USB2.4Inch_C.zip", "CYDDualUSB.zip", "CYDMicroUSB.zip"
]
TARGET_TAG = "GhostESP"
LISTA_JSON_PATH = "./3rd/lista.json"

# Headers com token
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

def delete_existing_assets(repo, release_id):
    assets_url = f"https://api.github.com/repos/{repo}/releases/{release_id}/assets"
    r = requests.get(assets_url)
    r.raise_for_status()
    for asset in r.json():
        requests.delete(asset["url"])

def upload_asset(repo, release_id, filepath):
    upload_url = f"https://uploads.github.com/repos/{repo}/releases/{release_id}/assets"
    filename = os.path.basename(filepath)
    with open(filepath, "rb") as f:
        params = {"name": filename}
        headers = HEADERS.copy()
        headers["Content-Type"] = "application/octet-stream"
        r = requests.post(f"{upload_url}?name={filename}", headers=headers, data=f.read())
        r.raise_for_status()
        return f"https://github.com/{repo}/releases/download/{TARGET_TAG}/{filename}"

def atualizar_lista_json(binaries, version, published_date):
    if os.path.exists(LISTA_JSON_PATH):
        with open(LISTA_JSON_PATH, "r") as f:
            lista = json.load(f)
    else:
        lista = []

    # Remove entradas antigas do Ghost
    lista = [entry for entry in lista if entry.get("name", "").lower().find("ghost") == -1]

    nova_entry = {
        "name": f"Ghost {version}",
        "author": "jaylikesbunda",
        "versions": [
            {
                "version": name.replace(".bin", ""),
                "published_at": published_date,
                "file": url
            } for name, url in binaries.items()
        ]
    }

    lista.append(nova_entry)

    with open(LISTA_JSON_PATH, "w") as f:
        json.dump(lista, f, indent=4)

def main():
    source_release = get_latest_release(SOURCE_REPO)
    version = source_release["tag_name"]
    published_at = datetime.strptime(source_release["published_at"], "%Y-%m-%dT%H:%M:%SZ").strftime("%d/%m/%y")

    # Verificar versão atual registrada em lista.json
    if os.path.exists(LISTA_JSON_PATH):
        with open(LISTA_JSON_PATH, "r") as f:
            lista = json.load(f)
        ghost_entry = next((entry for entry in lista if entry.get("name", "").lower().startswith("ghost")), None)
        if ghost_entry and ghost_entry["name"] == f"Ghost {version}":
            print(f"Versão Ghost {version} já presente em lista.json. Nenhuma ação necessária.")
            return
    else:
        lista = []

    # Extrair e renomear binários
    binaries = {}
    for asset in source_release["assets"]:
        if asset["name"] in FILES_TO_PROCESS:
            print(f"Processando {asset['name']}...")
            bin_path = download_and_extract_bin(asset["browser_download_url"], asset["name"])
            binaries[bin_path] = None  # será preenchido com a URL depois

    # Obter release "ghost" do repositório atual
    resp = requests.get(f"https://api.github.com/repos/{TARGET_REPO}/releases")
    resp.raise_for_status()
    target_releases = resp.json()
    ghost_release = next((r for r in target_releases if r["tag_name"] == TARGET_TAG), None)
    if not ghost_release:
        raise Exception("Release 'GhostESP' não encontrada no repositório destino.")

    release_id = ghost_release["id"]
    delete_existing_assets(TARGET_REPO, release_id)

    # Fazer upload dos .bin
    for bin_path in binaries:
        url = upload_asset(TARGET_REPO, release_id, bin_path)
        binaries[bin_path] = url
    
    for bin_path in binaries:
        try:
            os.remove(bin_path)
            print(f"Removido: {bin_path}")
        except Exception as e:
            print(f"Erro ao remover {bin_path}: {e}")

    # Atualizar lista.json
    atualizar_lista_json(binaries, version, published_at)


if __name__ == "__main__":
    main()
