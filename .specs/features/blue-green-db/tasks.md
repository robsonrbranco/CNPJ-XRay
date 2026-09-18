# Blue-Green Database — Tasks

> **Concluído e superado.** Esta especificação foi escrita para o projeto sobre
> **PostgreSQL**, e descreve a troca blue-green como `ALTER DATABASE ... RENAME`
> entre os bancos `receita_federal` e `receita_federal_staging`. A feature foi
> entregue, mas o projeto migrou para Firebird 3.0 na versão 3.0.0 e a troca
> passou a ser **renomeação de arquivo** — ver `src/blue_green/switch.py`.
>
> Os comandos citados aqui (`uv run`, `ETL_dados_publicos_empresas.py`) não
> funcionam mais. O documento fica como registro do desenho original, não como
> instrução. Para o comportamento atual, ver o código e seus testes em
> `tests/test_switch.py`.

**Design**: `.specs/features/blue-green-db/design.md`
**Status**: Done

---

## Execution Plan

```
Phase 1 (Sequential — Foundation):
  T1 ──→ T2

Phase 2 (Parallel — Core components):
  T2 complete, then:
    ├── T3 [P]  (validator)
    └── T4 [P]  (switcher)

Phase 3 (Sequential — Integration):
  T3 + T4 complete, then:
    T5 ──→ T6
```

---

## Task Breakdown

### T1: Criar módulo `src/blue_green/` com constants

**What**: Criar o pacote `src/blue_green/` com `__init__.py` e `constants.py` contendo as listas `EXPECTED_TABLES` e `EXPECTED_INDEXES`
**Where**: `src/blue_green/__init__.py`, `src/blue_green/constants.py`
**Depends on**: None
**Reuses**: Valores de `src/validation/check_database_status.py:29-51`
**Requirement**: BG-06, BG-07, BG-08

**Done when**:
- [ ] `src/blue_green/__init__.py` criado (pode ser vazio)
- [ ] `src/blue_green/constants.py` exporta `EXPECTED_TABLES: list[str]` com 10 tabelas
- [ ] `src/blue_green/constants.py` exporta `EXPECTED_INDEXES: list[str]` com 7 índices
- [ ] Valores idênticos aos de `check_database_status.py:29-51`

**Verify**:
```bash
python -c "from src.blue_green.constants import EXPECTED_TABLES, EXPECTED_INDEXES; assert len(EXPECTED_TABLES) == 10; assert len(EXPECTED_INDEXES) == 7; print('OK')"
```

**Commit**: `feat(blue-green): add blue_green package with table/index constants`

---

### T2: Criar `StateManager` (`src/blue_green/state.py`)

**What**: Implementar classe `StateManager` que lê e grava `blue_green_state.json` na raiz do projeto
**Where**: `src/blue_green/state.py`
**Depends on**: T1
**Reuses**: Padrão `pathlib.Path` já usado no ETL
**Requirement**: BG-03, BG-04, BG-05

**Interfaces a implementar**:
- `StateManager(state_file_path: str = None)` — padrão: raiz do projeto
- `read() -> dict` — lê JSON; cria com estado vazio se não existir
- `update_staging_downloaded(source_month: str) -> None`
- `update_staging_processed() -> None`
- `promote_staging() -> None` — move staging → active, grava `switched_at` e `last_switch`
- `get_active() -> dict | None`
- `get_staging() -> dict | None`

**Done when**:
- [ ] Todos os métodos implementados
- [ ] `read()` cria o arquivo se não existir (estrutura: `{"active": null, "staging": null, "last_switch": null}`)
- [ ] `update_staging_downloaded()` grava `staging.downloaded_at` (ISO 8601), `staging.source_month`, `staging.database = "receita_federal_staging"`
- [ ] `update_staging_processed()` grava `staging.processed_at` (ISO 8601)
- [ ] `promote_staging()` move staging → active, limpa `staging`, grava `switched_at` e `last_switch`
- [ ] State file corrompido → recria com estado vazio e imprime aviso

**Verify**:
```bash
python -c "
from src.blue_green.state import StateManager
import tempfile, os, json
with tempfile.NamedTemporaryFile(suffix='.json', delete=False) as f:
    path = f.name
os.unlink(path)
sm = StateManager(path)
sm.update_staging_downloaded('01-2025')
sm.update_staging_processed()
sm.promote_staging()
state = sm.read()
assert state['active']['source_month'] == '01-2025'
assert state['active']['switched_at'] is not None
assert state['staging'] is None
print('OK')
"
```

**Commit**: `feat(blue-green): add StateManager for blue_green_state.json`

---

### T3: Criar `BlueGreenValidator` (`src/blue_green/validator.py`) [P]

**What**: Implementar `BlueGreenValidator` que conecta à staging e valida tabelas, registros e índices
**Where**: `src/blue_green/validator.py`
**Depends on**: T2
**Reuses**: Lógica de `get_table_info()` e `get_index_info()` de `src/validation/check_database_status.py:64-104`; `EXPECTED_TABLES`/`EXPECTED_INDEXES` de `constants.py`
**Requirement**: BG-06, BG-07, BG-08, BG-09

