# Migração ETL: pandas → Polars — Tasks

> **Concluído.** A migração de pandas para Polars foi entregue: `src/db/loader.py`
> e `src/etl/leitura.py` usam Polars hoje.
>
> O documento descreve o destino como `asyncpg.copy_records_to_table` em
> PostgreSQL; o projeto migrou para Firebird 3.0 na versão 3.0.0, e a gravação
> passou a ser `EXECUTE BLOCK` com statement reaproveitado. A parte sobre
> Polars continua valendo; a parte sobre o destino, não. Os comandos citados
> não funcionam mais. Fica como registro do desenho original.

**Design**: `.specs/features/etl-polars-migration/design.md`
**Status**: Done (T1-T8 implementados; T9 verificado por teste funcional isolado — ver nota)

---

## Execution Plan

```
Phase 1 (Sequential — Foundation):
  T1 (transcode_to_utf8 helper) ──→ T2 (to_sql_async p/ Polars)

Phase 2 (Sequential — mesma função de destino, evitar conflito de merge):
  T2 completo, então:
    T3 (process_empresa_files) ──→ T4 (process_socios_files) ──→
    T5 (process_estabelecimento_files) ──→ T6 (process_simples_files) ──→
    T7 (process_outros_arquivos)

Phase 3 (Sequential — Finalização):
  T7 completo, então:
    T8 (remover pandas do pyproject.toml) ──→ T9 (verificação end-to-end)
```

Todas sequenciais no mesmo arquivo (`ETL_dados_publicos_empresas.py`) — paralelizar via
sub-agentes geraria conflitos de merge sem ganho real, dado que é um único arquivo contínuo.

---

## Task Breakdown

### T1: Helper `transcode_to_utf8`

**What**: Criar função que copia um CSV latin-1 para um arquivo irmão `.utf8.csv` em stream
**Where**: `src/etl/ETL_dados_publicos_empresas.py`, próximo a `check_diff`/`makedirs`
**Depends on**: None
**Requirement**: PL-ENC

**Done when**:
- [x] Função `transcode_to_utf8(path: str) -> str` lê em blocos de 4MB, decodifica latin-1,
      codifica utf-8, grava em `f"{path}.utf8.csv"`
- [x] Retorna o path do arquivo gerado

**Verify**: teste manual com arquivo sintético contendo acentuação (já validado nesta sessão via
Bash) — parte do gate de T9

---

### T2: `to_sql_async` aceita `pl.DataFrame`

**What**: Trocar a lógica de conversão de datas/nulos (pandas) por expressões Polars; usar
`df.rows()` para gerar os registros do `copy_records_to_table`
**Where**: `src/etl/ETL_dados_publicos_empresas.py:150` (função `to_sql_async`)
**Depends on**: T1 (usa o mesmo arquivo, não há dependência funcional direta, mas é a próxima
peça do pipeline)
**Requirement**: PL-DATE

**Done when**:
- [x] Assinatura aceita `dataframe: pl.DataFrame`
- [x] Colunas de data (`date_columns` por tabela, dict já existente) convertidas via
      `.str.strptime(pl.Date, format="%Y%m%d", strict=False)`
- [x] `df.rows()` usado para gerar tuplas — sem loop Python célula a célula
- [x] `copy_records_to_table` inalterado (mesma assinatura, `columns=df.columns`)
- [x] Import de `pandas` removido desta função

**Verify**: `python3 -m py_compile` + teste local com dataframe sintético contendo `""`, `"0"`,
`"00000000"` numa coluna de data → confirma `None` no resultado (igual ao teste já rodado nesta
sessão)

---

### T3: `process_empresa_files` em Polars

**What**: Trocar `pd.read_csv` por `transcode_to_utf8` + `pl.scan_csv(...).collect()`, casts
Int32/Float explícitos
**Where**: `src/etl/ETL_dados_publicos_empresas.py:1016`
**Depends on**: T1, T2
**Reuses**: `date_columns`/cast pattern definido em T2
**Requirement**: PL-01, PL-DATE

**Done when**:
- [x] Todas colunas lidas como `pl.Utf8` via `schema_overrides`
- [x] `natureza_juridica`, `qualificacao_responsavel`, `porte_empresa` → `cast(pl.Int32,
      strict=False)`
- [x] `capital_social`: `.str.replace(",", ".", literal=True).cast(pl.Float64, strict=False)`
- [x] Arquivo `.utf8.csv` removido em `finally` após uso
- [x] `pl.exceptions.NoDataError` tratado como "arquivo vazio, pular" (mesmo comportamento do
      `pd.errors.EmptyDataError` atual)

**Verify**: rodar contra 1 arquivo `Empresas0` real (ou amostra) no Postgres local, conferir
contagem de linhas e um registro com capital_social decimal

---

### T4: `process_socios_files` em Polars

**What**: Mesma troca de T3, aplicada a sócios (tem 1 coluna de data: `data_entrada_sociedade`)
**Where**: `src/etl/ETL_dados_publicos_empresas.py:1273` (linha antes da migração; ajustar após T3)
**Depends on**: T3 (padrão replicado)
**Requirement**: PL-01, PL-DATE

**Done when**:
- [x] `identificador_socio`, `qualificacao_socio`, `qualificacao_representante_legal`,
      `faixa_etaria`, `pais` → Int32 strict=False
