import os
import requests
import json
import time
import random

def process_jsons():
    input_folder = "./3rd/"
    output_folder = "./3rd/r/"
    temp_bin = os.path.join(input_folder, "temp.bin")

    os.makedirs(output_folder, exist_ok=True)
    files_added_total = 0
    aggregated_devices = []

    for filename in os.listdir(input_folder):
        if not filename.endswith(".json"):
            continue

        json_path = os.path.join(input_folder, filename)
        output_path = os.path.join(output_folder, filename)

        print(f"\nProcessando {filename}...\n")

        try:
            with open(json_path, 'r') as f:
                data_new = json.load(f)
        except Exception as e:
            print(f"Erro ao carregar {filename}: {e}")
            continue

        try:
            with open(output_path, 'r') as f:
                data_old = json.load(f)
        except:
            data_old = []

        old_map = {item["name"].strip(): item for item in data_old}
        new_map = {item["name"].strip(): item for item in data_new}

        merged_data = []

        for name, new_item in new_map.items():
            new_item["name"] = new_item["name"].strip()

            versions = [
                v for v in new_item.get("versions", [])
                if v["file"].endswith(".bin") or v["file"].endswith("file")
            ]
            if not versions:
                continue

            old_versions = old_map.get(name, {}).get("versions", [])
            old_versions_map = {(v.get("version"), v.get("file")): v for v in old_versions}

            for v in versions:
                key = (v.get("version"), v.get("file"))
                if key in old_versions_map:
                    for k, val in old_versions_map[key].items():
                        if k not in v:
                            v[k] = val

            new_item["versions"] = sorted(versions, key=lambda v: v.get("published_at", "0000-00-00"), reverse=True)
            merged_data.append(new_item)

        merged_data = sorted(merged_data, key=lambda x: x["name"])

        category_name, _ = os.path.splitext(filename)
        for item in merged_data:
            item["category"] = category_name
        files_added = 0

        for item in merged_data:
            for version in item.get("versions", []):
                if "s" in version:
                    print(f"{item['name']} - {version.get('version', '?')} - Ok", flush=True)
                    continue

                print(f"{item['name']} - {version.get('version', '?')} - {version['file']}", flush=True)
                files_added += 1

                file_url = version["file"]
                try:
                    time.sleep(random.uniform(0.1, 0.3))
                    with requests.get(file_url, stream=True, timeout=10) as r:
                        version['Fs'] = int(r.headers.get('Content-Length', 0))
                        first_bytes = r.raw.read(33600)
                        with open(temp_bin, "wb") as temp_file:
                            temp_file.write(first_bytes)

                    version['s'] = False
                    if os.path.getsize(temp_bin) > 33120:
                        with open(temp_bin, "rb") as temp_file:
                            temp_file.seek(0x8000)
                            app_size_bytes = temp_file.read(16)
                            if app_size_bytes[:3] == b'\xAA\x50\x01':
                                for i in range(8):
                                    temp_file.seek(0x8000 + i * 0x20)
                                    blk = temp_file.read(16)
                                    if blk[3] in (0x00, 0x10, 0x20) and blk[6] == 0x01:
                                        size = blk[0x0A] << 16 | blk[0x0B] << 8
                                        version['as'] = min(size, version['Fs'] - 0x10000)
                                    elif blk[3] == 0x82:
                                        version['ss'] = blk[0x0A] << 16 | blk[0x0B] << 8
                                        version['so'] = blk[0x06] << 16 | blk[0x07] << 8 | blk[0x08]
                                        version['s'] = version['Fs'] >= version['so'] + version['ss']
                            else:
                                version['as'] = version['Fs']
                                version['nb'] = True
                except Exception as e:
                    print(f"Erro ao processar {file_url}: {e}")

        if os.path.exists(temp_bin):
            os.remove(temp_bin)

        with open(output_path, 'w') as final_file:
            json.dump(merged_data, final_file)

        aggregated_devices.extend(merged_data)

        files_added_total += files_added
        print(f"\n{filename} finalizado. Arquivos .bin adicionados: {files_added}\n")

    all_devices_path = os.path.join(output_folder, "all_devices_firmware.json")
    aggregated_devices = sorted(
        aggregated_devices,
        key=lambda x: (x.get("category", ""), x.get("name", ""))
    )

    with open(all_devices_path, 'w') as all_devices_file:
        json.dump(aggregated_devices, all_devices_file)

    print(f"\n\nTotal de arquivos .bin adicionados em todos os JSONs: {files_added_total}\n")
    return files_added_total > 0

if __name__ == "__main__":
    changed = process_jsons()
    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a") as fh:
            fh.write(f"changed={'true' if changed else 'false'}\n")