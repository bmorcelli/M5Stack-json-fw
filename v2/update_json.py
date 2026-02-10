import os
import requests
import json
import argparse


all_device_firmware = "./v2/all_device_firmware.json"
all_device_firmware_old = "./v2/all_device_firmware.old.json"
temp_bin = "./v2/temp.bin"
temp_folder = "./v2/tmp/"

parser = argparse.ArgumentParser()
parser.add_argument(
    "--force-download-all",
    action="store_true",
    default=False,
    help="Force full download by removing cached JSON files first.",
)
args = parser.parse_args()

if args.force_download_all:
    if os.path.exists(all_device_firmware):
        os.remove(all_device_firmware)
    if os.path.exists(all_device_firmware_old):
        os.remove(all_device_firmware_old)

# Passo 1: Renomear arquivo existente (substituindo o .old se já existir)
if os.path.exists(all_device_firmware):
    os.replace(all_device_firmware, all_device_firmware_old)

# Passo 2: Download dos dados da API
url = "https://m5burner-api.m5stack.com/api/firmware"
response = requests.get(url)
data = response.json()
files_added = 0

with open(all_device_firmware, 'w') as new_file:
    json.dump(data, new_file, indent=2)

# Filtrando versões que não terminam com '.bin'
for item in data:
    item['versions'] = [version for version in item['versions'] if version['file'].endswith('.bin') or version['file'].endswith('file')]

# Filtrar para excluir elementos sem versões ou sem arquivos binarios
data = [item for item in data if 'versions' in item and len(item['versions']) > 0]

# Corrigir espaços no início dos nomes e ordenar pelo campo 'name'
for item in data:
    item['name'] = item['name'].strip()
    
# Ordena por "name"
data = sorted(data, key=lambda x: x['name'])

# Carregando dados antigos, se disponíveis
old_data = []
if os.path.exists(all_device_firmware_old):
    with open(all_device_firmware_old, 'r') as old_file:
        old_data = json.load(old_file)
    # Passo 3: Comparação e atualização de dados
    for new_item in data:
        for old_item in old_data:
            if new_item['_id'] == old_item['_id']:
                if 'esp' in old_item:
                    new_item['esp'] = old_item['esp']
                for new_version in new_item['versions']:
                    new_version.pop('change_log', None)
                    new_version.pop('published', None)
                    for old_version in old_item['versions']:
                        if new_version['version'] == old_version['version']:
                            if new_version['file'] == old_version['file']:
                                fields_to_copy = ['Fs', 'as', 'ao', 'ss', 'so', 's', 'nb', 'fs', 'fo', 'f', 'fs2', 'fo2', 'f2', 'invalid']
                                for field in fields_to_copy:
                                    if field in old_version:
                                        new_version[field] = old_version[field]

# Passo 4: Atualizações adicionais com base em downloads parciais e leitura de bytes
for item in data:
    for version in item['versions']:
        process = False
        if 's' in version:
            print(f"{item['name']} - {version['version']} - Ok ", flush=True)
        else:
            process = True
        
        if item.get('category') == 'stickc' and 'esp' not in item:
            # print(f"{item['name']} - {version['version']} - Will check for S3 version ", flush=True)
            process = True            

        if process == True and not 'invalid' in version:
            print(f"{item['name']} - {version['version']} - {version['file']}", flush=True)
            files_added += 1
            file_url = f"https://m5burner.oss-cn-shenzhen.aliyuncs.com/firmware/{version['file']}"
            # time.sleep(random.uniform(0.1, 0.3))  # Pausa aleatória entre 0.1s a 0.2s
            with requests.get(file_url, stream=True) as r:
                version['Fs'] = int(r.headers.get('Content-Length', 0)) # File Size
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


                                
if os.path.exists(temp_bin):
    os.remove(temp_bin)  # Passo 5: Exclusão do arquivo temporário

with open(all_device_firmware, 'w') as final_file:
    json.dump(data, final_file, indent=2)

github_env_path = os.environ.get("GITHUB_ENV")
if github_env_path:
    with open(github_env_path, "a", encoding="utf-8") as env_file:
        env_value = "true" if files_added > 0 else "false"
        env_file.write(f"FILES_ADDED={env_value}\n")


print(f"\n\n\nNúmero de arquivos adicionados {files_added}\n\n\n", flush=True)