**Interfaces a implementar**:
- `@dataclass ValidationResult` com campos: `is_valid`, `missing_tables`, `empty_tables`, `missing_indexes`, property `summary`
- `BlueGreenValidator(db_config: dict)` — config sem `database`
- `async validate(db_name: str = "receita_federal_staging") -> ValidationResult`

**Done when**:
- [ ] `ValidationResult` é dataclass com `is_valid: bool`, `missing_tables: list[str]`, `empty_tables: list[str]`, `missing_indexes: list[str]`
- [ ] `validate()` conecta ao `db_name` especificado
- [ ] `validate()` verifica existência de todas as tabelas de `EXPECTED_TABLES`
- [ ] `validate()` verifica `COUNT(*) > 0` em cada tabela existente
- [ ] `validate()` verifica existência de todos os índices de `EXPECTED_INDEXES`
- [ ] `is_valid = True` somente se `missing_tables`, `empty_tables` e `missing_indexes` todos vazios
- [ ] Se `db_name` não existe → retorna `ValidationResult(is_valid=False, ...)` com mensagem clara
- [ ] `summary` retorna string legível com os problemas encontrados

**Verify**:
```bash
# Requer receita_federal_staging existindo no banco
python -c "
import asyncio
from src.blue_green.validator import BlueGreenValidator
import os; from dotenv import load_dotenv; load_dotenv()
config = {'host': os.getenv('DB_HOST'), 'port': int(os.getenv('DB_PORT', 5432)), 'user': os.getenv('DB_USER'), 'password': os.getenv('DB_PASSWORD')}
result = asyncio.run(BlueGreenValidator(config).validate('banco_inexistente'))
assert not result.is_valid
print('INVALID test: OK')
print(result.summary)
"
```

**Commit**: `feat(blue-green): add BlueGreenValidator with table/index checks`

---

### T4: Criar `BlueGreenSwitcher` (`src/blue_green/switch.py`) [P]

**What**: Implementar `BlueGreenSwitcher` que executa DROP old → rename duplo → update state
**Where**: `src/blue_green/switch.py`
**Depends on**: T2
**Reuses**: Padrão de conexão admin do `create_database_if_not_exists()` (`ETL_dados_publicos_empresas.py:742`); padrão SSL config
**Requirement**: BG-09, BG-10, BG-11, BG-12, BG-13

**Interfaces a implementar**:
- `@dataclass SwitchResult` com: `success: bool`, `message: str`, `active_db: str`, `source_month: str | None`
- `BlueGreenSwitcher(db_config: dict, state_manager: StateManager)`
- `async switch(force: bool = False) -> SwitchResult`
- `async cleanup_old() -> None` — dropa `receita_federal_old` se existir

**Sequência exata do `switch()`**:
1. Se `force=False`: `BlueGreenValidator.validate()` → abortar se INVALID
2. Verificar que `receita_federal_staging` existe → abortar se não
3. Se `receita_federal_old` existe → `pg_terminate_backend` + `DROP DATABASE receita_federal_old`
4. `pg_terminate_backend` em `receita_federal`
5. `ALTER DATABASE receita_federal RENAME TO receita_federal_old`
6. `ALTER DATABASE receita_federal_staging RENAME TO receita_federal`
7. `state_manager.promote_staging()`
8. `DROP DATABASE receita_federal_old` (imediato — invariante dos 2 bancos)

**Done when**:
- [ ] Conexão ao banco `postgres` (não ao `receita_federal`) para todos os comandos DDL
- [ ] Passo 3: `receita_federal_old` é sempre dropado antes do rename se existir
- [ ] Passo 8: `receita_federal_old` é dropado imediatamente após rename duplo
- [ ] Se rename falha no meio → exceção propagada, state file NÃO é atualizado
- [ ] Se `receita_federal_staging` não existe → `SwitchResult(success=False, message="staging não encontrada")`
- [ ] `cleanup_old()` encerra conexões antes de dropar

**Verify**:
```bash
# Teste com banco de staging real (após ETL ter rodado)
python -c "
import asyncio
from src.blue_green.switch import BlueGreenSwitcher
from src.blue_green.state import StateManager
import os; from dotenv import load_dotenv; load_dotenv()
# Apenas verifica que imports e instanciação funcionam sem erros de sintaxe
config = {'host': os.getenv('DB_HOST'), 'port': int(os.getenv('DB_PORT', 5432)), 'user': os.getenv('DB_USER'), 'password': os.getenv('DB_PASSWORD')}
sm = StateManager()
sw = BlueGreenSwitcher(config, sm)
print('BlueGreenSwitcher instanciado: OK')
"
```

**Commit**: `feat(blue-green): add BlueGreenSwitcher with rename and drop-old logic`

---

### T5: Criar CLI unificado (`src/blue_green/cli.py`)

**What**: Implementar script CLI com subcomandos `status`, `validate`, `switch`, `cleanup`
**Where**: `src/blue_green/cli.py`
**Depends on**: T3, T4
**Reuses**: Padrão `Console()` + `rich.table.Table` de `check_database_status.py`; `parse_arguments()` pattern do ETL
**Requirement**: BG-14, BG-15, BG-16

