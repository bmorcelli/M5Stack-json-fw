import base64
import fnmatch
import hashlib
import json
import os
from datetime import datetime

import requests

# ============================================================================
# CARREGAMENTO DE CONFIGURAÇÕES
# ============================================================================

def load_firmware_configs():
    """
    Carrega as configurações de firmware do arquivo update_firmware.json
    
    Este arquivo contém todas as configurações de firmware em formato JSON.
    Para editar as configurações, modifique apenas o arquivo update_firmware.json.
    
    Estrutura das configurações:
    - name: Nome do firmware
    - repo_owner: Proprietário do repositório no GitHub
    - repo_name: Nome do repositório
    - author: Autor do firmware
    - cover: URL ou hash da imagem de capa
    - description: Descrição do firmware
    - fid_prefix: Prefixo usado para gerar IDs únicos (FIDs)
    - devices: Lista de dispositivos suportados
      - name: Nome do dispositivo
      - asset_contains: Substring ou padrão com '*' para identificar o arquivo
      - json: Arquivo JSON do database onde as informações serão salvas
    """
    source_file = os.path.join(os.path.dirname(__file__), "update_firmware.json")
    try:
        with open(source_file, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"ERRO: Arquivo {source_file} não encontrado!")
        return []
    except json.JSONDecodeError as e:
        print(f"ERRO: Falha ao parsear JSON em {source_file}: {e}")
        return []

FIRMWARE_CONFIGS = load_firmware_configs()

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
        headers["Authorization"] = f"Bearer {github_token}"
        headers["Accept"] = "application/vnd.github+json"
        print("[update_firmware.py] GitHub token encontrado e configurado", flush=True)
    else:
        print("[update_firmware.py] AVISO: GitHub token não encontrado, usando limite anônimo", flush=True)
    return headers


def _asset_matches(asset_name: str, asset_contains: str) -> bool:
    """
    Faz match de asset por substring simples ou por padrão com wildcard '*'.

    Compatibilidade:
    - sem wildcard: comportamento antigo, usando substring
    - com wildcard: usa match do nome completo, case-insensitive
    """
    normalized_asset_name = asset_name.lower()
    normalized_pattern = asset_contains.lower()

    if "*" in normalized_pattern:
        return fnmatch.fnmatchcase(normalized_asset_name, normalized_pattern)

    return normalized_pattern in normalized_asset_name


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
    allow_prerelease = fw_config.get("pre_release", False)

    print(f"\n{'=' * 60}")
    print(f"Processando {fw_config['name']}...")
    print(f"{'=' * 60}")

    releases = fetch_all_releases(repo_owner, repo_name)
    devices_by_json = {}
    for device in devices:
        devices_by_json.setdefault(device["json"], []).append(device)

    for json_filename, json_devices in devices_by_json.items():
        json_path = os.path.join(os.path.dirname(__file__), "database", json_filename)
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
                is_draft = rel.get("draft", False)
                is_prerelease = rel.get("prerelease", False)
                
                # Se é draft, sempre ignora
                if is_draft:
                    continue
                
                # Se é prerelease, só aceita se o firmware permitir
                if is_prerelease and not allow_prerelease:
                    continue

                tag = rel.get("tag_name")
                published_at = rel.get("published_at", "")[:10]

                matching_asset = None
                for asset in rel.get("assets", []):
                    if _asset_matches(asset.get("name", ""), device["asset_contains"]):
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
