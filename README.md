# M5Stack-json-fw
This repo analises the M5Burner list of firmwares, filters, simplifies and keep the json to be used in one of my projects

## Como executar `starred_list.html`

Como a página lê arquivos JSON locais por meio de `fetch`, é necessário servi-la por um pequeno servidor HTTP em vez de abrir o arquivo diretamente no navegador (`file://`). Siga os passos abaixo:

1. Abra um terminal e navegue até a raiz do repositório:

   ```bash
   cd /caminho/para/M5Stack-json-fw
   ```

2. Inicie o servidor especializado que permite salvar as estrelas diretamente no arquivo `starred_list.json`:

   ```bash
   python script/starred_server.py --port 8000
   ```

   Caso deseje servir a partir de outro diretório, utilize a opção `--directory` apontando para o local desejado.

3. Abra o navegador e acesse:

   ```
   http://localhost:8000/starred_list.html
   ```

4. Após terminar, encerre o servidor pressionando `Ctrl+C` no terminal.

Esse processo garante que as requisições `fetch` para `v2/all_device_firmware.json`, `3rd/r/all_devices_firmware.json` e `starred_list.json` sejam atendidas corretamente e que o botão **Submit** atualize o arquivo local apenas com os registros estrelados.
