# Migração ETL: pandas → Polars — Design

> **Concluído.** A migração de pandas para Polars foi entregue: `src/db/loader.py`
> e `src/etl/leitura.py` usam Polars hoje.
>
> O documento descreve o destino como `asyncpg.copy_records_to_table` em
> PostgreSQL; o projeto migrou para Firebird 3.0 na versão 3.0.0, e a gravação
> passou a ser `EXECUTE BLOCK` com statement reaproveitado. A parte sobre
> Polars continua valendo; a parte sobre o destino, não. Os comandos citados
> não funcionam mais. Fica como registro do desenho original.

**Spec**: `.specs/features/etl-polars-migration/spec.md`
**Status**: Draft

---

## Architecture Overview

```
extract_all_files() (zipfile, inalterado)
        │
        ▼
categorize_extracted_files() (inalterado — só nomes de arquivo)
        │
        ▼
┌───────────────────────────────────────────────────────────────┐
│ NOVO: transcode_to_utf8(path) -> Path                          │
│   lê em stream binário, .decode("latin-1").encode("utf-8")     │
│   grava em "<nome>.utf8.csv" ao lado do original                │
└───────────────────────────────────────────────────────────────┘
        │
        ▼
┌───────────────────────────────────────────────────────────────┐
│ process_*_files(pool)                                           │
│   pl.scan_csv(utf8_path, schema_overrides={todas: pl.Utf8}, ...) │
│   .collect() (arquivos pequenos) ou .collect_batches(N) (grandes)│
│   casts explícitos (Int32/Date/Float) via expressões Polars     │
└───────────────────────────────────────────────────────────────┘
        │
        ▼
to_sql_async(df: pl.DataFrame, pool, table_name)
        │  df.rows() -> list[tuple] (já python-native: str/int/float/date/None)
        ▼
conn.copy_records_to_table(...)  (inalterado — já usa COPY desde a otimização anterior)
```

---

## Componentes

### 1. `transcode_to_utf8(path: str) -> str` (novo helper)

```python
def transcode_to_utf8(path: str) -> str:
    """Copia um CSV latin-1 para um arquivo irmão .utf8.csv (Polars só lê utf8/utf8-lossy)."""
    utf8_path = f"{path}.utf8.csv"
    with open(path, "rb") as fin, open(utf8_path, "wb") as fout:
        while chunk := fin.read(4 * 1024 * 1024):
            fout.write(chunk.decode("latin-1").encode("utf-8"))
    return utf8_path
```

Chamado no início de cada `process_*_files`, por arquivo, antes de abrir com Polars. O caller é
responsável por `os.remove(utf8_path)` num `finally` após consumir os batches daquele arquivo —
evita duplicar permanentemente o espaço em disco (arquivos de estabelecimento/simples são
grandes).

**Por que não usar `IO[bytes]`/`io.BytesIO` em memória em vez de arquivo?** `scan_csv` aceita
`IO[bytes]`, mas isso obrigaria manter o arquivo transcodificado inteiro em RAM antes de começar o
parsing — quebra o objetivo de manter uso de memória baixo em arquivos de vários GB
(estabelecimento, simples). Escrever em disco e usar `collect_batches` mantém o padrão de
streaming já validado na sessão anterior (correção do bug `skiprows`/`nrows`).

### 2. Leitura tipada — todas as colunas como `pl.Utf8` na entrada

Nenhuma coluna recebe tipo numérico direto no `schema_overrides` do `scan_csv`/`read_csv`. Isso
elimina o risco de o parser do Polars inferir tipo e truncar zeros à esquerda em colunas como
`cnpj_basico`, `municipio`, `codigo` — confirmado por teste local nesta sessão (`schema_overrides`
com `pl.Utf8` preserva `"007"`; sem isso, a inferência erraria).

Casts explícitos acontecem depois, com `strict=False` (vira `NULL` em vez de lançar exceção):

| Tabela | Colunas → Int32 (strict=False) | Colunas → Date (strptime strict=False) | Outras |
|---|---|---|---|
| `empresa` | `natureza_juridica`, `qualificacao_responsavel`, `porte_empresa` | — | `capital_social`: replace `,`→`.` + cast Float64 |
| `estabelecimento` | `identificador_matriz_filial`, `situacao_cadastral`, `motivo_situacao_cadastral`, `pais`, `cnae_fiscal_principal`, `municipio` | `data_situacao_cadastral`, `data_inicio_atividade`, `data_situacao_especial` | resto `pl.Utf8` |
| `socios` | `identificador_socio`, `qualificacao_socio`, `qualificacao_representante_legal`, `faixa_etaria`, `pais` | `data_entrada_sociedade` | — |
| `simples` | — | `data_opcao_simples`, `data_exclusao_simples`, `data_opcao_mei`, `data_exclusao_mei` | `opcao_pelo_simples`/`opcao_mei` continuam texto (`S`/`N`) |
| `cnae`,`motivo`,`municipio`,`natureza`,`pais`,`qualificacao` | `codigo` → Int32 | — | `descricao` texto |

