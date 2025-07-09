import os
import requests
import json

# Caminhos locais
LAST_COMMIT_FILE = "./3rd/bruce.lastCommit"
JSON_DIR = "./3rd/r/"

# 1. Obter commit atual da branch WebPage
def get_latest_commit():
    url = "https://api.github.com/repos/pr3y/Bruce/commits/WebPage"
    r = requests.get(url)
    r.raise_for_status()
    return r.json()["sha"]

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

# 4. Remover entradas com "author": "pr3y" dos arquivos JSON
def clean_json_files():
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

        # Remove elementos com author == "pr3y"
        if isinstance(data, list):
            new_data = [item for item in data if item.get("author") != "pr3y"]
        else:
            print(f"Formato inesperado em {filename}")
            continue

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(new_data, f)

# Execução principal
def main():
    try:
        latest_commit = get_latest_commit()
        saved_commit = read_saved_commit()

        if latest_commit != saved_commit:
            print("Commit mudou. Limpando arquivos JSON...")
            clean_json_files()
            write_commit(latest_commit)
        else:
            print("Commit não mudou. Nada a fazer.")
    except Exception as e:
        print(f"Erro: {e}")

if __name__ == "__main__":
    main()
