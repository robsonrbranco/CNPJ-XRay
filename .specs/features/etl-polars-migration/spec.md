# Migração ETL: pandas → Polars — Especificação

> **Concluído.** A migração de pandas para Polars foi entregue: `src/db/loader.py`
> e `src/etl/leitura.py` usam Polars hoje.
>
> O documento descreve o destino como `asyncpg.copy_records_to_table` em
> PostgreSQL; o projeto migrou para Firebird 3.0 na versão 3.0.0, e a gravação
> passou a ser `EXECUTE BLOCK` com statement reaproveitado. A parte sobre
> Polars continua valendo; a parte sobre o destino, não. Os comandos citados
> não funcionam mais. Fica como registro do desenho original.

## Problem Statement

O ETL (`src/etl/ETL_dados_publicos_empresas.py`) usa pandas para ler e transformar os CSVs da
Receita Federal antes de gravar via `asyncpg.copy_records_to_table`. Os arquivos são grandes
(estabelecimento e simples chegam a dezenas de milhões de linhas) e o parsing/transformação em
pandas é single-threaded e relativamente lento. Polars usa um parser CSV nativo em Rust,
multi-thread, com menor overhead de memória — o objetivo é trocar pandas por Polars nos pontos de
leitura/transformação sem alterar o schema do banco, o comportamento de nulos/datas, nem a lógica
de checkpoint/resume já existente.

## Goals

- [ ] Nenhuma tabela (`empresa`, `estabelecimento`, `socios`, `simples`, `cnae`, `motivo`,
      `municipio`, `natureza`, `pais`, `qualificacao`) muda de schema ou de dados carregados
- [ ] `pandas` removido de `pyproject.toml` — Polars é a única lib de dataframe no projeto
- [ ] Leitura em chunks de `estabelecimento` (2M linhas) e `simples` (1M linhas) preservada —
      sem carregar arquivo inteiro em memória
- [ ] Checkpoint/resume por arquivo em `process_estabelecimento_files` continua funcionando
- [ ] Datas `YYYYMMDD` continuam virando `NULL` quando vazias, `"0"` ou `"00000000"`
- [ ] Colunas com zeros à esquerda (`cnpj_basico`, `cnpj_ordem`, `cnpj_dv`, códigos) não perdem os
      zeros (risco real: Polars infere tipo por coluna se não for restringido)
- [ ] Caracteres acentuados (nomes de sócios, razão social, logradouro) continuam corretos —
      arquivos são latin-1, o parser do Polars só aceita utf8/utf8-lossy nativamente
- [ ] `capital_social` continua convertendo vírgula decimal (`"1000,50"`) para número
- [ ] Tempo de processamento (fase 3 do ETL) reduz de forma perceptível vs. a versão atual

## Out of Scope

| Item | Motivo |
|------|--------|
| Trocar o mecanismo de insert (`copy_records_to_table`) | Já otimizado nesta sessão; só a origem dos dados (pandas→Polars) muda |
| Paralelizar download/extração | Fora do escopo — só a Fase 3 (processamento) é afetada |
| Suíte de testes automatizados | Projeto não tem testes; verificação é manual contra Postgres local (docker) |
| Migrar `resume_etl.py` / `blue_green/` | Não usam pandas — grep confirmou (`ETL_dados_publicos_empresas.py` é o único ponto) |

---

## Constraint técnica crítica (achado da pesquisa)

O leitor CSV nativo do Polars (`read_csv`, `scan_csv`, `read_csv_batched`) só aceita
`encoding="utf8"` ou `"utf8-lossy"`. Não existe suporte nativo a `latin-1`/`iso-8859-1`. Os
arquivos da Receita Federal são latin-1 (contêm "JOÃO", "RAZÃO", etc.).

**Mitigação validada**: transcodificar cada CSV extraído de latin-1 para UTF-8 (leitura binária
em stream, `bytes.decode("latin-1").encode("utf-8")`, gravação em arquivo `.utf8.csv` irmão) antes
de abrir com Polars. Testado localmente com acentuação — round-trip correto. Como latin-1 é
1 byte = 1 caractere, o chunking do stream de transcodificação pode cortar em qualquer offset sem
risco de quebrar um caractere multibyte.

`read_csv_batched` está **deprecated** na versão instalada (1.42.1) — usar
`pl.scan_csv(...).collect_batches(chunk_size=N)`, que retorna um iterador de `DataFrame` e aceita
o mesmo padrão de streaming.

---

## User Stories

### P1: Leitura tipada sem perda de zeros à esquerda ⭐ MVP

**User Story**: Como operador do ETL, quero que os CSVs sejam lidos com todas as colunas como
string (`pl.Utf8`) explicitamente, para que códigos com zero à esquerda (cnpj_basico, municipio,
etc.) não sejam corrompidos por inferência de tipo.

