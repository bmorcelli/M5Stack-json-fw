"""Gera 3rd/database/seeedstudio-reterminal-sticky.json a partir do submodule
3rd/repos/seeedstudio-reterminal-sticky (mirror do repositório oficial
Seeed-Projects/reterminal-sticky-playground-registry).

Cada `integrations/**/integration.json` do registro vira uma entrada no
database, com FID derivado deterministicamente do caminho completo até o
arquivo integration.json. Entradas geradas por este script recebem o campo
"managed_by": "seeedstudio-reterminal-sticky" para que o merge nunca apague
firmwares adicionados manualmente ao mesmo arquivo JSON.
"""

import base64
import hashlib
import json
import os
import subprocess
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

REPO_OWNER = "Seeed-Projects"
REPO_NAME = "reterminal-sticky-playground-registry"
MANAGED_BY = "seeedstudio-reterminal-sticky"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(BASE_DIR)
SUBMODULE_DIR = os.path.join(BASE_DIR, "repos", "seeedstudio-reterminal-sticky")
INTEGRATIONS_DIR = os.path.join(SUBMODULE_DIR, "integrations")
DB_PATH = os.path.join(BASE_DIR, "database", "seeedstudio-reterminal-sticky.json")

HEADERS = {"User-Agent": "M5Stack-json-fw/seeedstudio-reterminal-sticky"}
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
API_HEADERS = dict(HEADERS)
if GITHUB_TOKEN:
    API_HEADERS["Authorization"] = f"Bearer {GITHUB_TOKEN}"
    API_HEADERS["Accept"] = "application/vnd.github+json"


def _http_get(url: str, headers: dict, retries: int = 3):
    request = Request(url, headers=headers)
    last_exc = None
    for attempt in range(1, retries + 1):
        try:
            with urlopen(request, timeout=30) as response:
                return response.read()
        except (HTTPError, URLError) as exc:
            last_exc = exc
            print(f"[seeedstudio_reterminal_sticky] Tentativa {attempt}/{retries} falhou para {url}: {exc}", flush=True)
    raise last_exc


def _git(args, cwd):
    result = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=False
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def make_fid(path_key: str) -> str:
    digest = hashlib.sha256(path_key.encode("utf-8")).digest()
    b32 = base64.b32encode(digest).decode("ascii").rstrip("=")
    return ("CFW" + b32)[:32]


def commit_date_for(rel_path: str, fallback: str) -> str:
    date = _git(["log", "-1", "--format=%cs", "--", rel_path], SUBMODULE_DIR)
    return date or fallback


def release_published_at(release_url: str, fallback: str) -> str:
    tag = release_url.rstrip("/").rsplit("/", 1)[-1]
    api_url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/releases/tags/{tag}"
    try:
        data = json.loads(_http_get(api_url, API_HEADERS))
        published = (data.get("published_at") or "")[:10]
        return published or fallback
    except Exception as exc:
        print(f"[seeedstudio_reterminal_sticky] Falha ao buscar data da release {tag}: {exc}", flush=True)
        return fallback


def classify_part(path: str):
    lowered = path.lower()
    if "bootloader" in lowered:
        return "bootloader"
    if "partition" in lowered and "boot_app0" not in lowered:
        return "partitions"
    if "boot_app0" in lowered:
        return None
    return "file"


