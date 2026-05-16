import argparse
import json
import os
import sys

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from script.firmware_manifest import (
    analyze_remote_firmware,
    analyze_remote_firmware_batch,
    copy_preserved_version_fields,
    ensure_install_manifest,
)


all_device_firmware = "./v2/all_device_firmware.json"
all_device_firmware_old = "./v2/all_device_firmware.old.json"

parser = argparse.ArgumentParser()
parser.add_argument(
    "--force-download-all",
    action="store_true",
    default=False,
    help="Force full download by removing cached JSON files first.",
)
parser.add_argument(
    "--max-workers",
    type=int,
    default=4,
    help="Number of parallel workers for firmware analysis (default: 4).",
)
args = parser.parse_args()

if args.force_download_all:
    if os.path.exists(all_device_firmware):
        os.remove(all_device_firmware)
    if os.path.exists(all_device_firmware_old):
        os.remove(all_device_firmware_old)

# Passo 1: renomear arquivo existente.
if os.path.exists(all_device_firmware):
    os.replace(all_device_firmware, all_device_firmware_old)

# Passo 2: baixar os dados da API do M5Burner.
url = "https://m5burner-api.m5stack.com/api/firmware"
response = requests.get(url)
data = response.json()
files_added = 0

with open(all_device_firmware, "w", encoding="utf-8") as new_file:
    json.dump(data, new_file, indent=2)

# Filtrar versoes sem arquivo binario.
for item in data:
    item["versions"] = [
        version
        for version in item["versions"]
        if version["file"].endswith(".bin") or version["file"].endswith("file")
    ]

data = [item for item in data if "versions" in item and len(item["versions"]) > 0]

for item in data:
    item["name"] = item["name"].strip()
    if item["category"] == "sticks3":
        item["category"] = "stickc"

data = sorted(data, key=lambda x: x["name"])

# Passo 3: preservar metadados enriquecidos de execucoes anteriores.
old_data = []
if os.path.exists(all_device_firmware_old):
    with open(all_device_firmware_old, "r", encoding="utf-8") as old_file:
        old_data = json.load(old_file)

    old_by_id = {old_item.get("_id"): old_item for old_item in old_data}
    for new_item in data:
        old_item = old_by_id.get(new_item.get("_id"))
        if not old_item:
            continue

        if "esp" in old_item:
            new_item["esp"] = old_item["esp"]
        if "name_en" in old_item:
            new_item["name_en"] = old_item["name_en"]
            new_item["name_src"] = old_item["name_src"]
        if "description_en" in old_item:
            new_item["description_en"] = old_item["description_en"]
            new_item["description_src"] = old_item["description_src"]

        old_versions = {
            (old_version.get("version"), old_version.get("file")): old_version
            for old_version in old_item.get("versions", [])
        }
        for new_version in new_item["versions"]:
            new_version.pop("change_log", None)
            new_version.pop("published", None)
            old_version = old_versions.get((new_version.get("version"), new_version.get("file")))
            if old_version:
                copy_preserved_version_fields(new_version, old_version)

# Passo 4: analisar novas versoes e adicionar manifesto install.
tasks = []
for item in data:
    for version in item["versions"]:
        needs_analysis = "s" not in version
        if item.get("category") == "stickc" and "esp" not in item:
            needs_analysis = True
        if needs_analysis and "invalid" not in version:
            tasks.append((item, version))
        else:
            print(f"{item['name']} - {version['version']} - Ok ", flush=True)
            ensure_install_manifest(version, item)

if tasks:
    print(f"\nAnalisando {len(tasks)} versões com {args.max_workers} workers...", flush=True)
    result = analyze_remote_firmware_batch(tasks, max_workers=args.max_workers)
    files_added += result["files_added"]

    for item, version in tasks:
        if version.get("invalid"):
            print(f"{item['name']} - {version['version']} - Invalid ", flush=True)
        else:
            ensure_install_manifest(version, item)

    if result["errors"]:
        print(f"\nAVISO: {len(result['errors'])} erros durante análise:", flush=True)
        for err in result["errors"]:
            print(f"  {err['item']} - {err['version']}: {err['error']}", flush=True)

previous_final = ""
if os.path.exists(all_device_firmware_old):
    with open(all_device_firmware_old, "r", encoding="utf-8") as old_file:
        previous_final = old_file.read()

final_json = json.dumps(data, indent=2)
data_changed = previous_final != final_json

with open(all_device_firmware, "w", encoding="utf-8") as final_file:
    final_file.write(final_json)

github_env_path = os.environ.get("GITHUB_ENV")
if github_env_path:
    with open(github_env_path, "a", encoding="utf-8") as env_file:
        env_value = "true" if files_added > 0 or data_changed else "false"
        env_file.write(f"FILES_ADDED={env_value}\n")

print(f"\n\n\nNumero de arquivos adicionados {files_added}\n\n\n", flush=True)
