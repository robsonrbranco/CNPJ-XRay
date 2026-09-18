# ETL

Do `.zip` publicado pela Receita até a base Firebird em produção.

## Módulos

| arquivo | papel |
|---|---|
| `download.py` | baixa uma competência do compartilhamento WebDAV da Receita |
| `leitura.py` | lê blocos de CSV direto de dentro do `.zip`, sem extrair |
| `carga.py` | mapeia arquivo → tabela e fatia o trabalho entre processos |
| `pipeline.py` | orquestra as 11 fases da construção |

## Download

```bash
python -m src.etl.download --listar
python -m src.etl.download --competencia 2026-09
python -m src.etl.download --conexoes 4 --aguardar 180
```

Multipart em faixas de 64 MB, com a fila de pedaços **global** — não uma
conexão por arquivo. Isso importa porque os arquivos são desiguais:
`Estabelecimentos0.zip` tem 2,24 GB e os de domínio têm 1 KB; por arquivo, o
fim do download viraria uma conexão só arrastando o gigante.

O servidor da Receita oscila: aceita a conexão, entrega alguns MB e para de
mandar byte **sem fechar o socket**. Por isso há timeout de socket, 6 tentativas
retomando de onde parou, e — quando a origem inteira cai — espera pela volta em
vez de gastar tentativa. Cada arquivo é conferido byte a byte contra o manifesto
WebDAV e tem o diretório central do zip lido.

Medido: a banda satura em ~7 MB/s a partir de 2 conexões; passar de 4 não compra
nada.

## Leitura

Os CSV são lidos em blocos de 32 MB de dentro do `.zip`, sem passar por disco.
Duas coisas que quebram parser ingênuo:

- **cp1252, não latin-1.** Os dois divergem em 0x80–0x9F. Os 5 bytes que cp1252
  não define são traduzidos para `?`.
- **`complemento` contém `;` entre aspas** (`"BLOCO: 01; APT: 144;"`). O corte
  de bloco respeita paridade de aspas, senão ~5% de `estabelecimento` sai com o
  número errado de campos.

Tudo sai como texto; a conversão de tipo é do `db.loader`, derivada do schema —
deixar o parser adivinhar levaria a decisões diferentes bloco a bloco.

## Pipeline

```bash
python -m src.etl.pipeline --origem ./Download --switch --processos 8
```

| flag | efeito |
|---|---|
| `--origem` | raiz dos `.zip`; apontando para a raiz, pega a competência mais recente |
| `--switch` | promove a base nova para produção ao final |
| `--continuar` | retoma pelo `checkpoint.json` |
| `--processos` | processos paralelos de carga |

As 11 fases: base nova → modo carga → tabelas sem trava → carga em N processos →
índices → remoção de chave repetida da fonte → estatísticas → modo produção →
validação → troca → read-only.

Paralelismo é por **processo**, não thread: as threads passam o tempo em Python
montando tuplas e disputam o GIL. Por processo, 4,7x.

Referência de tempo (2026-09, 8 processos, 222 M linhas): 310 min de carga mais
45 min de índices, dedup, estatísticas e troca.
