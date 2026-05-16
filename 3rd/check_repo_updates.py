import json
import os
import re
import sys
from argparse import ArgumentParser
from urllib.parse import urlparse

import requests

BASE_DIR = os.path.dirname(__file__)
DATABASE_DIR = os.path.join(BASE_DIR, "database")
OUTPUT_DIR = os.path.join(BASE_DIR, "r")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
HEADERS = {
    "Accept": "application/vnd.github.v3+json"
}
if GITHUB_TOKEN:
    HEADERS["Authorization"] = f"Bearer {GITHUB_TOKEN}"

# Campos de metadados que devem ser removidos para forçar reanálise
VERSION_METADATA_FIELDS = {
    "Fs",
    "s",
    "f",
    "f2",
    "fs",
    "fs2",
    "fo",
    "fo2",
    "as",
    "ao",
    "ss",
    "so",
    "nb",
    "invalid",
    "install",
}
ITEM_METADATA_FIELDS = {"esp"}

GITHUB_RAW_HOSTS = {"raw.githubusercontent.com", "github.com"}


class GitHubPathParseError(Exception):
    pass


def load_json_file(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json_file(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)


def parse_github_raw_url(url):
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if host not in GITHUB_RAW_HOSTS:
        raise GitHubPathParseError(f"URL não é do GitHub: {url}")

    path_parts = parsed.path.strip("/").split("/")
    if host == "raw.githubusercontent.com":
        if len(path_parts) < 4:
            raise GitHubPathParseError(f"URL raw.githubusercontent.com inválida: {url}")
        owner, repo, ref = path_parts[:3]
        file_path = "/".join(path_parts[3:])
        return owner, repo, ref, file_path

    # host == github.com
    if len(path_parts) < 5:
        raise GitHubPathParseError(f"URL github.com inválida: {url}")

    owner, repo, mode = path_parts[0], path_parts[1], path_parts[2]
    if mode == "raw":
        if len(path_parts) < 5:
            raise GitHubPathParseError(f"URL github raw inválida: {url}")
        if path_parts[3] == "refs" and len(path_parts) > 5 and path_parts[4] in ("heads", "tags"):
            ref = f"refs/{path_parts[4]}/{path_parts[5]}"
            file_path = "/".join(path_parts[6:])
        else:
            ref = path_parts[3]
            file_path = "/".join(path_parts[4:])
        return owner, repo, ref, file_path
    if mode == "blob":
        if len(path_parts) < 5:
            raise GitHubPathParseError(f"URL github blob inválida: {url}")
        if path_parts[3] == "refs" and len(path_parts) > 5 and path_parts[4] in ("heads", "tags"):
            ref = f"refs/{path_parts[4]}/{path_parts[5]}"
            file_path = "/".join(path_parts[6:])
        else:
            ref = path_parts[3]
            file_path = "/".join(path_parts[4:])
        return owner, repo, ref, file_path

    raise GitHubPathParseError(f"Formato GitHub não suportado: {url}")


def normalize_ref(ref):
    if ref.startswith("refs/heads/"):
        return ref[len("refs/heads/"):]
    if ref.startswith("refs/tags/"):
        return ref[len("refs/tags/"):]
    return ref


def get_latest_commit_for_path(owner, repo, path, ref=None):
    url = f"https://api.github.com/repos/{owner}/{repo}/commits"
    params = {"path": path, "per_page": 1}
    if ref:
        params["sha"] = normalize_ref(ref)

    response = requests.get(url, headers=HEADERS, params=params, timeout=15)
    if response.status_code == 404:
        raise GitHubPathParseError(f"Arquivo não encontrado no repositório: {owner}/{repo}/{path}")
    response.raise_for_status()
    commits = response.json()
    if not isinstance(commits, list) or len(commits) == 0:
        raise GitHubPathParseError(f"Nenhum commit retornado para {owner}/{repo}/{path}")
    latest = commits[0]
    return latest["sha"], latest["commit"]["committer"]["date"]


def is_path_git_url(file_url):
    try:
        parse_github_raw_url(file_url)
        return True
    except GitHubPathParseError:
        return False


def extract_commit_from_versions(versions):
    commit_candidates = []

    for version in versions:
        file_url = version.get("file")
        if not isinstance(file_url, str) or not file_url:
            continue

        try:
            owner, repo, ref, file_path = parse_github_raw_url(file_url)
        except GitHubPathParseError:
            continue

        try:
            sha, date = get_latest_commit_for_path(owner, repo, file_path, ref)
            commit_candidates.append({"sha": sha, "date": date, "file": file_url})
        except Exception as exc:
            print(f"[WARN] Não foi possível consultar commit para {file_url}: {exc}")

    if not commit_candidates:
        return None

    commit_candidates.sort(key=lambda item: item["date"], reverse=True)
    return commit_candidates[0]


def clear_metadata_for_output_item(output_item):
    for field in ITEM_METADATA_FIELDS:
        output_item.pop(field, None)

    for version in output_item.get("versions", []):
        if not isinstance(version, dict):
            continue
        for field in VERSION_METADATA_FIELDS:
            version.pop(field, None)


def clear_metadata_by_fid(fid, dry_run=False):
    if not fid:
        return False

    changed = False
    for filename in os.listdir(OUTPUT_DIR):
        if not filename.endswith(".json"):
            continue

        output_path = os.path.join(OUTPUT_DIR, filename)
        try:
            data = load_json_file(output_path)
        except Exception as exc:
            print(f"[ERROR] Falha ao ler {output_path}: {exc}")
            continue

        if not isinstance(data, list):
            continue

        updated = False
        for item in data:
            if item.get("fid") == fid:
                clear_metadata_for_output_item(item)
                updated = True

        if updated:
            changed = True
            if dry_run:
                print(f"[DRY RUN] Metadados limpos em {output_path} para fid {fid}")
            else:
                save_json_file(output_path, data)
                print(f"Metadados limpos em {output_path} para fid {fid}")

    return changed


def process_database_file(path, dry_run=False):
    data = load_json_file(path)
    if not isinstance(data, list):
        print(f"[WARN] Formato inesperado em {path}, esperado lista de objetos.")
        return False

    changed = False
    for item in data:
        if not item.get("checkFileOnRepo"):
            continue

        versions = item.get("versions", [])
        if not isinstance(versions, list) or len(versions) == 0:
            continue

        result = extract_commit_from_versions(versions)
        if result is None:
            print(f"[INFO] Nenhum arquivo GitHub encontrado para {item.get('name', '<sem nome>')} em {path}")
            continue

        current_last_commit = item.get("lastCommit")
        if current_last_commit != result["sha"]:
            print(f"[INFO] Atualização detectada em {item.get('name', '<sem nome>')}: {current_last_commit} -> {result['sha']}")
            item["lastCommit"] = result["sha"]
            if not dry_run:
                changed = True
            if clear_metadata_by_fid(item.get("fid"), dry_run=dry_run):
                print(f"[INFO] Forçando reanálise de {item.get('name', '<sem nome>')} (fid={item.get('fid')})")
        else:
            print(f"[OK] Sem mudança para {item.get('name', '<sem nome>')} ({result['sha']})")

    if changed and not dry_run:
        save_json_file(path, data)
        print(f"Arquivo atualizado: {path}")

    return changed


def find_database_files():
    return [
        os.path.join(DATABASE_DIR, filename)
        for filename in sorted(os.listdir(DATABASE_DIR))
        if filename.endswith(".json")
    ]


def main():
    global DATABASE_DIR, OUTPUT_DIR

    parser = ArgumentParser(description="Verifica arquivos GitHub em 3rd/database e força reanálise em 3rd/r quando necessário.")
    parser.add_argument("--dry-run", action="store_true", help="Não grava mudanças, apenas mostra o que seria feito.")
    parser.add_argument("--database-dir", default=DATABASE_DIR, help="Pasta de arquivos JSON de origem (default: 3rd/database)")
    parser.add_argument("--output-dir", default=OUTPUT_DIR, help="Pasta de arquivos JSON de saída a limpar (default: 3rd/r)")
    args = parser.parse_args()

    DATABASE_DIR = args.database_dir
    OUTPUT_DIR = args.output_dir

    if not os.path.isdir(DATABASE_DIR):
        print(f"Diretório não encontrado: {DATABASE_DIR}")
        sys.exit(1)
    if not os.path.isdir(OUTPUT_DIR):
        print(f"Diretório não encontrado: {OUTPUT_DIR}")
        sys.exit(1)

    if GITHUB_TOKEN is None:
        print("[AVISO] GITHUB_TOKEN não definido, usando acesso anônimo à API do GitHub.")

    any_changes = False
    for database_file in find_database_files():
        print(f"Analisando {database_file}...")
        if process_database_file(database_file, dry_run=args.dry_run):
            any_changes = True

    if any_changes:
        print("Atualizações aplicadas.")
    else:
        print("Nenhuma atualização necessária.")


if __name__ == "__main__":
    main()
