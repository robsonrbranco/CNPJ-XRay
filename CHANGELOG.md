# 📝 Changelog - CNPJ-XRay

> **Aviso de datação.** As entradas **2.2.0 e anteriores** descrevem o projeto
> quando ele rodava sobre **PostgreSQL** e era invocado com **uv**. Os comandos
> citados nelas — `uv run src/etl/ETL_dados_publicos_empresas.py` e afins — não
> funcionam mais: o script foi removido na 3.0.0 e o uv foi abandonado. Elas
> ficam como estão de propósito, porque registram o que era verdade naquela
> data; corrigi-las para a sintaxe atual produziria comandos igualmente
> quebrados e um registro falso. Para instruções válidas, ver o
> [README](README.md) e o [CONFIGURACAO_INICIAL.md](CONFIGURACAO_INICIAL.md).

## [3.0.0] - 2026-09-18

Reescrita do projeto sobre **Firebird 3.0**. É uma mudança incompatível em
tudo: banco, forma de carregar, forma de consultar e forma de instalar.

### 💥 Incompatível

- **PostgreSQL saiu inteiro.** Removidas 7.561 linhas: o ETL
  (`ETL_dados_publicos_empresas.py`), `run_prod.py`, `resume_etl.py`,
  `create_indexes.py`, `check_database_status.py`,
  `check_database_structure.py`, `dump_and_restore.py`,
  `sql_dump_generator.py` e os `.sql` de DDL e tuning. Cada um tem substituto
  no caminho Firebird.
- **uv abandonado.** O `uv.lock` estava congelado em agosto e travava o
  backport `pathlib` do PyPI, que sombreia o módulo da stdlib. Instalação
  passa a ser venv + pip.
- **`requires-python`** de `==3.13.*` para `>=3.13` — a restrição antiga
  excluía o 3.14, que é o interpretador em uso.

### ✨ Novo

- **Camada Firebird** (`src/db/`): schema como fonte da verdade, carga em
  massa com statement reaproveitado, remoção de chave repetida via
  `RDB$DB_KEY` e contorno para a Services API do driver, que falha neste
  ambiente.
- **Download multipart** (`src/etl/download.py`): faixas `Range` de 64 MB com
  fila de pedaços global, retomável, e espera pela origem quando o servidor da
  Receita cai — o que ele fez cerca de 595 vezes em 15 horas na competência
  2026-09. Substitui o download que travava para sempre num socket abandonado.
- **Pipeline de 11 fases** (`src/etl/pipeline.py`) com troca blue-green por
  renomeação de arquivo, validação antes de promover e produção em read-only
  no header.
- **Consulta de empresa** (`src/consulta/empresa.py`), com `--json`.
- **Relatório de qualidade** (`src/validation/qualidade.py`) e a
  [linha de base da 2026-09](docs/qualidade-linha-de-base.md).
- **Primeiros testes do projeto**: 23, sem banco e sem rede, cobrindo o
  caminho de erro da troca blue-green e as sutilezas da leitura dos CSV.

### ⚡ Decisões medidas

- **WIN1252 + WIN_PTBR** em vez de UTF8: base 27% menor, e comparação sem
  acento e sem caixa.
- **Sem reserva de espaço nas páginas** (`USE_FULL`), aplicado na criação:
  −12,4% de tamanho, com 1,86 milhão de linhas a mais.
- **Paralelismo por processo, não thread** — as threads disputam o GIL. 4,7x.
- **Carga sem nenhuma trava.** Sem PRIMARY KEY, NOT NULL ou FOREIGN KEY: a
  fonte traz chave repetida e código órfão reais, e uma constraint abortaria a
  carga. A conferência virou relatório, medindo o estrago em vez de parar por
  causa dele.
- **`page_size` não afeta o tamanho final** — 1,4 MB de diferença em 190 MB
  entre 4K, 8K e 16K. Mantido 16384 pelo outro critério.
- **Índices de `uf` e `capital_social`**: ~80x na maioria das UFs por 439,9 MB.

### 🐛 Correções que custaram caro

- **Encoding é cp1252, não latin-1.** Os dois divergem em 0x80–0x9F; ler como
  latin-1 e gravar em WIN1252 matou uma carga de 40 minutos com
  `UnicodeEncodeError`. Os 5 bytes que cp1252 não define viram `?`.
- **`complemento` tem `;` dentro de aspas** — o corte de bloco passou a
  respeitar paridade de aspas.
- **A configuração criava a base errada**: o `docker-compose.yml` criava o
  banco em UTF8 e sem collation, e o `.env.example` dizia `DB_CHARSET=UTF8`.
  Quem seguisse o exemplo construía uma base 27% maior com ordenação errada.

### 📊 Resultado

