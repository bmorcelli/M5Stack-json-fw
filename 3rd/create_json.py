import os
import requests
import json
import random
import string


def _generate_fid(existing_fids):
    prefix = "CFW"
    total_length = 32
    random_length = max(total_length - len(prefix), 0)
    alphabet = string.ascii_uppercase + string.digits

    while True:
        fid = prefix + ''.join(random.choices(alphabet, k=random_length))
        if fid not in existing_fids:
            existing_fids.add(fid)
            return fid


def process_jsons():
    input_folder = "./3rd/"
    output_folder = "./3rd/r/"
    temp_bin = os.path.join(input_folder, "temp.bin")

    os.makedirs(output_folder, exist_ok=True)
    files_added_total = 0
    aggregated_devices = []
    existing_fids = set()

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
            with open(output_path, 'r') as f:
                data_old = json.load(f)
        except:
            data_old = []

        for item in data_old:
            fid = item.get("fid")
            if fid:
                existing_fids.add(fid)

        if data_new_modified:
            try:
                with open(json_path, 'w') as f:
                    json.dump(data_new, f, indent=4)
            except Exception as e:
                print(f"Erro ao atualizar {filename} com novos FIDs: {e}")

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
                    with requests.get(file_url, stream=True, timeout=10) as r:
                        version['Fs'] = int(r.headers.get('Content-Length', 0))
                        first_bytes = r.raw.read(33600)
                        with open(temp_bin, "wb") as temp_file:
                            temp_file.write(first_bytes)

                    # Leitura e cálculos
                    version['s'] = 0 # Spiffs
                    version['f'] = 0 # FAT Vfs
                    version['f2'] = 0 # FAT Vfs
                    if os.path.getsize(temp_bin) > (33120): # 0x8160 and  i = 9
                        with open(temp_bin, "rb") as temp_file:
                            esp_bytes = temp_file.read()
                            if b"esp32p4" in esp_bytes:
                                item['esp'] = "p4"
                            elif b"esp32s2" in esp_bytes:
                                item['esp'] = "s2"
                            elif b"esp32s3" in esp_bytes:
                                item['esp'] = "s3"
                            elif b"esp32c3" in esp_bytes:
                                item['esp'] = "c3"
                            elif b"esp32c5" in esp_bytes:
                                item['esp'] = "c5"
                            elif b"esp32c61" in esp_bytes:
                                item['esp'] = "c61"
                            elif b"esp32c6" in esp_bytes:
                                item['esp'] = "c6"
                            elif b"esp32h2" in esp_bytes:
                                item['esp'] = "h2"
                            elif b"esp32e22" in esp_bytes:
                                item['esp'] = "e22"
                            else:
                                item['esp'] = "32"

                            temp_file.seek(0x8000)
                            app_size_bytes = temp_file.read(16)
                            if (app_size_bytes[0] == 0xAA and app_size_bytes[1] == 0x50 and app_size_bytes[2] == 0x01):
                                j=0
                                app_offset_set = False
                                for i in range(8):
                                    temp_file.seek(0x8000 + i*0x20)
                                    app_size_bytes = temp_file.read(16)
                                    if not app_offset_set and (app_size_bytes[3] == 0x00 or app_size_bytes[3] == 0x20 or app_size_bytes[3]== 0x10) and app_size_bytes[2] == 0x00: 
                                        ao = app_size_bytes[0x06] << 16 | app_size_bytes[0x07] << 8 | app_size_bytes[0x08]          # app offset, usually 0x10000
                                        if ao > 0x10000:
                                            print(f"App starts at 0x{ao:X}")
                                        version['ao'] = ao
                                        app_offset_set = True
                                        if (app_size_bytes[0x0A] << 16 | app_size_bytes[0x0B] << 8 | 0x00) > (int(r.headers.get('Content-Length', 0)) - ao):
                                            version['as'] = int(r.headers.get('Content-Length', 0)) - ao
                                        else:
                                            version['as'] = app_size_bytes[0x0A] << 16 | app_size_bytes[0x0B] << 8 | 0x00
                                    elif app_size_bytes[3] == 0x82:
                                        ss =  app_size_bytes[0x0A] << 16 | app_size_bytes[0x0B] << 8 | 0x00                     # Spiffs_size
                                        so = app_size_bytes[0x06] << 16 | app_size_bytes[0x07] << 8 | app_size_bytes[0x08]      # Spiffs_offset
                                        if version['Fs'] >= so + ss:                                                            # Spiffs exists or not
                                            version['s'] = 1
                                            version['ss'] = ss
                                            version['so'] = so
                                    elif app_size_bytes[3] == 0x81 and j==0:
                                        fs = app_size_bytes[0x0A] << 16 | app_size_bytes[0x0B] << 8 | 0x00                    # Spiffs_size
                                        fo = app_size_bytes[0x06] << 16 | app_size_bytes[0x07] << 8 | app_size_bytes[0x08]    # Spiffs_offset
                                        j=1
                                        if version['Fs'] >= fo + fs:                                                          # Spiffs exists or not
                                            version['f'] = 1
                                            version['fs'] = fs
                                            version['fo'] = fo
                                    elif app_size_bytes[3] == 0x81 and j==1:
                                        fs = app_size_bytes[0x0A] << 16 | app_size_bytes[0x0B] << 8 | 0x00                    # Spiffs_size
                                        fo = app_size_bytes[0x06] << 16 | app_size_bytes[0x07] << 8 | app_size_bytes[0x08]    # Spiffs_offset
                                        j=2
                                        if version['Fs'] >= fo + fs:                                                          # Spiffs exists or not
                                            version['f2'] = 1
                                            version['fs2'] = fs
                                            version['fo2'] = fo
                            else:
                                version['as'] = int(r.headers.get('Content-Length', 0))
                                version['nb'] = True # nb stands for No-Bootloader, to be downloaded whole
                    else:
                        version['invalid'] = True
                        print(f"{item['name']} - {version['version']} - Invalid ", flush=True)

                except Exception as e:
                    print(f"Erro ao processar {file_url}: {e}")

        if os.path.exists(temp_bin):
            os.remove(temp_bin)

        with open(output_path, 'w') as final_file:
            json.dump(merged_data, final_file, indent=4)

        aggregated_devices.extend(merged_data)

        files_added_total += files_added
        print(f"\n{filename} finalizado. Arquivos .bin adicionados: {files_added}\n")

    all_devices_path = os.path.join(output_folder, "all_devices_firmware.json")
    aggregated_devices = sorted(
        aggregated_devices,
        key=lambda x: (x.get("category", ""), x.get("name", ""))
    )

    with open(all_devices_path, 'w') as all_devices_file:
        json.dump(aggregated_devices, all_devices_file, indent=2)

    print(f"\n\nTotal de arquivos .bin adicionados em todos os JSONs: {files_added_total}\n")
    return files_added_total > 0

if __name__ == "__main__":
    changed = process_jsons()
    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a") as fh:
            fh.write(f"changed={'true' if changed else 'false'}\n")