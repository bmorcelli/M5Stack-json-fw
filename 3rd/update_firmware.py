import base64
import fnmatch
import hashlib
import json
import os
import re
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
    - pre_release: Autoriza pre-releases para serem adicionadas ao arquivo
    - only_pre_releases: Adiciona SOMENTE pre-releases (caso de beta firmwares)
    - files_on_repo: Quando true, os binários dos devices NÃO são assets da
      release (não estão no tarball anexado a ela), e sim arquivos versionados
      dentro do próprio repositório. Nesse modo, os devices usam os campos
      "*_on_repo" abaixo em vez de "asset_contains"/"bootloader"/etc., e cada
      link é resolvido como:
      https://github.com/{repo_owner}/{repo_name}/raw/refs/tags/{tag}/{caminho}
    - devices: Lista de dispositivos suportados
      - name: Nome do dispositivo, usado apenas para gerar o fid (não aparece
        mais no nome exibido do firmware)
      - variant: Opcional. Quando presente, é adicionado entre parênteses ao
        nome do firmware exibido (ex.: "Marauder (V8)"). Sem variant, o nome
        exibido é só o nome do firmware, igual para todos os devices dele
      - asset_contains: Substring ou padrão com '*' para identificar o arquivo
        do firmware entre os assets da release (modo padrão, sem files_on_repo)
      - bootloader / bootloader_contains: link direto ou padrão de busca nos
        assets da release para o bootloader (modo padrão)
      - partitions / partitions_contains: idem, para o binário de partitions
      - data / data_contains: idem, para o binário de data
      - asset_on_repo: caminho do binário do firmware dentro do repositório
        (usado somente quando files_on_repo=true)
      - bootloader_on_repo: caminho do bootloader dentro do repositório
        (files_on_repo=true). Se ausente ou null, o device não tem bootloader
      - partition_on_repo: caminho do binário de partitions dentro do
        repositório (files_on_repo=true). Se ausente ou null, não existe
      - data_on_repo: caminho do binário de data dentro do repositório
        (files_on_repo=true). Se ausente ou null, não existe
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


# Campos opcionais de binários auxiliares que podem ser declarados por device
# em update_firmware.json. Cada um aceita duas formas:
#   - "<campo>": link direto para o binário (bootloader/partitions/data)
#   - "<campo>_contains": substring/padrão para localizar o binário nos assets
#     da release (mesma lógica de asset_contains)
AUXILIARY_BINARY_FIELDS = ("bootloader", "partitions", "data")


def _normalize_binary_url(url: str) -> str:
    """Converte links github.com/.../blob/... em .../raw/... para download direto."""
    if not url:
        return url
    return re.sub(r"^(https://github\.com/[^/]+/[^/]+)/blob/", r"\1/raw/", url)


def _resolve_auxiliary_link(device: dict, release: dict, field: str):
    """Resolve o link de um binário auxiliar (bootloader/partitions/data).

    Prioriza o link direto ("<field>"); caso ausente, procura nos assets da
    release usando "<field>_contains" (mesma lógica de asset_contains).
    """
    direct = device.get(field)
    if direct:
        return _normalize_binary_url(direct)

    contains = device.get(f"{field}_contains")
    if contains and release:
        for asset in release.get("assets", []):
            if _asset_matches(asset.get("name", ""), contains):
                return asset.get("browser_download_url")
    return None


def _apply_auxiliary_links(version: dict, device: dict, release: dict) -> None:
    """Adiciona bootloader/partitions/data à versão quando declarados no device."""
    for field in AUXILIARY_BINARY_FIELDS:
        if field in version:
            continue
        link = _resolve_auxiliary_link(device, release, field)
        if link:
            version[field] = link


def _should_include_release(release: dict, allow_prerelease: bool, only_prerelease: bool) -> bool:
    """Determina se uma release do GitHub deve ser incluída no JSON do firmware."""
    is_draft = release.get("draft", False)
    is_prerelease = release.get("prerelease", False)

    if is_draft:
        return False

    if is_prerelease:
        return allow_prerelease or only_prerelease

    return not only_prerelease


# Campos "*_on_repo" declarados por device quando o firmware usa
# files_on_repo=true, e a chave correspondente no dicionário de versão salvo
# no database. "asset_on_repo" não entra aqui pois mapeia para "file" e é
# tratado separadamente (é obrigatório, os demais são opcionais).
ON_REPO_AUXILIARY_FIELDS = {
    "bootloader_on_repo": "bootloader",
    "partition_on_repo": "partitions",
    "data_on_repo": "data",
}


def _parse_owner_repo(github_url: str):
    """Extrai (owner, repo) de uma URL https://github.com/{owner}/{repo}[...].

    Usado como fallback quando repo_owner/repo_name não são informados no
    config (por exemplo, em firmwares com files_on_repo onde só o campo
    "github" foi preenchido).
    """
    if not github_url:
        return None, None
    match = re.match(r"^https?://github\.com/([^/]+)/([^/]+?)/?(?:/.*)?$", github_url.strip())
    if not match:
        return None, None
    return match.group(1), match.group(2)


