# Blue-Green Database — Design

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

**Spec**: `.specs/features/blue-green-db/spec.md`
**Status**: Draft

---

## Architecture Overview

```
┌──────────────────────────────────────────────────────────────────┐
│  Operador                                                         │
│                                                                   │
│  uv run ETL_dados_publicos_empresas.py --last                    │
│         └─► ETL carrega em receita_federal_staging               │
│             └─► grava downloaded_at, processed_at no state file  │
│                                                                   │
│  uv run src/blue_green/cli.py validate                           │
│         └─► BlueGreenValidator.validate(staging)                 │
│             └─► retorna VALID / INVALID com relatório            │
│                                                                   │
│  uv run src/blue_green/cli.py switch                             │
│         └─► validate() → se VALID:                               │
│             └─► BlueGreenSwitcher.switch()                       │
│                 ├── dropa receita_federal_old se existir (auto)   │
│                 ├── encerra conexões em receita_federal           │
│                 ├── ALTER DATABASE receita_federal RENAME TO old  │
│                 ├── ALTER DATABASE staging RENAME TO receita...   │
│                 └── StateManager.promote_staging()                │
└──────────────────────────────────────────────────────────────────┘

   INVARIANTE: no máximo 2 bancos existem em qualquer momento
   ├── receita_federal        (ativo — nunca tocado durante carga)
   └── receita_federal_staging  (destino do ETL — ou não existe)

   receita_federal_old é TRANSITÓRIO: criado no switch e dropado
   automaticamente no próximo switch. Nunca coexiste com staging.
```

---

## Invariante de Recursos

**Máximo 2 bancos CNPJ ativos ao mesmo tempo.**

| Situação | Bancos existentes |
|----------|-------------------|
| Estado normal (pós-switch) | `receita_federal` |
| Durante carga ETL | `receita_federal` + `receita_federal_staging` |
| Imediatamente após switch | `receita_federal` + `receita_federal_old` |
| **Proibido** | `receita_federal` + `staging` + `old` simultaneamente |

**Regra no switch**: antes de qualquer rename, o switcher SEMPRE dropa `receita_federal_old`
se existir — sem perguntar, sem flag `--drop-old`. Isso garante a invariante.

---

## Code Reuse Analysis

### Existing Components to Leverage

| Component | Location | How to Use |
|-----------|----------|------------|
| `create_database_if_not_exists()` | `src/etl/ETL_dados_publicos_empresas.py:742` | Extrair lógica de conexão ao `postgres` DB para helper reutilizável |
| `create_db_pool()` | `src/etl/ETL_dados_publicos_empresas.py:809` | Adicionar parâmetro `db_name` opcional; padrão muda para `receita_federal_staging` |
| `parse_arguments()` | `src/etl/ETL_dados_publicos_empresas.py:270` | Adicionar `--db-target` como argumento opcional |
| `get_table_info()` + `get_index_info()` | `src/validation/check_database_status.py:64-104` | Copiar lógica para `BlueGreenValidator` (não importar — arquivo tem `main()` global) |
| `EXPECTED_TABLES` + `EXPECTED_INDEXES` | `src/validation/check_database_status.py:29-51` | Mover para `src/blue_green/constants.py` |
| SSL config pattern | `src/etl/ETL_dados_publicos_empresas.py:754-760` | Extrair para helper `build_ssl_config(ssl_mode)` em `src/blue_green/db_utils.py` |
| `ano, mes` globais | `src/etl/ETL_dados_publicos_empresas.py:487` | Usar como `source_month` no state file — já formatado como `MM-AAAA` |

### Integration Points

| System | Integration Method |
|--------|-------------------|
| ETL main flow | `main()` grava timestamps no state file nos pontos de download e conclusão |
| asyncpg | Switch usa conexão direta ao banco `postgres` (admin DB) para executar ALTER DATABASE e DROP DATABASE |
| `.env` | Sem mudança — `DB_NAME` continua apontando para `receita_federal`; staging é hardcoded |

---

## Components

### StateManager