Base 2026-09 em produção: **222.197.006 linhas em 34,2 GB**, read-only,
construída em 5,9 h. Inconsistência de 0,0103%, toda vinda da fonte.

---

## [2.2.0] - 2026-01-29

### ✨ Novas Funcionalidades

#### Suporte a Argumentos de Linha de Comando
- **Modo Automático (`--last`)**: Detecta e baixa automaticamente a versão mais recente disponível na Receita Federal
- **Modo Específico (`MM-AAAA`)**: Permite especificar uma versão exata via linha de comando
- **Modo Interativo (padrão)**: Mantém compatibilidade com o comportamento original

#### Detecção Automática de Versão
- Implementada função `get_latest_available_date()` que faz scraping da página da Receita Federal
- Identifica automaticamente a versão mais recente disponível
- Fallback inteligente para data atual em caso de erro

#### Validação Robusta de Entrada
- Parser `parse_date_string()` com validação de formato `MM-AAAA`
- Validação de intervalos de ano (2019 até ano atual + 1)
- Validação de mês (01-12)
- Mensagens de erro claras e informativas

### 📚 Documentação

#### Novos Arquivos
- **EXEMPLOS_USO.md**: Guia completo com exemplos práticos de uso
  - Demonstrações de todos os modos de execução
  - Casos de uso: Cron, CI/CD, análise comparativa
  - Scripts de exemplo em Shell e Python
  - Troubleshooting de erros comuns

#### Atualizações
- **README.md**: Adicionada seção "Modos de Execução do ETL"
- **CLAUDE.md**: Documentação técnica atualizada com:
  - Novos modos de operação
  - Comandos de execução atualizados
  - Casos de uso práticos
  - Versão atualizada para v2.2

### 🔧 Melhorias Técnicas

#### Arquitetura
- Adicionado módulo `argparse` para parsing de argumentos CLI
- Função `get_year_month()` refatorada para suportar múltiplos modos
- Separação clara de responsabilidades entre funções

#### Automação
- Suporte completo a execução não-interativa
- Compatível com scripts, cron jobs e pipelines CI/CD
- Código testável e reutilizável

### 🎯 Casos de Uso Suportados

1. **Automação**: Scripts e cron jobs podem usar `--last` para sempre obter dados atualizados
2. **CI/CD**: Integração fácil em pipelines de GitHub Actions, GitLab CI, etc.
3. **Análise Histórica**: Download de versões específicas para comparações temporais
4. **Desenvolvimento**: Modo interativo mantido para uso manual e testes

### 💡 Exemplos de Uso

```bash
# Modo interativo (padrão)
uv run src/etl/ETL_dados_publicos_empresas.py

# Baixar versão mais recente automaticamente
uv run src/etl/ETL_dados_publicos_empresas.py --last

# Baixar versão específica
uv run src/etl/ETL_dados_publicos_empresas.py 01-2025
uv run src/etl/ETL_dados_publicos_empresas.py 12-2024

# Ver ajuda
uv run src/etl/ETL_dados_publicos_empresas.py --help
```

### ⚙️ Alterações no Código

#### Arquivo: `src/etl/ETL_dados_publicos_empresas.py`

**Novas Funções:**
- `parse_arguments()`: Parseia argumentos de linha de comando
- `get_latest_available_date()`: Detecta versão mais recente via scraping
- `parse_date_string(date_str)`: Valida e parseia formato MM-AAAA

**Modificações:**
- `get_year_month(args=None)`: Refatorada para suportar argumentos CLI
- Adicionada lógica para escolher modo de operação baseado em argumentos

**Imports:**
- `import argparse`: Adicionado para parsing de argumentos

### 🔄 Compatibilidade

- ✅ **Retrocompatível**: Modo interativo funciona exatamente como antes
- ✅ **Python 3.8+**: Compatível com versões modernas do Python
- ✅ **Dependências**: Nenhuma dependência adicional necessária

### 📦 Migração

Não é necessária nenhuma ação para migração. O comportamento padrão (sem argumentos) permanece inalterado.

Usuários que desejam usar os novos recursos podem simplesmente adicionar os argumentos CLI:
- Adicionar `--last` para automação
- Passar `MM-AAAA` para versões específicas

---

## [2.1.0] - 2025-08-15

### ✨ Funcionalidades Anteriores

- Sistema de seleção dinâmica de data (interativo)
- Tratamento robusto de conexões SSL/TLS
- Sistema de recuperação de falhas
- Configurações de ambiente via .env
- Função `check_diff()` com SSL robusto

---

**Versionamento**: Seguimos [Semantic Versioning](https://semver.org/)
- **MAJOR**: Mudanças incompatíveis na API
- **MINOR**: Novas funcionalidades compatíveis
- **PATCH**: Correções de bugs compatíveis
