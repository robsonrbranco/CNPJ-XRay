# CNPJ-XRay — Configuração inicial

## 1. Firebird 3.0 rodando

O projeto fala com um servidor Firebird 3.0 já instalado. Não há container nem
setup embutido.

**Windows** — o instalador cria os serviços `FirebirdServerDefaultInstance` e
`FirebirdGuardianDefaultInstance`:

```powershell
Get-Service *Firebird* | Select-Object Name, Status
```

**Linux**:

```bash
systemctl status firebird3.0
```

Testar a porta:

```bash
python -c "import socket; s=socket.create_connection(('localhost',3050),5); print('ok'); s.close()"
```

Firebird 3.0 é requisito, não preferência: o projeto usa `RDB$DB_KEY` para
remover chave repetida, `EXECUTE BLOCK` para carga em massa e o limite de 31
caracteres para identificador está codificado no schema.

## 2. Arquivo `.env`

```bash
cp .env.example .env
```

| variável | o que é | exemplo |
|---|---|---|
| `DB_HOST` | host do servidor Firebird | `localhost` |
| `DB_PORT` | porta | `3050` |
| `DB_NAME` | caminho do `.fdb` **como o servidor o enxerga** | `C:/CNPJ-XRay/bd/cnpj_xray.fdb` |
| `DB_USER` | usuário | `SYSDBA` |
| `DB_PASSWORD` | senha | — |
| `DB_CHARSET` | charset da conexão e do banco | `WIN1252` |
| `DB_COLLATION` | collation padrão | `WIN_PTBR` |
| `FB_CLIENT_LIBRARY` | caminho do `fbclient` | `C:/Program Files/Firebird/Firebird_3_0/fbclient.dll` |
| `FB_PAGE_SIZE` | tamanho de página | `16384` |
| `FB_CACHE_MB` | cache de páginas do banco, em MB | `2048` |
| `OUTPUT_FILES_PATH` | raiz dos `.zip` baixados | `C:/CNPJ-XRay/Download` |

Três detalhes que costumam morder:

- **`DB_NAME` é o caminho que o SERVIDOR vê**, não o cliente. Se o Firebird
  roda em container ou noutra máquina, use `FB_LOCAL_DATA_DIR` para dizer onde
  *este* processo enxerga o mesmo arquivo — a troca blue-green renomeia
  arquivos, e quem renomeia é o Python, não o servidor.
- **`DB_CHARSET` e `DB_COLLATION` só valem na criação do banco.** Mudar depois
  exige backup/restore com `gbak`. O mesmo vale para `FB_PAGE_SIZE`.
- **O usuário do Firebird não fica no banco da aplicação.** Ele vive no
  security database do servidor (`security3.fdb`, apontado por
  `SecurityDatabase` no `firebird.conf`). Trocar senha é operação de
  infraestrutura e vale para toda a instância.

## 3. Diretórios

```bash
mkdir -p Download bd logs
```

Espaço necessário: ~8 GB para os `.zip`, ~33 GB para a base nova, e a base
atual continua no disco até a troca terminar. Reserve ~45 GB livres.

## 4. Dependências

```bash
python -m venv .venv
```

```bash
.venv\Scripts\activate          # Windows;  no Linux: source .venv/bin/activate
python -m pip install -e .
```

Para rodar os testes, instale também o grupo de desenvolvimento:

```bash
python -m pip install pytest
```

## 5. Conferir a instalação

```bash
python -c "from dotenv import load_dotenv; load_dotenv(); from src.db import connection; print('banco existe:', connection.database_exists())"
```

`False` é o esperado antes da primeira carga — significa que o cliente falou
com o servidor e o arquivo ainda não existe. Erro de conexão ou de credencial
aparece como exceção.

## 6. Primeira carga

```bash
python -m src.etl.download
```

```bash
python -m src.etl.pipeline --origem ./Download --switch --processos 8
```

Cerca de 6 h em 8 processos. O pipeline valida antes de trocar: se a base nova
não fechar o número de linhas ou vier com chave repetida acima do limiar, nada
é promovido e a base anterior continua de pé.

## Problemas comuns

**`Your user name and password are not defined`** — o script não carregou o
`.env`. Os pontos de entrada (`src.etl.pipeline`, `src.etl.download`,
`src.consulta.empresa`) já chamam `load_dotenv()`; um script avulso precisa
chamar também.

**`Shared memory area is probably already created by another engine instance`**
— a Services API do driver atinge um engine local em vez do serviço. O projeto
já contorna isso caindo para o binário `gfix` (ver `src/db/gfix.py`); confirme
que o `gfix` está ao lado do `fbclient` configurado ou no `PATH`.

**`-104 Name longer than database column size`** — identificador acima de 31
caracteres. O `schema.py` checa isso na importação do módulo justamente para o
erro não aparecer só na hora do `CREATE TABLE`.

**`attempted update on read-only database`** — é o comportamento esperado: a
produção fica read-only no header. Para manutenção, use
`connection.modo_leitura_escrita()` e devolva com `modo_somente_leitura()`.