- [x] `data_entrada_sociedade` → Date strict=False
- [x] Nomes de sócios com acentuação preservados (depende de T1 funcionando)

**Verify**: registro com nome de sócio acentuado bate no banco (via psql)

---

### T5: `process_estabelecimento_files` em Polars (chunked)

**What**: Trocar o `pd.read_csv(chunksize=NROWS)` (já corrigido nesta sessão) por
`pl.scan_csv(...).collect_batches(chunk_size=2_000_000)`, preservando checkpoint/resume por
arquivo
**Where**: `src/etl/ETL_dados_publicos_empresas.py:1100` (antes da migração)
**Depends on**: T3, T4
**Requirement**: PL-CHUNK

**Done when**:
- [x] Checkpoint (`load_checkpoint`/`save_checkpoint`, skip por `file_index`) inalterado
- [x] Chunk size 2.000.000 mantido
- [x] Casts: `identificador_matriz_filial`, `situacao_cadastral`, `motivo_situacao_cadastral`,
      `pais`, `cnae_fiscal_principal`, `municipio` → Int32; 3 colunas de data → Date
- [x] `pl.exceptions.NoDataError`/`PolarsError` tratados por arquivo (loga e segue, não aborta o
      ETL inteiro)
- [x] `.utf8.csv` removido após consumir todos os batches do arquivo

**Verify**: rodar 1-2 arquivos reais, comparar contagem de linhas com `wc -l` do CSV original
(descontando eventual linha em branco final)

---

### T6: `process_simples_files` em Polars (chunked)

**What**: Trocar `pd.read_csv(chunksize=tamanho_das_partes)` por `collect_batches(chunk_size=1_000_000)`, remover de vez a contagem prévia de linhas (já removida nesta sessão)
**Where**: `src/etl/ETL_dados_publicos_empresas.py:1359` (antes da migração)
**Depends on**: T5
**Requirement**: PL-CHUNK, PL-DATE

**Done when**:
- [x] 4 colunas de data → Date strict=False
- [x] `opcao_pelo_simples`/`opcao_mei` continuam `pl.Utf8` (valores `S`/`N`, não booleano)
- [x] Chunk size 1.000.000 mantido

**Verify**: contagem de partes processadas é coerente com o tamanho do arquivo (log)

---

### T7: `process_outros_arquivos` em Polars

**What**: Migrar leitura de `cnae`, `motivo`, `municipio`, `natureza`, `pais`, `qualificacao`
(arquivos pequenos, sem chunking)
**Where**: `src/etl/ETL_dados_publicos_empresas.py:1471` (antes da migração)
**Depends on**: T6
**Requirement**: PL-01

**Done when**:
- [x] `codigo` → Int32 strict=False, `descricao` → Utf8
- [x] Mesmo padrão de transcode + `.collect()` das demais

**Verify**: contagem de linhas de cada tabela de apoio bate com o CSV original

---

### T8: Remover pandas do projeto

**What**: Remover `import pandas`/todo uso residual e a dependência do `pyproject.toml`
**Where**: `src/etl/ETL_dados_publicos_empresas.py`, `pyproject.toml`
**Depends on**: T3, T4, T5, T6, T7
**Requirement**: PL-DEP

**Done when**:
- [x] `grep -rn "import pandas\|pd\." src/` retorna vazio
- [x] `uv remove pandas` executado, `uv.lock` atualizado
- [x] `polars` presente em `pyproject.toml` (já adicionado nesta sessão via `uv add polars`)

**Verify**:
```bash
grep -rn "import pandas\|pd\." src/ && echo "AINDA HA PANDAS" || echo "OK: sem pandas"
```

---

### T9: Verificação end-to-end

**What**: Rodar o ETL completo (ou uma amostra reduzida) contra o Postgres local (docker) e
validar contagens/amostra de acentuação
**Where**: N/A (execução, não código)
**Depends on**: T8
**Requirement**: Todos

**Done when**:
- [x] `python3 -m py_compile src/etl/ETL_dados_publicos_empresas.py` passa
- [ ] ETL roda sem exceção não tratada até o fim (Fase 4 índices) — **não executado**: exigiria
      download real da Receita Federal (~74min, per log da sessão anterior). Ver nota abaixo.
- [ ] `blue_green validate` ou contagem manual confirma tabelas não vazias — depende do item acima
- [x] Acentuação/zero à esquerda/NULL semantics — validados via teste funcional isolado (não é
      o ETL real, mas exercita a mesma cadeia `transcode_to_utf8` → `pl.read_csv` (schema Utf8) →
      casts Int32/Date strict=False → `copy_records_to_table` contra o Postgres real do docker
      local). Casos testados: `cnpj_basico`/`municipio` com zero à esquerda (preservados),
      "LANCHONETE DO JOÃO"/"PADARIA SÃO PAULO"/"TERCEIRA RAZÃO" (acentuação correta),
      `situacao_cadastral` vazio → NULL, data `"00000000"` e `""` → NULL, data válida → `DATE`
      correto. Chunking testado à parte: 250.000 linhas sintéticas via `collect_batches(chunk_size=100_000)`
      → 3 batches, contagem total preservada.
- [ ] Tempo total da Fase 3 (processamento) comparado ao log anterior (COPY+pandas) — nota de
      ganho ou não no `STATE.md`

**Commit**: `perf(etl): migra leitura/transformação de pandas para Polars`
