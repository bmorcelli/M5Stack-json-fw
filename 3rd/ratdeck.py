import requests
import os
import json
from datetime import datetime

# https://github.com/ratspeak/ratdeck/releases
REPO_OWNER = "ratspeak"
REPO_NAME = "ratdeck"
FW_FID = "CFWFIQGOHMB27TVKXUVP3TJAX2BQMDDO"
API_URL = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/releases"
FILE_NAME = "ratdeck-merged.bin"
LISTA_JSON = "t-deck.json"

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

def atualizar_somente_ratdeck(releases: list):
    # Carregar o JSON da lista de firmwares
    with open(LISTA_JSON, 'r') as f:
        data = json.load(f)
    
    # Encontrar o firmware do Ratdeck pelo FID
    ratdeck_firmware = None
    for fw in data:
        if fw.get('fid') == FW_FID:
            ratdeck_firmware = fw
            break
    
    if not ratdeck_firmware:
        print("Firmware Ratdeck não encontrado na lista.")
        return
    
    # Obter versões existentes
    existing_versions = {v['version'] for v in ratdeck_firmware['versions']}
    
    new_versions = []
    
    for release in releases:
        tag = release['tag_name']
        if tag in existing_versions:
            continue
        
        # Verificar se a release possui o arquivo "ratdeck-merged.bin"
        has_bin = any(asset['name'] == FILE_NAME for asset in release.get('assets', []))
        if has_bin:
            published_at = release['published_at'][:10]  # Formato yyyy-mm-dd
            file_url = f"https://github.com/{REPO_OWNER}/{REPO_NAME}/releases/download/{tag}/{FILE_NAME}"
            new_versions.append({
                "version": tag,
                "published_at": published_at,
                "file": file_url
            })
    
    # Adicionar novas versões à lista
    ratdeck_firmware['versions'].extend(new_versions)
    
    # Salvar o JSON atualizado
    with open(LISTA_JSON, 'w') as f:
        json.dump(data, f, indent=4)
    
    print(f"Adicionadas {len(new_versions)} novas versões ao firmware Ratdeck.")


if __name__ == "__main__":
    releases = fetch_all_releases()  # Buscar releases uma vez por execução
    atualizar_somente_ratdeck(releases)