def _build_on_repo_url(repo_owner: str, repo_name: str, tag: str, path: str) -> str:
    """Monta o link raw de um arquivo versionado dentro do próprio repositório."""
    return f"https://github.com/{repo_owner}/{repo_name}/raw/refs/tags/{tag}/{path}"


def _url_exists(url: str) -> bool:
    """Verifica se um arquivo existe em `url` sem baixá-lo por completo.

    Faz HEAD primeiro (rápido, sem corpo). Como alguns hosts (ex.: redirects
    do GitHub) não suportam HEAD corretamente, um retorno ambíguo (405, 403,
    5xx) é confirmado com um GET em streaming, fechado imediatamente após o
    status ser lido.
    """
    try:
        resp = requests.head(url, allow_redirects=True, timeout=15)
        if resp.status_code == 200:
            return True
        if resp.status_code in (403, 405) or resp.status_code >= 500:
            resp = requests.get(url, allow_redirects=True, timeout=15, stream=True)
            resp.close()
            return resp.status_code == 200
        return False
    except requests.RequestException:
        return False


def _find_versions_on_repo(
    device: dict,
    repo_owner: str,
    repo_name: str,
    releases: list,
    existing_versions: set,
    allow_prerelease: bool,
    only_prerelease: bool,
) -> list:
    """Localiza versões de um device cujos binários vivem dentro do próprio
    repositório (files_on_repo=true), em vez de como assets de release.

    Percorre as releases da mais recente para a mais antiga (ordem já
    retornada pela API do GitHub) e, para cada uma, monta o link raw da tag
    e confirma via HTTP se o arquivo principal (asset_on_repo) existe. A
    busca para em dois casos, para evitar checagens desnecessárias:
      - ao alcançar uma tag já presente em existing_versions, ou seja, já
        processada em uma execução anterior;
      - ao encontrar a primeira release (mais recente -> mais antiga) em que
        o arquivo principal não existe mais, assumindo que releases ainda
        mais antigas também não o terão.
    """
    asset_path = device.get("asset_on_repo")
    if not asset_path:
        return []

    new_versions = []
    for release in releases:
        tag = release.get("tag_name")
        if tag in existing_versions:
            break

        if not _should_include_release(release, allow_prerelease, only_prerelease):
            continue

        asset_url = _build_on_repo_url(repo_owner, repo_name, tag, asset_path)
        if not _url_exists(asset_url):
            break

        version = {
            "version": tag,
            "published_at": release.get("published_at", "")[:10],
            "file": asset_url,
        }

        for field, version_key in ON_REPO_AUXILIARY_FIELDS.items():
            aux_path = device.get(field)
            if not aux_path:
                continue
            aux_url = _build_on_repo_url(repo_owner, repo_name, tag, aux_path)
            if _url_exists(aux_url):
                version[version_key] = aux_url

        new_versions.append(version)

    return new_versions


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
    if not repo_owner or not repo_name:
        # Fallback para configs (ex.: files_on_repo) que só preenchem "github"
        parsed_owner, parsed_repo = _parse_owner_repo(fw_config.get("github"))
        repo_owner = repo_owner or parsed_owner
        repo_name = repo_name or parsed_repo
    if not repo_owner or not repo_name:
        raise ValueError(
            f"Não foi possível determinar repo_owner/repo_name para {fw_config.get('fid_prefix')}"
        )
    author = fw_config["author"]
    github_url = f"https://github.com/{repo_owner}/{repo_name}"
    cover = fw_config["cover"]
    description = fw_config["description"]
    fid_prefix = fw_config["fid_prefix"]
    devices = fw_config["devices"]
    allow_prerelease = fw_config.get("pre_release", False)
    only_prerelease = fw_config.get("only_pre_releases", False)
    files_on_repo = fw_config.get("files_on_repo", False)

    print(f"\n{'=' * 60}")
    print(f"Processando {fw_config['name']}...")
    print(f"{'=' * 60}")

    releases = fetch_all_releases(repo_owner, repo_name)
    releases_by_tag = {rel.get("tag_name"): rel for rel in releases}
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

            if files_on_repo:
                new_versions = _find_versions_on_repo(
                    device, repo_owner, repo_name, releases,
                    existing_versions, allow_prerelease, only_prerelease,
                )
            else:
                new_versions = []
                for rel in releases:
                    if not _should_include_release(rel, allow_prerelease, only_prerelease):
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

            # Propaga bootloader/partitions/data para todas as versões (novas e
            # já existentes), usando a release correspondente a cada tag para
            # resolver as variantes "_contains".
            for version in combined_versions:
                _apply_auxiliary_links(
                    version, device, releases_by_tag.get(version.get("version"))
                )

            variant = device.get("variant")
            firmware_display_name = (
                f"{fw_config['name']} ({variant})" if variant else fw_config["name"]
            )

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
