# Relatório de Validação e Atualização de Links de Repositórios GitHub

## Sumário Executivo

O script `validate_repo_links.py` foi executado com sucesso, validando e atualizando links de arquivos hospedados em repositórios GitHub para versões com **commit hash permanente**, garantindo que os links continuarão válidos mesmo se o arquivo for deletado do repositório.

## Exemplo de Atualização

```diff
- "file": "https://github.com/Xinyuan-LilyGO/T-Deck-Pro/raw/refs/heads/master/firmware/H693_factory_v1.5_20251230.bin"
+ "file": "https://raw.githubusercontent.com/Xinyuan-LilyGO/T-Deck-Pro/420f756c962fecd8efe3363972e55c8d151d6bf6/firmware/H693_factory_v1.5_20251230.bin"
```

Os links agora incluem o **commit hash** no caminho, tornando-os permanentes.

## ❌ Links Inválidos - Requer Ação Manual

### 1. t-deck-pro.json
**Firmware**: Meshtastic Standard UI
- **URL**: `https://github.com/meshtastic/meshtastic.github.io/raw/refs/heads/master/firmware-2.7.13.f4ff210/firmware-t-deck-pro-2.7.13.f4ff210.bin`
- **Problema**: Arquivo não existe mais no repositório
- **Ação recomendada**: Remover este firmware da lista ou atualizar para versão existente

### 2. t-deck.json
**Firmware**: Marauder for T-Deck
- **URL**: `https://github.com/jstockdale/ESP32Marauder-T-Deck/blob/77d9cd16c4f19a7eeb92cab2bc5dcac093706931/Release%20Bins/esp32_marauder_v1_0_0_20250103_tdeck_pre_alpha_aq12.bin`
- **Problema**: Caminho incorreto (usa `/blob/` em vez de `/raw/`) e arquivo não está no commit especificado
- **Ação recomendada**: Verificar se existe versão corrigida do link ou remover

## Como Usar o Script

### Modo de Revisão (sem aplicalr mudanças)
```bash
python3 3rd/validate_repo_links.py --dry-run
```

### Aplicar Mudanças
```bash
python3 3rd/validate_repo_links.py
```

### Com Diretório Customizado
```bash
python3 3rd/validate_repo_links.py --database-dir ./custom/database
```

## Vantagens das Mudanças

1. **Permanência**: URLs com commit hash não são afetadas por exclusão de arquivos
2. **Rastreabilidade**: É possível saber exatamente qual versão estava sendo usado
3. **Segurança**: Evita que mudanças no arquivo afetemmotório sem aviso
4. **Compatibilidade**: Links continuam funcionando por tempo indefinido

## Notas

- O script requer `GITHUB_TOKEN` para melhor taxa de requisições da API
- Sem token, funciona com limite de 60 requisições/hora
- Com token, limite é 5000 requisições/hora
