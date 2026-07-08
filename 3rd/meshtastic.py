import json
import os
from urllib.request import Request, urlopen

REPO_OWNER = "meshtastic"
REPO_NAME = "firmware"
MIRROR_OWNER = "meshtastic"
MIRROR_REPO = "meshtastic.github.io"

FIRMWARE_TEMPLATE = {
    "author": "Meshtastic",
    "cover": "https://meshtastic.org/img/logo.svg",
    "logic": "meshtastic",
    "github": "https://meshtastic.org/",
    "description": (
        "An open source, off-grid, decentralized mesh network built to run on "
        "affordable, low-power devices. No cell towers. No internet. Just pure "
        "peer-to-peer connectivity."
    ),
}

# Lista de dispositivos rastreados. Para adicionar um novo alvo, basta incluir
# uma nova entrada aqui com o "target" usado nos nomes de arquivo do
# repositório mirror (meshtastic.github.io) e o arquivo do database onde a
# entrada deve ser criada/atualizada.
DEVICES = [
    {"target": "t-deck", "json": "t-deck.json"},
    {"target": "t-deck-tft", "json": "t-deck.json", "is_fancy": True},
    {"target": "t-deck-pro", "json": "t-deck-pro.json"},
    {"target": "t-deck-pro-v1_1", "json": "t-deck-pro.json", "variant": "v1.1"},
    {"target": "tlora-pager", "json": "t-lora-pager.json"},
    {"target": "t5s3-epaper-v1", "json": "t-t5s3.json", "variant": "v1.0"},
    {"target": "t5s3-epaper-v2", "json": "t-t5s3.json", "variant": "v1.1"},
    {"target": "t-watch-s3", "json": "t-watch-s3.json"},
]

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
HEADERS = {"User-Agent": "M5Stack-json-fw/meshtastic"}
if GITHUB_TOKEN:
    HEADERS["Authorization"] = f"Bearer {GITHUB_TOKEN}"
    HEADERS["Accept"] = "application/vnd.github+json"
    print("[meshtastic.py] GitHub token encontrado e configurado", flush=True)
else:
    print("[meshtastic.py] AVISO: GitHub token não encontrado, usando limite anônimo", flush=True)


def _api_get(url: str, params: dict = None):
    if params:
        from urllib.parse import urlencode
        url = f"{url}?{urlencode(params)}"
    request = Request(url, headers=HEADERS)
    with urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode("utf-8")), response.headers


def _parse_next_link(link_header: str):
    if not link_header:
        return None
    for part in [p.strip() for p in link_header.split(",")]:
        if 'rel="next"' in part:
            url = part.split(";")[0].strip()
            if url.startswith("<") and url.endswith(">"):
                return url[1:-1]
    return None


def fetch_all_releases():
    """Busca todas as releases de meshtastic/firmware."""
    releases = []
    url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/releases"
    while url:
        data, headers = _api_get(url, {"per_page": 100} if "?" not in url else None)
        releases.extend(data)
        url = _parse_next_link(headers.get("Link"))
    return releases


def fetch_beta_releases():
    """Filtra as releases mantendo apenas as Beta (excluindo as revogadas)."""
    betas = []
    for release in fetch_all_releases():
        if release.get("draft"):
            continue
        name = release.get("name") or ""
        if "Revoked" in name:
            continue
        if "Beta" not in name:
            continue
        tag = release.get("tag_name", "")
        version = tag[1:] if tag.startswith("v") else tag
        betas.append(
            {
                "version": version,
                "published_at": (release.get("published_at") or "")[:10],
            }
        )
    return betas


def fetch_mirror_state():
    """Retorna (commit_sha, set de paths) do repositório mirror meshtastic.github.io."""
    commit, _ = _api_get(f"https://api.github.com/repos/{MIRROR_OWNER}/{MIRROR_REPO}/commits/master")
    commit_sha = commit["sha"]

    tree, _ = _api_get(
        f"https://api.github.com/repos/{MIRROR_OWNER}/{MIRROR_REPO}/git/trees/{commit_sha}",
        {"recursive": "1"},
    )
    paths = {item["path"] for item in tree.get("tree", []) if item.get("type") == "blob"}
    return commit_sha, paths