- **Purpose**: Ler e gravar `blue_green_state.json` com os metadados de cada slot
- **Location**: `src/blue_green/state.py`
- **Interfaces**:
  - `StateManager(state_file_path: str)` — construtor; usa project root por padrão
  - `read() -> dict` — lê state file; cria com estado vazio se não existir
  - `update_staging_downloaded(source_month: str) -> None` — grava `staging.downloaded_at` + `staging.source_month` + `staging.database`
  - `update_staging_processed() -> None` — grava `staging.processed_at`
  - `promote_staging() -> None` — move staging → active, limpa slot staging, grava `last_switch` e `switched_at`
  - `get_active() -> dict | None`
  - `get_staging() -> dict | None`
- **Dependencies**: `json`, `datetime`, `pathlib`

**Estrutura do arquivo:**
```json
{
  "active": {
    "database": "receita_federal",
    "source_month": "2025-01",
    "downloaded_at": "2025-01-15T10:30:00",
    "processed_at": "2025-01-15T14:45:00",
    "switched_at": "2025-01-15T15:00:00"
  },
  "staging": {
    "database": "receita_federal_staging",
    "source_month": "2025-02",
    "downloaded_at": "2025-02-10T09:00:00",
    "processed_at": null
  },
  "last_switch": "2025-01-15T15:00:00"
}
```

---

### BlueGreenValidator

- **Purpose**: Validar que `receita_federal_staging` tem estrutura e dados suficientes para ser promovido
- **Location**: `src/blue_green/validator.py`
- **Interfaces**:
  - `BlueGreenValidator(db_config: dict)`
  - `async validate(db_name: str = "receita_federal_staging") -> ValidationResult`
- **Dependencies**: `asyncpg`, `src/blue_green/constants.py`
- **Reuses**: lógica de `get_table_info()` e `get_index_info()` de `check_database_status.py`

---

### BlueGreenSwitcher

- **Purpose**: Executar o rename atômico dos bancos e atualizar o state file
- **Location**: `src/blue_green/switch.py`
- **Interfaces**:
  - `BlueGreenSwitcher(db_config: dict, state_manager: StateManager)`
  - `async switch(force: bool = False) -> SwitchResult`
- **Dependencies**: `asyncpg`, `BlueGreenValidator`, `StateManager`

**Sequência completa do switch:**
```
1. Se force=False: validar staging → abortar se INVALID
2. Se receita_federal_old EXISTE → DROP DATABASE receita_federal_old  ← sempre automático
3. Encerrar conexões ativas em receita_federal
4. ALTER DATABASE receita_federal RENAME TO receita_federal_old
5. ALTER DATABASE receita_federal_staging RENAME TO receita_federal
6. state_manager.promote_staging()
7. DROP DATABASE receita_federal_old  ← dropa imediatamente após switch bem-sucedido
```

**Nota sobre o DROP imediato (passo 7):** o `receita_federal_old` existe apenas como
segurança durante o switch. Uma vez que o rename duplo foi concluído com sucesso e o
state file foi atualizado, o old é dropado imediatamente — nunca fica "sobrando".

**SQL executado (conexão ao banco `postgres`):**
```sql
-- Drop old se existir (pré-condição)
SELECT pg_terminate_backend(pid) FROM pg_stat_activity
WHERE datname = 'receita_federal_old' AND pid <> pg_backend_pid();
DROP DATABASE IF EXISTS receita_federal_old;

-- Encerrar conexões no ativo
SELECT pg_terminate_backend(pid) FROM pg_stat_activity
WHERE datname = 'receita_federal' AND pid <> pg_backend_pid();

-- Rename
ALTER DATABASE receita_federal RENAME TO receita_federal_old;
ALTER DATABASE receita_federal_staging RENAME TO receita_federal;

-- Drop old imediatamente
DROP DATABASE receita_federal_old;
```

---

### constants.py

- **Purpose**: Centralizar listas de tabelas e índices esperados
- **Location**: `src/blue_green/constants.py`
- **Interfaces**: `EXPECTED_TABLES: list[str]`, `EXPECTED_INDEXES: list[str]`
- **Reuses**: valores de `src/validation/check_database_status.py:29-51`

