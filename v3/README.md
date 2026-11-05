# Catálogo M5Burner (API v1)

Execute a partir de qualquer diretório, com Python e `requests` instalados:

```sh
python v3/update_json.py --max-workers 8
```

`all_device_firmware.json` contém o snapshot completo da API pública:
`schemaVersion`, `source`, `fetchedAt`, `devices`, `firmwareCategories`,
`developers`, `rankings` (top 10 semanal e histórico) e `firmwares`.
Cada firmware mantém os campos retornados por `/firmwares/{firmwareId}` e
recebe `versions` de `/versions?scope=PUBLIC`. IDs permanecem strings para
evitar perda de precisão em JavaScript. Descrições estruturadas e datas com
fuso são preservadas como retornadas pela API. Dispositivos e categorias de
firmware são entidades distintas.

Cada versão recebe `binary`: `fileName`, `url`, `verified` e `resolution`,
ou `reason` e os candidatos não encontrados. A URL legada é
`https://m5burner.oss-cn-shenzhen.aliyuncs.com/firmware/{binFileName}`.
Para uploads novos também verificamos
`/community/{userId}/{firmwareId}/{versionId}/bin/{versionId+1}.bin`.
Essa regra é uma hipótese: somente URLs que respondem HTTP 200/206 à
requisição de um byte são publicadas como verificadas. Não baixamos todo o
arquivo para essa verificação. O endpoint de download do site exige um
token de CAPTCHA e não é usado para obter URLs permanentes. Erros de rede
e HTTP transitórios são repetidos e, se persistirem, interrompem a coleta.
HTTP 403/404 é registrado como arquivo indisponível, sem assumir que não existe.

O script também atualiza `../v2/all_device_firmware.json`, mantendo os campos
compatíveis: `cover` recebe `coverUrl` completo, `category` recebe exatamente
`deviceSlug`, `fid` recebe `firmwareId` e `versions[].file` recebe a URL completa.
Firmwares com vários dispositivos geram uma entrada por slug; `_id` recebe
`firmwareId:deviceSlug` nesses casos, enquanto `fid` permanece igual ao ID da API.
A compatibilidade de cada versão prevalece sobre a lista geral do firmware.
Versões são ordenadas da mais antiga para a mais recente.

Versões sem binário verificado continuam no snapshot v3, mas não entram no
catálogo v2; veja `unresolved_binaries.json`. Metadados de análise são
reaproveitados apenas quando a URL do binário coincide (nomes legados são
convertidos em URLs para comparação); traduções exigem texto original igual.
Novos binários usam o analisador compartilhado de `script/firmware_manifest.py`.
`analysis_errors.json` registra exceções dessa análise.

Opções:

- `--force-download-all`: ignora checkpoints e análises anteriores; não apaga os catálogos antes da coleta.
- `--from-snapshot`: regenera v2 usando o snapshot v3 salvo, sem consultar a API de catálogo.
- `--skip-analysis`: preserva análises anteriores e adia a análise dos binários novos.
- `--max-workers N`: concorrência da coleta e da análise (padrão 8).

Checkpoints em `.cache/` são ignorados pelo Git e permitem retomar uma coleta
interrompida. O snapshot anterior também funciona como cache nas execuções do
workflow: alterações de conteúdo invalidam o cache; contadores não. Binários
anteriormente indisponíveis são verificados novamente. Estatísticas gerais
dos firmwares são atualizadas pela listagem; contadores individuais das versões
em cache permanecem os da coleta anterior até uma alteração de conteúdo ou
uma execução com `--force-download-all`. Somente uma coleta
completa substitui o snapshot. Cada JSON é
substituído atomicamente. `FILES_ADDED` em `GITHUB_ENV` mantém a integração
com o workflow, considerando alterações de conteúdo nos dois catálogos e
ignorando mudanças isoladas de contadores e do horário da coleta.
O workflow de atualização utiliza este script; `v2/update_json.py` permanece
como referência histórica da API antiga.

Fontes: [M5Burner](https://burner.m5stack.com/),
[dispositivos](https://burner.m5stack.com/api/v1/devices),
[firmwares](https://burner.m5stack.com/api/v1/firmwares?pageNum=1&pageSize=50&sort=LATEST),
[categorias](https://burner.m5stack.com/api/v1/firmware-categories).