**Acceptance Criteria**:
1. WHEN um CSV é lido THEN system SHALL declarar `schema_overrides` com `pl.Utf8` para todas as
   colunas (nenhuma inferência automática de tipo)
2. WHEN uma coluna numérica (ex: `situacao_cadastral`) precisa virar inteiro THEN system SHALL
   fazer o `.cast(pl.Int32, strict=False)` como etapa explícita pós-leitura, não na leitura do CSV
3. WHEN o valor não é um inteiro válido (vazio, texto) THEN system SHALL gravar `NULL`, nunca
   lançar exceção que aborte o arquivo inteiro

**Independent Test**: ler um CSV sintético com um código `"007"` e confirmar que a coluna
resultante mantém `"007"` como texto ou `7` como inteiro (conforme o tipo alvo da tabela), nunca
perde o zero à esquerda quando o destino é `TEXT`.

---

### P1: Encoding latin-1 preservado via transcodificação ⭐ MVP

**Requirement ID**: PL-ENC

**User Story**: Como operador, quero que nomes com acentuação (sócios, razão social, logradouro)
continuem corretos no banco após a migração, mesmo o Polars não suportando latin-1 nativamente.

**Acceptance Criteria**:
1. WHEN um arquivo CSV extraído é processado THEN system SHALL transcodificá-lo de latin-1 para
   UTF-8 em um arquivo irmão antes de abrir com Polars
2. WHEN a transcodificação termina THEN system SHALL remover o arquivo UTF-8 temporário após o
   processamento (não duplicar permanentemente o espaço em disco)
3. WHEN o texto contém caracteres acentuados (ã, ç, é, etc.) THEN system SHALL gravá-los
   corretamente no Postgres (mesmo resultado que a versão pandas com `encoding="latin-1"`)

**Independent Test**: processar um CSV sintético com "RAZÃO SOCIAL LTDA" e conferir que a linha
no banco tem o texto idêntico, sem `?` ou caracteres substitutos.

---

### P1: Datas e nulos com mesma semântica ⭐ MVP

**Requirement ID**: PL-DATE

**Acceptance Criteria**:
1. WHEN uma coluna de data (`data_situacao_cadastral`, `data_inicio_atividade`,
   `data_situacao_especial`, `data_entrada_sociedade`, `data_opcao_simples`,
   `data_exclusao_simples`, `data_opcao_mei`, `data_exclusao_mei`) tem valor `""`, `"0"` ou
   `"00000000"` THEN system SHALL gravar `NULL`
2. WHEN o valor é uma data válida `YYYYMMDD` THEN system SHALL converter para `DATE` do Postgres
3. WHEN `capital_social` usa vírgula decimal THEN system SHALL converter para `NUMERIC` com ponto

**Independent Test**: validado localmente nesta sessão via `pl.col(...).str.strptime(pl.Date,
format="%Y%m%d", strict=False)` — confirma `NULL` para `"00000000"`/`"0"`/`""` e data correta para
valores válidos.

---

### P1: Chunking e checkpoint preservados ⭐ MVP

**Requirement ID**: PL-CHUNK

**Acceptance Criteria**:
1. WHEN `estabelecimento` é processado THEN system SHALL ler em chunks de 2.000.000 linhas via
   `scan_csv(...).collect_batches(chunk_size=2_000_000)`, sem carregar o arquivo inteiro
2. WHEN `simples` é processado THEN system SHALL usar chunks de 1.000.000 linhas do mesmo modo
3. WHEN o checkpoint indica um `file_index` de retomada em `estabelecimento` THEN system SHALL
   pular os arquivos já processados (comportamento idêntico ao atual)
4. WHEN um arquivo está vazio ou ausente THEN system SHALL logar e seguir para o próximo, sem
   abortar o processo inteiro (equivalente ao atual `pd.errors.EmptyDataError` → agora
   `pl.exceptions.NoDataError`)

---

### P2: Remoção do pandas

**Requirement ID**: PL-DEP

**Acceptance Criteria**:
1. WHEN a migração termina THEN system SHALL remover `pandas` de `pyproject.toml`
2. WHEN `grep -rn "import pandas\|pd\." src/` roda THEN system SHALL retornar vazio

---

## Non-Goals / Riscos assumidos

- Sem suíte automatizada — verificação final é rodar contra o Postgres local (docker) com uma
  amostra real extraída, comparando contagens de linha e uma amostra de valores com acentuação.
- A transcodificação latin-1→UTF-8 adiciona uma passada extra de I/O por arquivo. Esperado ser
  marginal frente ao ganho do parser Rust do Polars, mas é um trade-off explícito, não um ganho
  gratuito.
