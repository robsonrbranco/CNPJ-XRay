# CLAUDE.md — CNPJ-XRay

Contexto para quem (ou o que) for trabalhar neste repositório.

## O que é

ETL e consulta dos dados públicos de CNPJ da Receita Federal em **Firebird
3.0**. Fork de `fonsecach/dados-publicos-cnpj`, que usava PostgreSQL; a
migração para Firebird foi a mudança central e o código PostgreSQL foi
removido, não mantido em paralelo.

## Quatro regras que sustentam o desenho

Elas se apoiam mutuamente. Mexer numa sem entender as outras quebra o conjunto.

1. **Firebird 3.0 é requisito fixo**, não preferência. Não propor 4.0/5.0 —
   mesmo a batch API do 4.0, que seria relevante para carga em massa, foi
   descartada explicitamente.
2. **Produção é base de CONSULTA.** Usuário nunca altera dado. O banco fica
   `read-only` no header — não é combinado operacional, é o engine recusando
   escrita.
3. **Nunca há atualização incremental.** Cada competência constrói uma base
   nova do zero em `_staging.fdb` e entra em produção pela troca de arquivo.
   `UPDATE OR INSERT` foi implementado e descartado por causa disso.
4. **A carga é otimista, sem nenhuma trava.** Sem PRIMARY KEY, NOT NULL ou
   FOREIGN KEY. Índices existem só para performance de consulta, nunca como
   constraint, e são criados depois da carga.

A razão da regra 4 é empírica: os dados da Receita têm código órfão e chave
repetida **reais** — `08314885` aparece duas vezes em `empresa` na fonte. Uma
constraint transformaria isso em exceção no meio de 222 milhões de INSERTs.
A conferência acontece depois, em `src/validation/qualidade.py`, medindo o
estrago em vez de abortar por causa dele.

## Mapa do código

| módulo | papel |
|---|---|
| `src/db/schema.py` | **fonte da verdade**: tabelas, larguras, índices, chaves naturais |
| `src/db/connection.py` | criação do banco, modos de carga/produção/read-only |
| `src/db/loader.py` | carga em massa com statement reaproveitado |
| `src/db/dedup.py` | remoção de chave repetida da fonte via `RDB$DB_KEY` |
| `src/db/gfix.py` | contorno para a Services API do driver |
| `src/etl/download.py` | download multipart, paralelo, com espera pela origem |
| `src/etl/leitura.py` | lê blocos direto do `.zip`, em cp1252 |
| `src/etl/carga.py` | fatia o trabalho entre processos |
| `src/etl/pipeline.py` | as 11 fases da construção |
| `src/blue_green/` | validação, troca de arquivo e modos de acesso |
| `src/consulta/empresa.py` | ficha de empresa por CNPJ |
| `src/api/publica.py` | as rotas do contrato do SERPRO, o `/saude` e o OpenAPI |
| `src/api/mcp.py` | a camada MCP para agentes (`POST /mcp`), com token de audiência própria — ver `docs/mcp.md` |

## Comandos

```bash
python -m src.etl.download --competencia 2026-09
```

```bash
python -m src.etl.pipeline --origem ./Download --switch --processos 8
```

```bash
python -m src.consulta.empresa 08314885
```

```bash
python -m src.validation.qualidade --producao
```

## Armadilhas já pagas

- **Encoding é cp1252, não latin-1.** Os dois divergem justamente em
  0x80–0x9F. Ler como latin-1 e gravar como WIN1252 quebra com
  `UnicodeEncodeError`. `leitura.py` lê cp1252 e traduz os 5 bytes que cp1252
  não define (`0x81 0x8D 0x8F 0x90 0x9D`) para `?`.
- **`complemento` contém `;` dentro de valor entre aspas.** Split ingênuo por
  `;` corrompe ~5% de `estabelecimento`.
- **O layout oficial não publica tamanho de campo nenhum.** As larguras foram
  medidas sobre dezenas de milhões de linhas reais.
- **Identificador tem limite de 31 caracteres** no Firebird 3.0. O
  `schema.py` checa na importação do módulo.
- **`EXECUTE BLOCK` tem limite de 256 contextos.** `INSERT` gasta 1 por
  instrução (256 por bloco), `UPDATE OR INSERT` gasta 3 (85 por bloco).
- **Paralelismo tem que ser por processo, não por thread** — as threads passam
  o tempo em Python e disputam o GIL. Por processo dá 4,7x.
- **A Services API do driver falha neste ambiente**; o projeto cai para o
  binário `gfix`.
- **`page_size` não muda o tamanho final da base.** Medido: 1,4 MB de
  diferença em 190 MB entre 4K, 8K e 16K, porque página de dado e página de
  índice reagem em direções opostas. Ver o comentário em `src/db/config.py`.
- **O servidor da Receita oscila muito** — cai e volta dezenas de vezes por
  hora, aceitando conexão e parando de entregar bytes sem fechar o socket.
  `download.py` trata isso; cliente ingênuo fica pendurado para sempre.

## Ao medir performance

Confirmar com um sinal que não venha do mesmo cronômetro, e checar se os dois
braços de uma comparação medem o mesmo escopo. Já houve quatro conclusões
erradas neste projeto por medir uma coisa e afirmar outra — tempo dentro de uma
chamada não é tempo gasto por ela, e probe num caminho não decide sobre outro.