---

### CLI

- **Purpose**: Ponto de entrada unificado para operações blue-green
- **Location**: `src/blue_green/cli.py`
- **Subcomandos**:
  - `status` — exibe state file formatado (ativo, staging, datas)
  - `validate` — executa validação e imprime resultado
  - `switch` — valida + switch (aceita `--force` para pular validação)
  - `cleanup` — dropa manualmente `receita_federal_old` se por algum motivo existir
- **Reuses**: padrão `Console()` + `rich.table.Table` dos scripts existentes

---

### Modificações no ETL (`ETL_dados_publicos_empresas.py`)

**1. `parse_arguments()` — adicionar `--db-target`:**
```python
parser.add_argument(
    "--db-target",
    default="receita_federal_staging",
    help="Banco de dados destino (padrão: receita_federal_staging)"
)
```

**2. `create_database_if_not_exists()` + `create_db_pool()` — aceitar `db_name`:**
```python
async def create_database_if_not_exists(db_name: str = None):
    database = db_name or getEnv("DB_NAME")
    ...

async def create_db_pool(db_name: str = None):
    database = db_name or getEnv("DB_NAME")
    ...
```

**3. `main()` — integrar StateManager:**
```python
# Após download concluído (pós download_all_files()):
state.update_staging_downloaded(source_month=f"{mes:02d}-{ano}")

# Após create_indexes() concluído:
state.update_staging_processed()
```

---

## Data Models

### ValidationResult

```python
@dataclass
class ValidationResult:
    is_valid: bool
    missing_tables: list[str]
    empty_tables: list[str]
    missing_indexes: list[str]

    @property
    def summary(self) -> str:
        if self.is_valid:
            return "VALID — staging pronta para switch"
        issues = []
        if self.missing_tables:
            issues.append(f"Tabelas ausentes: {', '.join(self.missing_tables)}")
        if self.empty_tables:
            issues.append(f"Tabelas vazias: {', '.join(self.empty_tables)}")
        if self.missing_indexes:
            issues.append(f"Índices ausentes: {', '.join(self.missing_indexes)}")
        return "INVALID — " + "; ".join(issues)
```

---

## Error Handling Strategy

| Error Scenario | Handling | User Impact |
|----------------|----------|-------------|
| `receita_federal_staging` não existe no switch | Aborta antes do rename | Nenhum impacto no banco ativo |
| DROP de `receita_federal_old` falha | Aborta o switch inteiro antes de qualquer rename | Nenhum impacto; operador precisa dropar manualmente |
| Rename falha após primeiro ALTER | Captura exceção; imprime estado atual; NÃO atualiza state file | Operador precisa intervir (staging ainda existe com outro nome) |
| State file corrompido | Recria com estado vazio; avisa operador | Perde histórico; não afeta bancos |
| Sem permissão para ALTER/DROP DATABASE | Exceção asyncpg com mensagem clara de superuser necessário | Operador ajusta permissões |
| Staging com `source_month` igual ao active | Aviso (não bloqueia) | Operador decide continuar |

---

## Tech Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Drop automático de `old` | Sempre automático, sem flag | Invariante de "máximo 2 bancos" é inegociável; não deve depender de ação do operador |
| Drop de `old` imediato pós-switch | Dropado logo após rename duplo bem-sucedido | Não há motivo para manter o old após o switch — ocupa espaço sem utilidade |
| Conexão ao banco `postgres` para DDL | Admin DB | ALTER/DROP DATABASE não pode ser executado de dentro do banco alvo; padrão já usado no ETL |
| `receita_federal_staging` hardcoded | Não via env | Simplifica; o nome da staging não precisa ser mutável |
| Sem `pg_dump`/restore para switch | Rename (ALTER DATABASE) | Instantâneo vs horas para ~50GB |
| State file na raiz do projeto | `blue_green_state.json` | Próximo do `.env`; não fica dentro de `src/` |
