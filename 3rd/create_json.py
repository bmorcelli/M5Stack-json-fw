import os
import requests
import json
import time
import random

input_folder = "./3rd/"
output_folder = "./3rd/r/"
temp_bin = os.path.join(input_folder, "temp.bin")

os.makedirs(output_folder, exist_ok=True)

files_added_total = 0

for filename in os.listdir(input_folder):
    if not filename.endswith(".json"):
        continue

    json_path = os.path.join(input_folder, filename)
    output_path = os.path.join(output_folder, filename)

    print(f"\nProcessando {filename}...\n")

    with open(json_path, 'r') as new_file:
        try:
            data = json.load(new_file)
        except Exception as e:
            print(f"Erro ao carregar {filename}: {e}")
            continue

    files_added = 0

    for item in data:
        item['versions'] = [
            version for version in item.get('versions', [])
            if version['file'].endswith('.bin') or version['file'].endswith('file')
        ]

    data = [item for item in data if 'versions' in item and len(item['versions']) > 0]

    for item in data:
        item['name'] = item['name'].strip()
        item['versions'] = sorted(
            item.get('versions', []),
            key=lambda v: v.get('published_at', '0000-00-00'),
            reverse=True
        )

    data = sorted(data, key=lambda x: x['name'])

    for item in data:
        for version in item['versions']:
            if 'spiffs' in version:
                print(f"{item['name']} - {version['version']} - Ok", flush=True)
            else:
                print(f"{item['name']} - {version['version']} - {version['file']}", flush=True)
                files_added += 1
                file_url = version['file']
                try:
                    time.sleep(random.uniform(0.1, 0.3))
                    with requests.get(file_url, stream=True, timeout=10) as r:
                        version['file_size'] = int(r.headers.get('Content-Length', 0))
                        first_bytes = r.raw.read(33600)
                        with open(temp_bin, "wb") as temp_file:
                            temp_file.write(first_bytes)

                    version['spiffs'] = False
                    if os.path.getsize(temp_bin) > 33120:
                        with open(temp_bin, "rb") as temp_file:
                            temp_file.seek(0x8000)
                            app_size_bytes = temp_file.read(16)
                            if (app_size_bytes[0] == 0xAA and app_size_bytes[1] == 0x50 and app_size_bytes[2] == 0x01):
                                for i in range(8):
                                    temp_file.seek(0x8000 + i * 0x20)
                                    app_size_bytes = temp_file.read(16)
                                    if (app_size_bytes[3] in (0x00, 0x20, 0x10)) and app_size_bytes[6] == 0x01:
                                        calc_size = app_size_bytes[0x0A] << 16 | app_size_bytes[0x0B] << 8 | 0x00
                                        version['app_size'] = min(calc_size, int(r.headers.get('Content-Length', 0)) - 0x10000)
                                    elif app_size_bytes[3] == 0x82:
                                        version['spiffs_size'] = app_size_bytes[0x0A] << 16 | app_size_bytes[0x0B] << 8 | 0x00
                                        version['spiffs_offset'] = app_size_bytes[0x06] << 16 | app_size_bytes[0x07] << 8 | app_size_bytes[0x08]
                                        version['spiffs'] = version['file_size'] >= version['spiffs_offset'] + version['spiffs_size']
                            else:
                                version['app_size'] = int(r.headers.get('Content-Length', 0))
                                version['nb'] = True
                except Exception as e:
                    print(f"Erro ao processar {file_url}: {e}")

    if os.path.exists(temp_bin):
        os.remove(temp_bin)

    with open(output_path, 'w') as final_file:
        json.dump(data, final_file)

    files_added_total += files_added
    print(f"\n{filename} finalizado. Arquivos .bin adicionados: {files_added}\n")

print(f"\n\nTotal de arquivos .bin adicionados em todos os JSONs: {files_added_total}\n")