def resolve_local_manifest(integration_dir: str, manifest_rel_path: str, commit_sha: str, fallback_date: str):
    manifest_local_path = os.path.join(integration_dir, manifest_rel_path)
    if not os.path.isfile(manifest_local_path):
        print(f"[seeedstudio_reterminal_sticky] manifestPath inexistente: {manifest_local_path}", flush=True)
        return None

    with open(manifest_local_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    manifest_dir = os.path.dirname(manifest_local_path)
    rel_dir_from_submodule = os.path.relpath(manifest_dir, SUBMODULE_DIR).replace(os.sep, "/")
    raw_base = f"https://raw.githubusercontent.com/{REPO_OWNER}/{REPO_NAME}/{commit_sha}/{rel_dir_from_submodule}/"

    urls = build_urls_from_manifest(manifest, raw_base)
    if not urls:
        return None

    rel_manifest_from_submodule = os.path.relpath(manifest_local_path, SUBMODULE_DIR).replace(os.sep, "/")
    published_at = commit_date_for(rel_manifest_from_submodule, fallback_date)

    return urls, published_at


def resolve_remote_manifest(manifest_url: str, release_url: str, fallback_date: str):
    try:
        manifest = json.loads(_http_get(manifest_url, HEADERS))
    except Exception as exc:
        print(f"[seeedstudio_reterminal_sticky] Falha ao baixar manifestUrl {manifest_url}: {exc}", flush=True)
        return None

    raw_base = manifest_url.rsplit("/", 1)[0] + "/"
    urls = build_urls_from_manifest(manifest, raw_base)
    if not urls:
        return None

    published_at = release_published_at(release_url, fallback_date) if release_url else fallback_date

    return urls, published_at


def build_urls_from_manifest(manifest: dict, raw_base: str):
    all_parts = []
    for build in manifest.get("builds", []):
        all_parts.extend(build.get("parts", []))
    all_parts = [p for p in all_parts if p.get("path")]

    if not all_parts:
        return None

    # Layout suportado 1: imagem unica (merged) no offset 0.
    if len(all_parts) == 1:
        return {"file": urljoin(raw_base, all_parts[0]["path"])}

    # Layout suportado 2: quarteto classico bootloader/partitions/boot_app0/app.
    # Qualquer outro layout (ex.: multiplas particoes de dados) e ambiguo demais
    # para mapear com seguranca e e ignorado.
    urls = {}
    file_candidates = 0
    for part in all_parts:
        role = classify_part(part["path"])
        if role is None:
            continue
        if role == "file":
            file_candidates += 1
        urls[role] = urljoin(raw_base, part["path"])

    if file_candidates != 1 or "file" not in urls:
        return None
    return urls


def build_versions(integration_dir: str, flash_versions: list, commit_sha: str, fallback_date: str):
    versions = []
    for v in flash_versions:
        version_name = v.get("version")
        if not version_name:
            continue

        result = None
        if v.get("manifestPath"):
            result = resolve_local_manifest(integration_dir, v["manifestPath"], commit_sha, fallback_date)
        elif v.get("manifestUrl"):
            result = resolve_remote_manifest(v["manifestUrl"], v.get("releaseUrl"), fallback_date)
        else:
            print(f"[seeedstudio_reterminal_sticky] Versao '{version_name}' sem manifest (sourceBuild); ignorando.", flush=True)
            continue

        if not result:
            print(f"[seeedstudio_reterminal_sticky] Versao '{version_name}' sem binario utilizavel; ignorando.", flush=True)
            continue

        urls, published_at = result
        entry = {
            "version": version_name,
            "published_at": published_at,
            "file": urls["file"],
        }
        if "bootloader" in urls:
            entry["bootloader"] = urls["bootloader"]
        if "partitions" in urls:
            entry["partitions"] = urls["partitions"]
        versions.append(entry)

    versions.sort(key=lambda v: v["published_at"], reverse=True)
    return versions


def resolve_cover(integration: dict, integration_dir: str, commit_sha: str):
    assets = integration.get("assets") or {}
    asset_rel = assets.get("logo") or assets.get("preview")
    if not asset_rel:
        return None
    if not os.path.isfile(os.path.join(integration_dir, asset_rel)):
        return None
    rel_dir_from_submodule = os.path.relpath(integration_dir, SUBMODULE_DIR).replace(os.sep, "/")
    return f"https://raw.githubusercontent.com/{REPO_OWNER}/{REPO_NAME}/{commit_sha}/{rel_dir_from_submodule}/{asset_rel}"


def build_entry(integration_json_path: str, commit_sha: str, fallback_date: str):
    with open(integration_json_path, "r", encoding="utf-8") as f:
        integration = json.load(f)

    if integration.get("mode") != "flash":
        return None

    integration_dir = os.path.dirname(integration_json_path)
    flash_versions = (integration.get("flash") or {}).get("versions") or []
    versions = build_versions(integration_dir, flash_versions, commit_sha, fallback_date)
    if not versions:
        return None

    rel_path_from_repo_root = os.path.relpath(integration_json_path, REPO_ROOT).replace(os.sep, "/")
    fid = make_fid(rel_path_from_repo_root)

    author = (integration.get("author") or {}).get("name") or "Seeed Studio"
    source = (integration.get("source") or {}).get("url")
    origin = (integration.get("origin") or {}).get("url")
    github_url = source or origin or (integration.get("author") or {}).get("url")

    entry = {
        "name": integration.get("name") or integration.get("id"),
        "author": author,
        "description": integration.get("description") or integration.get("summary") or "",
        "versions": versions,
        "fid": fid,
        "managed_by": MANAGED_BY,
    }
    if github_url:
        entry["github"] = github_url

    cover = resolve_cover(integration, integration_dir, commit_sha)
    if cover:
        entry["cover"] = cover

    return entry


def _load_db():
    if os.path.exists(DB_PATH):
        with open(DB_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def _save_db(entries: list):
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    entries = sorted(entries, key=lambda e: e.get("name", ""))
    with open(DB_PATH, "w", encoding="utf-8") as f:
        json.dump(entries, f, indent=4)


def atualizar_seeedstudio_reterminal_sticky():
    if not os.path.isdir(INTEGRATIONS_DIR):
        print(f"[seeedstudio_reterminal_sticky] Submodule nao encontrado em {INTEGRATIONS_DIR}", flush=True)
        return

    commit_sha = _git(["rev-parse", "HEAD"], SUBMODULE_DIR)
    if not commit_sha:
        print("[seeedstudio_reterminal_sticky] Nao foi possivel determinar o commit do submodule.", flush=True)
        return

    fallback_date = _git(["show", "-s", "--format=%cs", commit_sha], SUBMODULE_DIR) or ""

    generated_entries = []
    for entry_name in sorted(os.listdir(INTEGRATIONS_DIR)):
        if entry_name.startswith("_"):
            continue
        integration_json_path = os.path.join(INTEGRATIONS_DIR, entry_name, "integration.json")
        if not os.path.isfile(integration_json_path):
            continue

        try:
            entry = build_entry(integration_json_path, commit_sha, fallback_date)
        except Exception as exc:
            print(f"[seeedstudio_reterminal_sticky] Erro processando {integration_json_path}: {exc}", flush=True)
            continue

        if entry:
            generated_entries.append(entry)
            print(f"[seeedstudio_reterminal_sticky] {entry['name']}: {len(entry['versions'])} versao(oes)", flush=True)
        else:
            print(f"[seeedstudio_reterminal_sticky] {entry_name}: nenhuma versao flashable, ignorado.", flush=True)

    existing = _load_db()
    manual_entries = [e for e in existing if e.get("managed_by") != MANAGED_BY]

    final_entries = manual_entries + generated_entries
    _save_db(final_entries)

    print(
        f"[seeedstudio_reterminal_sticky] {len(generated_entries)} entrada(s) geradas, "
        f"{len(manual_entries)} entrada(s) manuais preservadas.",
        flush=True,
    )


if __name__ == "__main__":
    try:
        atualizar_seeedstudio_reterminal_sticky()
    except Exception as exc:
        print(f"Erro ao processar seeedstudio_reterminal_sticky.py: {exc}")
        print("Falha ao atualizar seeedstudio_reterminal_sticky.py, mas seguindo sem interromper o workflow.")

    print("\nProcesso concluido!")
