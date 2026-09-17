# SQL

## O que sobrou aqui

Apenas `consulta_cnpj_receita_base_dos_dados.sql`, e ele **não roda contra esta
base**. É uma consulta de referência contra o espelho público do CNPJ na
[Base dos Dados](https://basedosdados.org/) (BigQuery), cujo schema é outro —
`cnpj` já montado, `sigla_uf`, `id_municipio`. Serve para conferir um resultado
por fora, com dado de outra origem.

Os demais arquivos que existiam aqui eram DDL e tuning de PostgreSQL
(`banco_de_dados.sql`, `database_setup.sql`) e a consulta de ficha de empresa
(`consulta_empresa_completa.sql`). Foram removidos porque o projeto passou para
Firebird e cada um tem substituto direto:

| era | virou |
|---|---|
| `banco_de_dados.sql` | `src/db/connection.py` (`create_database_sql`) |
| `database_setup.sql` | `src/db/connection.py` (`modo_carga`, `modo_producao`) |
| `consulta_empresa_completa.sql` | `src/consulta/empresa.py` |

## Escrevendo SQL contra esta base

**`JOIN` com tabela de domínio tem que ser `LEFT JOIN`.** A base não tem
FOREIGN KEY de propósito, e existe código órfão real na fonte — `INNER JOIN`
faria a linha sumir por causa de um código que a Receita publicou errado.

**Firebird 3.0 não é PostgreSQL.** As diferenças que mais aparecem ao traduzir
consulta:

| PostgreSQL | Firebird 3.0 |
|---|---|
| `LIMIT 10 OFFSET 20` | `FIRST 10 SKIP 20` (logo após o `SELECT`) |
| `ILIKE` | nada — a collation `WIN_PTBR` já compara sem caixa e sem acento |
| `$1`, `$2` | `?` posicional |
| `string_agg` | `LIST` |
| `COALESCE` | igual |
| trigrama / `pg_trgm` | não existe; não há busca por trecho no meio do nome |

**A collation faz mais do que parece.** `WIN_PTBR` compara **sem acento e sem
caixa**: `WHERE razao_social = 'JOSE'` acha `José`, e `ORDER BY razao_social`
ordena certo em português. O dado gravado continua fiel à fonte — a
insensibilidade é da comparação, não do armazenamento.

**`capital_social` é `NUMERIC(18,2)`** e chega a 532.014.391.511,00. Não
converter para float ao comparar.
