import json
import os
import random
import string
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from script.firmware_manifest import analyze_remote_firmware_batch, ensure_install_manifest


def _generate_fid(existing_fids):
    prefix = "CFW"
    total_length = 32
    random_length = max(total_length - len(prefix), 0)
    alphabet = string.ascii_uppercase + string.digits

    while True:
        fid = prefix + "".join(random.choices(alphabet, k=random_length))
        if fid not in existing_fids:
            existing_fids.add(fid)
            return fid


def process_jsons(max_workers: int = 4):
    input_folder = "./3rd/database/"
    output_folder = "./3rd/r/"

    os.makedirs(output_folder, exist_ok=True)
    files_added_total = 0
    output_changed = False
    aggregated_devices = []
    existing_fids = set()

    for filename in os.listdir(input_folder):
        if not filename.endswith(".json"):
            continue

        json_path = os.path.join(input_folder, filename)
        output_path = os.path.join(output_folder, filename)

        print(f"\nProcessando {filename}...\n")

        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data_new = json.load(f)
        except Exception as e:
            print(f"Erro ao carregar {filename}: {e}")
            continue

        for item in data_new:
            fid = item.get("fid")
            if fid:
                existing_fids.add(fid)

        data_new_modified = False
        for item in data_new:
            if not item.get("fid"):
                item["fid"] = _generate_fid(existing_fids)
                data_new_modified = True

        try:
            with open(output_path, "r", encoding="utf-8") as f:
                data_old = json.load(f)
        except Exception:
            data_old = []

        for item in data_old:
            fid = item.get("fid")
            if fid:
                existing_fids.add(fid)

        if data_new_modified:
            try:
                with open(json_path, "w", encoding="utf-8") as f:
                    json.dump(data_new, f, indent=4)
            except Exception as e:
                print(f"Erro ao atualizar {filename} com novos FIDs: {e}")

        old_map = {item["name"].strip(): item for item in data_old}
        new_map = {item["name"].strip(): item for item in data_new}
        merged_data = []

        for name, new_item in new_map.items():
            new_item["name"] = new_item["name"].strip()

            versions = [
                v
                for v in new_item.get("versions", [])
                if v["file"].endswith(".bin") or v["file"].endswith("file")
            ]
            if not versions:
                continue

            old_versions = old_map.get(name, {}).get("versions", [])
            old_versions_map = {(v.get("version"), v.get("file")): v for v in old_versions}

            for version in versions:
                key = (version.get("version"), version.get("file"))
                if key in old_versions_map:
                    for field, val in old_versions_map[key].items():
                        if field not in version:
                            version[field] = val

            new_item["versions"] = sorted(
                versions,
                key=lambda v: v.get("published_at", "0000-00-00"),
                reverse=True,
            )

            if "esp" not in new_item:
                old_item = old_map.get(name, {})
                if "esp" in old_item:
                    new_item["esp"] = old_item["esp"]

            merged_data.append(new_item)

        merged_data = sorted(merged_data, key=lambda x: x["name"])

        category_name, _ = os.path.splitext(filename)
        for item in merged_data:
            item["category"] = category_name

        tasks = []
        for item in merged_data:
            for version in item.get("versions", []):
                if "s" in version:
                    print(f"{item['name']} - {version.get('version', '?')} - Ok", flush=True)
                    ensure_install_manifest(version, item)
                else:
                    tasks.append((item, version))

        result = analyze_remote_firmware_batch(tasks, max_workers=max_workers)
        files_added = result["files_added"]

        for item, version in tasks:
            if version.get("invalid"):
                print(f"{item['name']} - {version['version']} - Invalid ", flush=True)
            else:
                ensure_install_manifest(version, item)

        if result["errors"]:
            print(f"\nAVISO: {len(result['errors'])} erros durante análise:", flush=True)
            for err in result["errors"]:
                print(f"  {err['item']} - {err['version']}: {err['error']}", flush=True)

        previous_output = ""
        if os.path.exists(output_path):
            with open(output_path, "r", encoding="utf-8") as existing_file:
                previous_output = existing_file.read()

        new_output = json.dumps(merged_data, indent=4)
        if previous_output != new_output:
            output_changed = True

        with open(output_path, "w", encoding="utf-8") as final_file:
            final_file.write(new_output)

        aggregated_devices.extend(merged_data)
        files_added_total += files_added
        print(f"\n{filename} finalizado. Arquivos .bin adicionados: {files_added}\n")

    all_devices_path = os.path.join(output_folder, "all_devices_firmware.json")
    aggregated_devices = sorted(
        aggregated_devices,
        key=lambda x: (x.get("category", ""), x.get("name", "")),
    )

    previous_all_devices = ""
    if os.path.exists(all_devices_path):
        with open(all_devices_path, "r", encoding="utf-8") as existing_file:
            previous_all_devices = existing_file.read()

    new_all_devices = json.dumps(aggregated_devices, indent=2)
    if previous_all_devices != new_all_devices:
        output_changed = True

    with open(all_devices_path, "w", encoding="utf-8") as all_devices_file:
        all_devices_file.write(new_all_devices)

    print(f"\n\nTotal de arquivos .bin adicionados em todos os JSONs: {files_added_total}\n")
    return files_added_total > 0 or output_changed


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--max-workers",
        type=int,
        default=4,
        help="Number of parallel workers for firmware analysis (default: 4).",
    )
    args = parser.parse_args()

    changed = process_jsons(max_workers=args.max_workers)
    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a", encoding="utf-8") as fh:
            fh.write(f"changed={'true' if changed else 'false'}\n")
