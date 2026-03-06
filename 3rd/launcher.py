import os
import requests
import json
import re
from datetime import datetime

# Caminhos locais
LAST_COMMIT_FILE = "./3rd/launcher.lastCommit"
JSON_DIR = "./3rd/"  # Pasta com os arquivos fonte JSON
JSON_DIR_OUT = "./3rd/r/"  # Pasta com os arquivos fonte JSON

# 1. Obter commit atual da branch WebPage
def get_latest_commit():
    url = "https://api.github.com/repos/bmorcelli/Launcher/releases/tags/beta"
    r = requests.get(url)
    r.raise_for_status()
    release = r.json()
    match = re.search(r'\((.*?)\)', release["name"])
    date = release.get("published_at")
    # print(f"Data obtida do release: {date}")
    if match:
        commit_hash = match.group(1)
        url = f"https://api.github.com/repos/bmorcelli/Launcher/commits/{commit_hash}"
        r = requests.get(url)
        r.raise_for_status()
        commit_data = r.json()
        date = commit_data["commit"]["committer"]["date"]
        # print(f"Data obtida do commit: {date}")
    return release["name"], date  # Título e data da release

# 2. Ler commit salvo anteriormente
def read_saved_commit():
    if os.path.exists(LAST_COMMIT_FILE):
        with open(LAST_COMMIT_FILE, "r") as f:
            return f.read().strip()
    return None

# 3. Salvar novo commit
def write_commit(commit):
    with open(LAST_COMMIT_FILE, "w") as f:
        f.write(commit)

# 4. Remover entradas com "author": "bmorcelli" dos arquivos JSON em /r
def clean_json_files():
    for filename in os.listdir(JSON_DIR_OUT):
        if not filename.endswith(".json"):
            continue

        filepath = os.path.join(JSON_DIR_OUT, filename)
        with open(filepath, "r", encoding="utf-8") as f:
            try:
                data = json.load(f)
            except Exception as e:
                print(f"Erro ao processar {filename}: {e}")
                continue

        # Remove elementos com author == "bmorcelli"
        if isinstance(data, list):
            new_data = [item for item in data if item.get("author") != "bmorcelli"]
        else:
            print(f"Formato inesperado em {filename}")
            continue

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(new_data, f, indent=4)

# 5. Atualizar published_at nos arquivos JSON
def update_published_at(published_date):
    for filename in os.listdir(JSON_DIR):
        if not filename.endswith(".json"):
            continue

        filepath = os.path.join(JSON_DIR, filename)
        with open(filepath, "r", encoding="utf-8") as f:
            try:
                data = json.load(f)
            except Exception as e:
                print(f"Erro ao processar {filename}: {e}")
                continue

        # Atualizar published_at nas versões
        if isinstance(data, list):
            modified = False
            for item in data:
                if "versions" in item and item.get("author") == "bmorcelli":
                    for version in item["versions"]:
                        if "published_at" in version and version["published_at"] != published_date:
                            version["published_at"] = published_date
                            modified = True

            if modified:
                with open(filepath, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=4)
                print(f"Atualizado published_at em {filename}")
        else:
            print(f"Formato inesperado em {filename}")

# Execução principal
def main():
    try:
        latest_commit, published_iso = get_latest_commit()
        saved_commit = read_saved_commit()

        if latest_commit != saved_commit:
            print("Commit mudou. Limpando arquivos JSON...")
            clean_json_files()
            # Usar a data da release atual quando o commit mudou
            published_date = datetime.strptime(published_iso, "%Y-%m-%dT%H:%M:%SZ").strftime("%Y-%m-%d") if published_iso else ""
            print(f"Commit mudou de '{saved_commit}' para '{latest_commit}'. Atualizando published_at nos arquivos JSON...")
            update_published_at(published_date)
            write_commit(latest_commit)
        else:
            print("Commit não mudou. Nada a fazer.")
    except Exception as e:
        print(f"Erro: {e}")

if __name__ == "__main__":
    main()