def _device_name(device: dict) -> str:
    base = "Meshtastic Fancy UI" if device.get("is_fancy") else "Meshtastic"
    variant = device.get("variant")
    return f"{base} ({variant})" if variant else base


def _mirror_url(commit_sha: str, path: str) -> str:
    return f"https://raw.githubusercontent.com/{MIRROR_OWNER}/{MIRROR_REPO}/{commit_sha}/{path}"


def collect_versions(device: dict, betas: list, commit_sha: str, mirror_paths: set):
    target = device["target"]
    versions = []
    for beta in betas:
        version = beta["version"]
        folder = f"firmware-{version}"
        factory_path = f"{folder}/firmware-{target}-{version}.factory.bin"
        plain_path = f"{folder}/firmware-{target}-{version}.bin"
        data_path = f"{folder}/littlefs-{target}-{version}.bin"

        if factory_path in mirror_paths:
            file_path = factory_path
        elif plain_path in mirror_paths:
            file_path = plain_path
        else:
            continue

        if data_path not in mirror_paths:
            continue

        file_url = _mirror_url(commit_sha, file_path)
        data_url = _mirror_url(commit_sha, data_path)
        versions.append(
            {
                "version": version,
                "published_at": beta["published_at"],
                "file": file_url,
                "data": data_url
            }
        )
    return versions


def _load_json_file(path: str):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def _save_json_file(path: str, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)


def _merge_versions(existing_versions: list, new_versions: list) -> list:
    by_version = {v["version"]: v for v in existing_versions}
    for v in new_versions:
        by_version[v["version"]] = v
    merged = list(by_version.values())
    merged.sort(key=lambda v: v["published_at"], reverse=True)
    return merged[:10]


def atualizar_meshtastic():
    betas = fetch_beta_releases()
    if not betas:
        print("Nenhuma release Beta encontrada em meshtastic/firmware")
        return

    commit_sha, mirror_paths = fetch_mirror_state()

    devices_by_json = {}
    for device in DEVICES:
        devices_by_json.setdefault(device["json"], []).append(device)

    for json_filename, json_devices in devices_by_json.items():
        json_path = os.path.join(os.path.dirname(__file__), "database", json_filename)
        lista = _load_json_file(json_path)

        for device in json_devices:
            name = _device_name(device)
            new_versions = collect_versions(device, betas, commit_sha, mirror_paths)

            if not new_versions:
                print(f"  {device['target']}: nenhuma versão Beta disponível no mirror")
                continue

            existing_index = None
            for idx, entry in enumerate(lista):
                if entry.get("logic") == "meshtastic" and entry.get("name") == name:
                    existing_index = idx
                    break

            if existing_index is not None:
                existing_entry = lista[existing_index]
                merged_versions = _merge_versions(existing_entry.get("versions", []), new_versions)
                updated_entry = dict(FIRMWARE_TEMPLATE)
                updated_entry["name"] = name
                updated_entry["versions"] = merged_versions
                if "fid" in existing_entry:
                    updated_entry["fid"] = existing_entry["fid"]
                lista[existing_index] = updated_entry
                print(f"  {device['target']}: entrada atualizada em {json_filename} (+{len(new_versions)} verificada(s))")
            else:
                new_entry = dict(FIRMWARE_TEMPLATE)
                new_entry["name"] = name
                new_entry["versions"] = new_versions
                lista.append(new_entry)
                print(f"  {device['target']}: nova entrada criada em {json_filename}")

        _save_json_file(json_path, lista)


if __name__ == "__main__":
    try:
        atualizar_meshtastic()
    except Exception as exc:
        print(f"Erro ao processar meshtastic.py: {exc}")
        raise

    print("\nProcesso concluído!")
