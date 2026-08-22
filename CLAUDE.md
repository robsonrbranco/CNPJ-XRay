# 🤖 CLAUDE.md - CNPJ-XRay (Sistema ETL)

## 📋 Resumo das Melhorias Implementadas

Este documento detalha as melhorias implementadas no sistema ETL de dados públicos do CNPJ para resolver problemas de conectividade, configuração e robustez do processo.

## 🛠️ Melhorias Técnicas Implementadas

### 1. **Sistema de Seleção Dinâmica de Data** 🗓️
- **Funcionalidade**: Múltiplos modos de seleção de ano/mês dos dados
- **Implementação**: Função `get_year_month()` com suporte a argumentos CLI e validação robusta
- **Modos de operação**:
  - **Interativo** (padrão): Interface interativa para seleção manual
  - **Automático** (`--last`): Detecta e baixa a versão mais recente disponível na Receita Federal
  - **Específico** (`MM-AAAA`): Baixa uma versão específica via linha de comando
- **Benefícios**:
  - Usuário pode escolher qualquer mês/ano disponível (2019 - atual)
  - Validação automática de entrada
  - URL construída dinamicamente: `{ano}-{mes:02d}`
  - Detecção automática da versão mais recente via scraping
  - Suporte completo a automação via scripts e CI/CD

### 2. **Tratamento Robusto de Conexões SSL/TLS** 🔐
- **Problema Original**: Falhas de conexão com certificados SSL da Receita Federal
- **Soluções Implementadas**:
  - Configuração SSL permissiva (`ssl_context.verify_mode = ssl.CERT_NONE`)
  - Retry automático com backoff exponencial
  - User-Agent atualizado para simular navegador moderno
  - Timeouts apropriados (30s requisições, 120s downloads)
  - Tratamento específico de `ConnectError`, `TimeoutException` e `SSLError`

### 3. **Sistema de Recuperação de Falhas** ⚡
- **Função `get_html_with_retry()`**: Até 3 tentativas para acessar página principal
- **Downloads assíncronos robustos**: Configuração SSL e limites de conexão otimizados
- **Logs detalhados**: Identificação precisa de pontos de falha

### 4. **Correção de Configurações do Ambiente** ⚙️
- **Variáveis adicionadas ao .env**:
  - `OUTPUT_FILES_PATH=./dados/downloads`
  - `EXTRACTED_FILES_PATH=./dados/extracted`
- **Problema resolvido**: Erro de diretórios `NoneType` durante extração

### 5. **Melhorias na Função `check_diff()`** 📁
- Aplicação das mesmas configurações SSL robustas
- Tratamento de exceções para verificação de arquivos
- Fallback seguro em caso de erro (força download)

## 🚀 Comandos de Teste e Validação

### Testar Conectividade com Banco
```bash
nc -zv localhost 5436
```

### Verificar Status dos Containers
```bash
docker ps | grep postgres
```

### Executar ETL

**Modo Interativo (padrão):**
```bash
uv run src/etl/ETL_dados_publicos_empresas.py
```

**Baixar a versão mais recente automaticamente:**
```bash
uv run src/etl/ETL_dados_publicos_empresas.py --last
```

**Baixar uma versão específica:**
```bash
# Formato: MM-AAAA
uv run src/etl/ETL_dados_publicos_empresas.py 01-2025
uv run src/etl/ETL_dados_publicos_empresas.py 12-2024
```

**Ver ajuda e exemplos:**
```bash
uv run src/etl/ETL_dados_publicos_empresas.py --help
```

## 📊 Impacto das Melhorias

### Antes das Melhorias:
- ❌ Falhas SSL frequentes
- ❌ Downloads interrompidos (0/37 arquivos)
- ❌ Extrações falhando por variáveis `None`
- ❌ Erro de conexão com banco (`Connection reset by peer`)

### Depois das Melhorias:
- ✅ Conexões SSL robustas com retry automático
- ✅ Downloads funcionando com configuração otimizada
- ✅ Extrações funcionais com caminhos corretos
- ✅ Seleção interativa, automática ou via CLI de ano/mês
- ✅ Detecção automática da versão mais recente (`--last`)
- ✅ Suporte a automação via argumentos de linha de comando
- ✅ Tratamento gracioso de erros com mensagens informativas

## 🔧 Configuração Recomendada

### Arquivo .env (variáveis obrigatórias):
```bash
# BANCO DE DADOS
DB_HOST=localhost
DB_PORT=5436
DB_NAME=receita_federal
DB_USER=postgres
DB_PASSWORD=sua_senha

# CAMINHOS OBRIGATÓRIOS
OUTPUT_FILES_PATH=./dados/downloads
EXTRACTED_FILES_PATH=./dados/extracted
```

## 🐛 Troubleshooting

### Se ainda encontrar problemas SSL:
1. Verifique conectividade: `curl -I https://arquivos.receitafederal.gov.br`
2. Confirme data/hora do sistema
3. Execute com logs detalhados

### Se downloads falharem:
1. Verifique espaço em disco
2. Confirme permissões nos diretórios de destino
3. Teste conectividade de rede

### Se banco não conectar:
1. Confirme que PostgreSQL está rodando: `docker ps | grep postgres`
2. Teste conectividade: `nc -zv localhost 5436`
3. Verifique credenciais no .env

## 📝 Notas de Desenvolvimento

- **Python 3.13**: Compatibilidade total testada
- **Bibliotecas principais**: `httpx`, `asyncpg`, `pandas`, `rich`
- **Padrão de SSL**: Permissivo para contornar limitações dos servidores governamentais
- **Logs**: Rich console com formatação colorida e progress bars

## 🎯 Casos de Uso

### 1. Automação em Cron/Scheduler
Para baixar automaticamente a versão mais recente todo mês:
```bash
# Crontab: Todo dia 5 do mês às 2h da manhã
0 2 5 * * cd /path/to/projeto && uv run src/etl/ETL_dados_publicos_empresas.py --last
```

### 2. CI/CD Pipeline
```yaml
# GitHub Actions / GitLab CI
- name: Download CNPJ Data
  run: |
    uv run src/etl/ETL_dados_publicos_empresas.py --last
```

### 3. Análise Histórica
Para comparar dados de períodos específicos:
```bash
# Baixar dados de janeiro de 2025
uv run src/etl/ETL_dados_publicos_empresas.py 01-2025

# Baixar dados de dezembro de 2024
uv run src/etl/ETL_dados_publicos_empresas.py 12-2024
```

### 4. Desenvolvimento e Testes
Para testes locais com versões específicas:
```bash
# Modo interativo permite escolha manual
uv run src/etl/ETL_dados_publicos_empresas.py
```

---

**🤖 Gerado com Claude Code**  
Data: 2026-01-29  
Versão: ETL v2.2 com argumentos CLI e detecção automática