Validado localmente: `.cast(pl.Int32, strict=False)` em string vazia → `null`;
`.str.strptime(pl.Date, format="%Y%m%d", strict=False)` em `""`/`"0"`/`"00000000"` → `null`
(mesma semântica do `convert_date_string` atual em pandas).

### 3. `to_sql_async` — adaptação para `pl.DataFrame`

A função já foi reescrita nesta sessão para usar `copy_records_to_table` (COPY). A única mudança
aqui é a origem dos registros:

- Antes: `df.astype(object).where(pd.notnull(df), None)` + `df.itertuples(...)`
- Depois: os casts (Int32/Date/Float) já produzem `null` nativo do Polars; `df.rows()` retorna
  `list[tuple]` com `None` no lugar de `null`, e tipos Python nativos (`str`, `int`, `float`,
  `datetime.date`) — testado localmente, compatível direto com `copy_records_to_table`.

A conversão de colunas de data continua centralizada em `to_sql_async` (mesma tabela
`date_columns` por nome de tabela já existente), só trocando a implementação pandas por expressões
Polars. Isso minimiza o diff e mantém a mesma superfície de decisão (fácil auditar coluna por
coluna).

### 4. Leitura em chunks — `estabelecimento` e `simples`

Troca de `pd.read_csv(chunksize=N)` (usado na correção desta sessão) por:

```python
lf = pl.scan_csv(utf8_path, separator=";", has_header=False,
                  schema_overrides={c: pl.Utf8 for c in raw_columns})
for batch in lf.collect_batches(chunk_size=NROWS):
    ... cast, to_sql_async(batch, pool, table_name) ...
```

`collect_batches` é a API não deprecated (substitui `read_csv_batched`, que emite
`DeprecationWarning` na versão instalada — 1.42.1). Arquivo vazio levanta
`pl.exceptions.NoDataError` (equivalente ao `pd.errors.EmptyDataError` atual) — mesmo padrão de
`try/except` já usado no código atual.

### 5. Arquivos pequenos (`empresa`, `socios`, tabelas de apoio)

Sem chunking — `lf.collect()` direto (equivalente ao `pd.read_csv()` sem `chunksize` de hoje).

### 6. Checkpoint/resume

Sem mudança de contrato — `save_checkpoint`/`load_checkpoint` continuam operando por índice de
arquivo (`file_index`), não por chunk interno. A skip logic em `process_estabelecimento_files`
(pular arquivos já processados) permanece igual, só a leitura interna de cada arquivo muda.

---

## Riscos e Mitigações

| Risco | Mitigação |
|---|---|
| Polars não lê latin-1 nativamente | Transcodificação para UTF-8 em arquivo irmão, validada localmente com acentuação |
| Inferência de tipo do Polars trunca zero à esquerda | `schema_overrides` força `pl.Utf8` em 100% das colunas na leitura; casts são etapa separada e explícita |
| `read_csv_batched` deprecated | Usar `scan_csv().collect_batches()` |
| Transcodificação adiciona I/O extra | Streaming em blocos de 4MB, custo dominado por throughput de disco — esperado marginal frente ao parser Rust do Polars |
| Disco cheio por arquivos `.utf8.csv` temporários | Remover cada `.utf8.csv` em `finally` logo após consumir os batches daquele arquivo |
| Regressão silenciosa em dado real (sem testes automatizados) | Verificação manual: rodar ETL completo contra Postgres local (docker) com uma amostra real, comparar contagens de linha por tabela e inspecionar amostra com acentuação (query direta) |
| **[Confirmado em produção 2026-07-19]** OOM: `to_sql_async` fazia `df.rows()` sobre a tabela/chunk inteira antes de dividir em batches, mantendo DataFrame original + transformado + lista de tuplas Python vivos ao mesmo tempo — servidor de 7.6GB de RAM (Hetzner CX33) matou o processo 3x (`dmesg`/oom-killer, `python3` com ~7.3GB RSS) | Corrigido: cada `copy_batch` agora faz `df.slice(offset, length).rows()` (fatia zero-copy do Polars, materializa só o batch atual em tuplas Python). Pico de memória cai de O(linhas da tabela) para O(batch_size × conexões simultâneas). Validado com teste funcional (8 linhas, batch_size=3, múltiplos batches) contra Postgres real |

---

## Fora do escopo deste design

- Não mexe em `download_all_files`, `extract_all_files`, `check_diff` — só a fase de
  processamento (Fase 3).
- Não mexe em `create_indexes`, `setup_tables`, `blue_green/*`.
