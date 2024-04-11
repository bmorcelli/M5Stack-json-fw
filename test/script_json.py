import os
import requests
import json
import time
import random

# Passo 1: Renomear arquivo existente
if os.path.exists("./test/all_device_firmware.json"):
    os.rename("./test/all_device_firmware.json", "./test/all_device_firmware.old.json")

# Passo 2: Download dos dados da API
url = "https://m5burner-api.m5stack.com/api/firmware"
response = requests.get(url)
data = response.json()

with open("./test/all_device_firmware.json", 'w') as new_file:
    json.dump(data, new_file)

# Carregando dados antigos, se disponíveis
old_data = []
if os.path.exists("./test/all_device_firmware.old.json"):
    with open("./test/all_device_firmware.old.json", 'r') as old_file:
        old_data = json.load(old_file)

# Passo 3: Comparação e atualização de dados
for new_item in data:
    for old_item in old_data:
        if new_item['fid'] == old_item['fid']:
            for new_version in new_item['versions']:
                for old_version in old_item['versions']:
                    if new_version['version'] == old_version['version']:
                        fields_to_copy = ['app_size', 'spiffs_size', 'spiffs_offset', 'spiffs']
                        for field in fields_to_copy:
                            if field in old_version:
                                new_version[field] = old_version[field]

# Passo 4: Atualizações adicionais com base em downloads parciais e leitura de bytes
for item in data:
    # Filtrando versões que não terminam com '.bin'
    item['versions'] = [version for version in item['versions'] if version['file'].endswith('.bin')]
    
    for version in item['versions']:
        print(f"{item['name']} - {version['version']} - {version['file']}", flush=True)
        file_url = f"https://m5burner.oss-cn-shenzhen.aliyuncs.com/firmware/{version['file']}"
        time.sleep(random.uniform(0.1, 0.2))  # Pausa aleatória entre 0.1s a 0.2s
        with requests.get(file_url, stream=True) as r:
            version['file_size'] = int(r.headers.get('Content-Length', 0))
            first_bytes = r.raw.read(33600)
            with open("./test/temp.bin", "wb") as temp_file:
                temp_file.write(first_bytes)

        # Leitura e cálculos
        with open("./test/temp.bin", "rb") as temp_file:
            temp_file.seek(0x804A)
            app_size_bytes = temp_file.read(3)
            version['app_size'] = sum(app_size_bytes)

            temp_file.seek(0x806A)
            spiffs_size_bytes = temp_file.read(3)
            version['spiffs_size'] = sum(spiffs_size_bytes)

            temp_file.seek(0x806D)
            spiffs_offset_bytes = temp_file.read(3)
            version['spiffs_offset'] = sum(spiffs_offset_bytes)

            version['spiffs'] = version['file_size'] >= version['spiffs_offset'] + version['spiffs_size']

os.remove("./test/temp.bin")  # Passo 5: Exclusão do arquivo temporário

# Função para filtrar e criar arquivos específicos
def create_filtered_file(category_name):
    filtered_data = [item for item in data if item['category'] == category_name]
    for item in filtered_data:
        for version in item.get("versions", []):
            version_fields = ["version", "published_at", "file", "app_size", "spiffs_size",  "spiffs_offset", "spiffs"]
            item["versions"] = [{field: version[field] for field in version_fields if field in version} for version in item["versions"]]
    with open(f"./test/{category_name}.json", 'w') as file:
        json.dump(filtered_data, file)

# Criação dos arquivos filtrados
create_filtered_file("cardputer")
create_filtered_file("stickc")

with open("./test/all_device_firmware.json", 'w') as final_file:
    json.dump(data, final_file)
