# 📝 Changelog - CNPJ-XRay

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
