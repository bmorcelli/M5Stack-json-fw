import json
import os
import sys
from argparse import ArgumentParser
from urllib.parse import urlparse, quote
import requests

BASE_DIR = os.path.dirname(__file__)
DATABASE_DIR = os.path.join(BASE_DIR, "database")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
HEADERS = {
    "Accept": "application/vnd.github.v3+json"
}
if GITHUB_TOKEN:
    HEADERS["Authorization"] = f"Bearer {GITHUB_TOKEN}"

GITHUB_RAW_HOSTS = {"raw.githubusercontent.com", "github.com"}


class GitHubPathParseError(Exception):
    pass


def load_json_file(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json_file(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)


def is_release_url(url):
    """Verifica se a URL é uma release do GitHub"""
    return "/releases/download/" in url


def parse_github_raw_url(url):
    """Parse URLs de arquivos brutos do GitHub (não releases)"""
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
    """Remove prefixos refs/ para usar na API do GitHub"""
    if ref.startswith("refs/heads/"):
        return ref[len("refs/heads/"):]
    if ref.startswith("refs/tags/"):
        return ref[len("refs/tags/"):]
    return ref


def get_latest_commit_for_path(owner, repo, path, ref=None):
    """Obtém o commit hash mais recente para um arquivo"""
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
    return latest["sha"]


def build_permanent_url(owner, repo, commit_sha, file_path):
    """Constrói uma URL permanente com o commit hash"""
    return f"https://raw.githubusercontent.com/{owner}/{repo}/{commit_sha}/{file_path}"


def validate_and_update_link(file_url, dry_run=False):
    """
    Valida um link de arquivo no GitHub e retorna a URL permanente se válido
    Retorna: (is_valid, permanent_url, error_message)
    """
    try:
        owner, repo, ref, file_path = parse_github_raw_url(file_url)
    except GitHubPathParseError as e:
        # Não é URL do GitHub que podemos processar
        return (None, file_url, str(e))

    try:
        commit_sha = get_latest_commit_for_path(owner, repo, file_path, ref)
        permanent_url = build_permanent_url(owner, repo, commit_sha, file_path)
        return (True, permanent_url, None)
    except Exception as e:
        return (False, file_url, str(e))


def process_database_file(path, dry_run=False):
    """Processa um arquivo de database e atualiza links de repositório"""
    data = load_json_file(path)
    if not isinstance(data, list):
        print(f"[WARN] Formato inesperado em {path}, esperado lista de objetos.")
        return {"valid": [], "invalid": [], "unchanged": []}

    results = {"valid": [], "invalid": [], "unchanged": []}

    for item in data:
        # Pula itens que já têm checkFileOnRepo marcado
        if item.get("checkFileOnRepo"):
            continue

        versions = item.get("versions", [])
        if not isinstance(versions, list):
            continue

        for version_idx, version in enumerate(versions):
            if not isinstance(version, dict):
                continue

            file_url = version.get("file")
            if not isinstance(file_url, str) or not file_url:
                continue

            # Pula releases do GitHub
            if is_release_url(file_url):
                results["unchanged"].append({
                    "type": "release",
                    "item": item.get("name", "<sem nome>"),
                    "file_url": file_url
                })
                continue

            # Tenta validar e atualizar o link
            is_valid, new_url, error = validate_and_update_link(file_url, dry_run=dry_run)

            if is_valid is None:
                # Não é URL do GitHub que podemos processar
                results["unchanged"].append({
                    "type": "other",
                    "item": item.get("name", "<sem nome>"),
                    "file_url": file_url
                })
            elif is_valid:
                # Link válido e agora com commit permanente
                if new_url != file_url:
                    print(f"[UPDATE] {item.get('name', '<sem nome>')}")
                    print(f"  De:  {file_url}")
                    print(f"  Para: {new_url}")
                    if not dry_run:
                        version["file"] = new_url
                    results["valid"].append({
                        "item": item.get("name", "<sem nome>"),
                        "old_url": file_url,
                        "new_url": new_url
                    })
                else:
                    results["valid"].append({
                        "item": item.get("name", "<sem nome>"),
                        "old_url": file_url,
                        "new_url": new_url,
                        "status": "already_permanent"
                    })
            else:
                # Link inválido
                print(f"[INVALID] {item.get('name', '<sem nome>')}: {error}")
                results["invalid"].append({
                    "item": item.get("name", "<sem nome>"),
                    "file_url": file_url,
                    "error": error
                })

    if results["valid"] and not dry_run:
        save_json_file(path, data)
        print(f"Arquivo atualizado: {path}\n")

    return results


def find_database_files():
    """Encontra todos os arquivos JSON de database"""
    return [
        os.path.join(DATABASE_DIR, filename)
        for filename in sorted(os.listdir(DATABASE_DIR))
        if filename.endswith(".json")
    ]


def print_report(all_results):
    """Imprime relatório final"""
    print("\n" + "="*80)
    print("RELATÓRIO FINAL")
    print("="*80)

    total_valid = sum(len(r["valid"]) for r in all_results.values())
    total_invalid = sum(len(r["invalid"]) for r in all_results.values())
    total_unchanged = sum(len(r["unchanged"]) for r in all_results.values())

    print(f"\nResumo:")
    print(f"  ✓ Links válidos (atualizados para permanent): {total_valid}")
    print(f"  ✗ Links inválidos (não encontrados): {total_invalid}")
    print(f"  → Links não alterados: {total_unchanged}")

    if total_invalid > 0:
        print("\n" + "-"*80)
        print("LINKS INVÁLIDOS (precisam revisão):")
        print("-"*80)
        for file_path, results in all_results.items():
            for invalid in results["invalid"]:
                print(f"\n{file_path}:")
                print(f"  Firmware: {invalid['item']}")
                print(f"  URL: {invalid['file_url']}")
                print(f"  Erro: {invalid['error']}")

    if total_valid > 0:
        print("\n" + "-"*80)
        print("LINKS ATUALIZADOS:")
        print("-"*80)
        for file_path, results in all_results.items():
            updated = [r for r in results["valid"] if r.get("new_url") != r.get("old_url")]
            if updated:
                print(f"\n{file_path}:")
                for item in updated:
                    print(f"  {item['item']}")
                    print(f"    Antes: {item['old_url']}")
                    print(f"    Depois: {item['new_url']}")


def main():
    parser = ArgumentParser(description="Valida e atualiza links de arquivos em repositórios GitHub para versões com commit permanente.")
    parser.add_argument("--dry-run", action="store_true", help="Não grava mudanças, apenas mostra o que seria feito.")
    parser.add_argument("--database-dir", default=DATABASE_DIR, help="Pasta de arquivos JSON de origem (default: 3rd/database)")
    args = parser.parse_args()

    database_dir = args.database_dir

    if not os.path.isdir(database_dir):
        print(f"Diretório não encontrado: {database_dir}")
        sys.exit(1)

    if GITHUB_TOKEN is None:
        print("[AVISO] GITHUB_TOKEN não definido, usando acesso anônimo à API do GitHub.")

    if args.dry_run:
        print("[DRY RUN] Nenhuma mudança será gravada\n")

    all_results = {}
    for database_file in find_database_files():
        print(f"Processando {os.path.basename(database_file)}...")
        results = process_database_file(database_file, dry_run=args.dry_run)
        all_results[database_file] = results

    print_report(all_results)


if __name__ == "__main__":
    main()
