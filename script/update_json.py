import json
import os
import requests

# Função para baixar o arquivo binário
def download_bin_file(url, filename):
    response = requests.get(url)
    with open(filename, 'wb') as f:
        f.write(response.content)

# Função para filtrar e salvar os dados em um novo arquivo JSON
def filter_and_save_old(data, category, output_filename):
    filtered_data = [item for item in data if item.get('category') == category and any(version.get('published') for version in item.get('versions'))]
    filtered_data = [{k: v for k, v in item.items() if k in ['fid', 'name', 'category', 'author', 'versions']} for item in filtered_data]
    for item in filtered_data:
        item['versions'] = [{k: v for k, v in version.items() if k in ['version', 'published_at', 'file']} for version in item['versions']]
    with open(output_filename, 'w') as f:
        json.dump(filtered_data, f, indent=2)

# Função para filtrar e salvar os dados em um novo arquivo JSON
def filter_and_save(data, category, output_filename):
    filtered_data = [item for item in data if item.get('category') == category and any(version.get('published') for version in item.get('versions', []))]
    final_data = []
    for item in filtered_data:
        versions = item.get('versions', [])
        if versions:
            latest_version = max(versions, key=lambda x: x.get('published_at', ''))
            if 'published_at' in latest_version:
                filtered_item = {
                    'fid': item['fid'],
                    'name': item['name'],
                    'category': item['category'],
                    'author': item['author'],
                    'version': latest_version['version'],
                    'published_at': latest_version['published_at'],
                    'file': latest_version['file']
                }
                final_data.append(filtered_item)
    with open(output_filename, 'w') as f:
        json.dump(final_data, f, indent=2)


# Request à API e salvando os dados em all_device_firmware.json
response = requests.get('https://m5burner-api.m5stack.com/api/firmware')
data = response.json()
with open('./script/all_device_firmware.json', 'w') as f:
    json.dump(data, f, indent=2)

# Filtrando e salvando os dados em cardputer.json e stickc.json
filter_and_save(data, 'cardputer', './script/cardputer.json')
filter_and_save(data, 'stickc', './script/stickc.json')
