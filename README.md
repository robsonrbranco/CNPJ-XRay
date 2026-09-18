# 🔎 CNPJ-XRay

ETL e consulta dos dados públicos de CNPJ da Receita Federal do Brasil, em
**Firebird 3.0**.

São 37 arquivos publicados mensalmente pela Receita, ~7,8 GB compactados e
~222 milhões de linhas, que viram uma base Firebird de ~33 GB pronta para
consulta.

## Como funciona

A base de produção é **somente leitura** e nunca é alterada no lugar. Toda
competência nova constrói uma base do zero num arquivo de staging e só entra em
produção pela troca do arquivo (blue-green). Isso torna a atualização atômica e
reversível até o último passo, e permite carregar sem nenhuma trava — sem
PRIMARY KEY, NOT NULL ou FOREIGN KEY.

Não é descuido: os dados da Receita chegam com código órfão e chave repetida
reais, e uma constraint transformaria cada ocorrência numa exceção no meio de
222 milhões de INSERTs. Os índices existem **só** para performance de consulta,
e são criados depois da carga. O relatório de
[`src/validation/qualidade.py`](src/validation/qualidade.py) mede o estrago
depois, sobre a base pronta.

## Estrutura

```
src/
  db/          camada Firebird: schema, conexão, carga, índices, dedup
  etl/         download multipart, leitura dos .zip e pipeline de construção
  blue_green/  troca de arquivo, validação e modos de acesso
  validation/  relatório de qualidade da base
  consulta/    ficha de empresa por CNPJ
  sql/         consulta de referência contra a Base dos Dados (BigQuery)
```

## Pré-requisitos

- Python 3.13
- Firebird 3.0 rodando (o `fbclient.dll`/`libfbclient.so` precisa estar
  acessível ao cliente)
- ~45 GB livres: ~8 GB de `.zip`, ~33 GB da base nova, mais a base atual
  durante a troca

## Instalação

```bash
git clone https://github.com/robsonrbranco/CNPJ-XRay.git
cd CNPJ-XRay
python -m venv .venv
```

```bash
.venv\Scripts\activate          # Windows;  no Linux: source .venv/bin/activate
python -m pip install -e .
cp .env.example .env
```

Ver [CONFIGURACAO_INICIAL.md](CONFIGURACAO_INICIAL.md) para o `.env`.

## Uso

### Baixar uma competência

```bash
python -m src.etl.download --listar
```

```bash
python -m src.etl.download --competencia 2026-09
```

Sem `--competencia` ele pega a mais recente publicada. O download é multipart
com paralelismo por pedaço, retomável, e confere cada arquivo byte a byte contra
o manifesto WebDAV. O servidor da Receita oscila muito — ele espera a origem
voltar em vez de falhar.

### Construir a base

```bash
python -m src.etl.pipeline --origem ./Download --switch --processos 8
```

Onze fases: base nova → modo carga → tabelas sem trava → carga em N processos →
índices → remoção de chave repetida da fonte → estatísticas → modo produção →
validação → troca → read-only. Sem `--switch` a base fica em staging, sem
promover. Com `--continuar` retoma de onde parou.

Leva cerca de 6 h em 8 processos.

### Consultar

```bash
python -m src.consulta.empresa 08314885
```

```bash
python -m src.consulta.empresa 08.314.885/0001-05 --json
```

### Relatório de qualidade

```bash
python -m src.validation.qualidade --producao
```

## Consulta SQL direta

`JOIN` com tabela de domínio tem que ser `LEFT JOIN` — existe código órfão real
na fonte, e `INNER JOIN` faria a linha sumir por causa de um código que a
Receita publicou errado.

```sql
SELECT e.razao_social, est.nome_fantasia, est.situacao_cadastral,
       cn.descricao AS atividade_principal, mu.descricao AS municipio, est.uf
FROM empresa e
JOIN estabelecimento est ON e.cnpj_basico = est.cnpj_basico
LEFT JOIN cnae cn ON est.cnae_fiscal_principal = cn.codigo
LEFT JOIN municipio mu ON est.municipio = mu.codigo
WHERE e.cnpj_basico = '08314885' AND est.cnpj_ordem = '0001';
```

A base usa `WIN1252` com collation `WIN_PTBR`, que compara **sem acento e sem
caixa** — `ORDER BY razao_social` ordena certo, e `WHERE razao_social = 'JOSE'`
acha `José`. O dado gravado continua fiel à fonte.

## Dados

| tabela | linhas (2026-09) |
|---|---:|
| `estabelecimento` | 73.366.147 |
| `empresa` | 70.085.591 |
| `simples` | 50.396.768 |
| `socios` | 28.341.092 |

Mais as tabelas de domínio: `cnae`, `municipio`, `natureza`, `pais`,
`qualificacao`, `motivo`.

## Backup

Não há ferramenta própria: a base é reconstruída do zero a partir dos `.zip`
todo mês, então a fonte **é** o backup. Para mover a base entre máquinas ou
mudar o `page_size` (que só pode ser definido na criação), use o `gbak` do
próprio Firebird.

## Origem dos dados

- Arquivos: `https://arquivos.receitafederal.gov.br/public.php/dav/files/YggdBLfdninEJX9/`
- Catálogo: https://dados.gov.br/dados/conjuntos-dados/cadastro-nacional-da-pessoa-juridica---cnpj
- Layout: https://www.gov.br/receitafederal/dados/cnpj-metadados.pdf

O layout oficial descreve os campos mas **não publica o tamanho de nenhum**. As
larguras em [`src/db/schema.py`](src/db/schema.py) foram medidas sobre dezenas
de milhões de linhas reais.

## Origem do projeto

Fork de [fonsecach/dados-publicos-cnpj](https://github.com/fonsecach/dados-publicos-cnpj),
por sua vez fork de
[aphonsoar/Receita_Federal_do_Brasil_-_Dados_Publicos_CNPJ](https://github.com/aphonsoar/Receita_Federal_do_Brasil_-_Dados_Publicos_CNPJ).
A mudança principal foi trocar o PostgreSQL pelo Firebird 3.0 e reescrever o
ETL em torno disso.