**Done when**:
- [ ] `python src/blue_green/cli.py status` exibe state file formatado (rich table): slot ativo, staging, datas
- [ ] `python src/blue_green/cli.py validate` executa e exibe `ValidationResult.summary` com rich
- [ ] `python src/blue_green/cli.py switch` executa validate + switch; exit code 1 se falhar
- [ ] `python src/blue_green/cli.py switch --force` pula validação
- [ ] `python src/blue_green/cli.py cleanup` dropa `receita_federal_old` se existir; avisa se não existir
- [ ] `status` funciona mesmo sem state file (mostra "Nenhum estado registrado")
- [ ] Todos os subcomandos usam `asyncio.run()` internamente

**Verify**:
```bash
uv run src/blue_green/cli.py status
uv run src/blue_green/cli.py --help
```

**Commit**: `feat(blue-green): add blue_green CLI with status/validate/switch/cleanup`

---

### T6: Modificar ETL para usar staging como destino padrão

**What**: Alterar `ETL_dados_publicos_empresas.py` para carregar em `receita_federal_staging` por padrão e integrar `StateManager`
**Where**: `src/etl/ETL_dados_publicos_empresas.py`
**Depends on**: T5
**Reuses**: `parse_arguments()` existente; `create_database_if_not_exists()` e `create_db_pool()` existentes; `StateManager` de T2
**Requirement**: BG-01, BG-02, BG-03, BG-04

**Mudanças exatas**:
1. `parse_arguments()` — adicionar argumento `--db-target` (default: `"receita_federal_staging"`)
2. `create_database_if_not_exists(db_name=None)` — aceitar parâmetro; usar `db_name or getEnv("DB_NAME")`
3. `create_db_pool(db_name=None)` — idem
4. `main()`:
   - Instanciar `StateManager()` no início
   - Após `download_all_files()`: chamar `state.update_staging_downloaded(source_month=f"{mes:02d}-{ano}")`
   - Passar `args.db_target` para `create_database_if_not_exists()` e `create_db_pool()`
   - Após `create_indexes()`: chamar `state.update_staging_processed()`

**Done when**:
- [ ] `--db-target` aceito via CLI; default é `receita_federal_staging`
- [ ] `create_database_if_not_exists()` e `create_db_pool()` aceitam `db_name` sem quebrar chamadas existentes
- [ ] ETL com `--db-target receita_federal_staging` cria e carrega na staging
- [ ] `blue_green_state.json` é atualizado com `downloaded_at` após download e `processed_at` após índices
- [ ] ETL com `--db-target receita_federal` ainda funciona (modo bypass para manutenção)
- [ ] `receita_federal` (banco ativo) não é tocado quando `--db-target receita_federal_staging`

**Verify**:
```bash
# Checar que --help mostra --db-target
uv run src/etl/ETL_dados_publicos_empresas.py --help

# Checar que state file é criado após ETL (sem executar ETL completo — apenas dry-run de parse)
python -c "
import sys; sys.argv = ['ETL', '--db-target', 'receita_federal_staging', '--help']
# apenas verifica que argparse aceita o flag
"
```

**Commit**: `feat(blue-green): wire ETL to load into staging and update state file`

---

## Validações Pre-Aprovação

### Check 1: Granularidade

| Task | Escopo | Status |
|------|--------|--------|
| T1: constants.py | 1 arquivo, 2 constantes | ✅ Granular |
| T2: StateManager | 1 classe, 1 arquivo | ✅ Granular |
| T3: BlueGreenValidator | 1 classe, 1 arquivo | ✅ Granular |
| T4: BlueGreenSwitcher | 1 classe, 1 arquivo | ✅ Granular |
| T5: CLI | 1 script entry point | ✅ Granular |
| T6: ETL modifications | 4 mudanças cirúrgicas em 1 arquivo | ✅ Granular |

### Check 2: Diagrama vs Dependências

| Task | Depends on (body) | Diagram mostra | Status |
|------|-------------------|----------------|--------|
| T1 | None | Início da fase 1 | ✅ |
| T2 | T1 | T1 → T2 | ✅ |
| T3 [P] | T2 | T2 → T3 (paralelo com T4) | ✅ |
| T4 [P] | T2 | T2 → T4 (paralelo com T3) | ✅ |
| T5 | T3, T4 | T3 + T4 → T5 | ✅ |
| T6 | T5 | T5 → T6 | ✅ |

### Check 3: Testes

Sem `TESTING.md` no projeto (sem suíte de testes automatizados). Todas as tasks usam verificação manual via `python -c` ou `uv run`. Tests: none para todas.

| Task | Layer | Requer | Task diz | Status |
|------|-------|--------|----------|--------|
| T1 | constants | none | none | ✅ |
| T2 | utility class | none | none | ✅ |
| T3 | async validator | none | none | ✅ |
| T4 | async switcher | none | none | ✅ |
| T5 | CLI script | none | none | ✅ |
| T6 | ETL modification | none | none | ✅ |
