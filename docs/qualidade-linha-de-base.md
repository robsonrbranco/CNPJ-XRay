# Qualidade da base — linha de base

Retrato da competência **2026-09**, medido em 2026-09-17 sobre a base já em
produção, com `python -m src.validation.qualidade --producao`. O relatório levou
2 h 06 (7.595 s).

Isto existe para ser **comparado**. Os números abaixo não são meta nem limite:
são o que a Receita publicou nesta competência. Um salto em qualquer linha no
mês seguinte é sinal de mudança na fonte — layout novo, campo que passou a vir
vazio, domínio que perdeu códigos — e merece investigação antes de promover a
base.

## Base

| | |
|---|---|
| arquivo | 34,20 GB |
| page size | 16.384 |
| charset | WIN1252 / WIN_PTBR |
| atributos | `force write, no reserve, read only` |
| índices | 15 |

## Linhas

| tabela | linhas |
|---|---:|
| `estabelecimento` | 73.366.147 |
| `empresa` | 70.085.591 |
| `simples` | 50.396.768 |
| `socios` | 28.341.092 |
| `municipio` | 5.572 |
| `cnae` | 1.359 |
| `pais` | 255 |
| `natureza` | 91 |
| `qualificacao` | 68 |
| `motivo` | 63 |
| **total** | **222.197.006** |

## Chaves repetidas: zero

Todas as tabelas, zero — mas isso é resultado, não estado da fonte. A fonte
trouxe **uma** repetição de `cnpj_basico` em `empresa` (o CNPJ `08314885`, que
aparece duas vezes, uma delas sem dado nenhum), removida pela fase de dedup do
pipeline durante a carga. O relatório confere por caminho independente
(`GROUP BY ... HAVING COUNT(*) > 1` sobre a tabela inteira) e confirma que
sobrou zero.

**O que vigiar:** se numa competência o dedup remover muito mais que uma
handful de linhas, a fonte mudou de comportamento.

## Códigos órfãos

Código presente no fato que não existe na tabela de domínio.

| referência | órfãos | % |
|---|---:|---:|
| `estabelecimento.motivo_situacao_cadastral` → `motivo` | 18.253 | 0,025% |
| `socios.pais` → `pais` | 1.791 | 0,006% |
| `empresa.qualificacao_responsavel` → `qualificacao` | 1.608 | 0,002% |
| `estabelecimento.pais` → `pais` | 1.222 | 0,002% |
| `estabelecimento.municipio` → `municipio` | 0 | — |
| `estabelecimento.cnae_fiscal_principal` → `cnae` | 0 | — |
| `empresa.natureza_juridica` → `natureza` | 0 | — |
| `socios.qualificacao_socio` → `qualificacao` | 0 | — |
| `socios.qualificacao_repres_legal` → `qualificacao` | 0 | — |

`motivo_situacao_cadastral` responde por **80% de toda a inconsistência da
base** e é justamente um campo que a ficha de empresa exibe — por isso o
`LEFT JOIN` em `src/consulta/empresa.py` não é preciosismo.

## Integridade entre fato e empresa

| referência | sem correspondente | % |
|---|---:|---:|
| `estabelecimento` → `empresa` | 0 | — |
| `socios` → `empresa` | 0 | — |
| `simples` → `empresa` | 2 | 0,000% |

101 milhões de linhas de `estabelecimento` e `socios` sem um único órfão.

## Campos vazios onde a Receita promete valor

| campo | vazios |
|---|---:|
| `empresa.razao_social` | 4 |
| `empresa.cnpj_basico` | 0 |
| `estabelecimento.cnpj_basico` | 0 |
| `estabelecimento.situacao_cadastral` | 0 |
| `socios.cnpj_basico` | 0 |
| `simples.cnpj_basico` | 0 |

## Leitura

**22.880 linhas com alguma inconsistência em 222.197.006 — 0,0103%.**

O dado da Receita é muito melhor do que a decisão de carregar sem travas faria
supor. A decisão continua certa, e este relatório mostra por quê: sob FOREIGN
KEY essas 22.880 linhas teriam abortado a carga — na prática, uma vez só, na
primeira delas, depois de horas de INSERT. Carregar tudo e medir depois custa
um relatório; carregar com trava custa a carga inteira.

## Como reproduzir

```bash
uv run python -m src.validation.qualidade --producao
```

Se virar rotina mensal, vale rodar **logo depois da carga**, não dias depois: a
fase de dedup do pipeline faz o `GROUP BY` mais caro em ~23 min com Force Write
em ASYNC e o cache quente da carga, contra as 2 h 06 deste relatório em base
fria e read-only.
