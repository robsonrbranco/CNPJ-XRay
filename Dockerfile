# Pod de consulta: Firebird 3 embedded + a API.
#
# Duas etapas. A primeira instala o Firebird inteiro só para extrair o engine;
# a segunda leva apenas o que roda. O `firebird3.0-server` traz o executável do
# servidor, o security database e as ferramentas de linha de comando, e nada
# disso vai para a imagem final: o pod não escuta na 3050, não autentica e não
# faz manutenção de banco.
#
# O que de fato é necessário, medido contra a documentação da IBPhoenix e
# confirmado em execução:
#
#   libfbclient.so.2          cliente
#   plugins/libEngine12.so    o engine, que em embedded roda no processo da app
#   firebird.conf             Providers = Engine12, ServerMode = Classic
#
# `ServerMode = Classic` não é detalhe: com `Super` o primeiro worker toma o
# arquivo e os demais morrem no attach. Medido, 1 de 4 contra 4 de 4.

FROM python:3.13-slim-bookworm AS engine

RUN apt-get update -qq \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends \
      firebird3.0-server \
 && rm -rf /var/lib/apt/lists/*


# A página de apresentação em https://themis.ecomciencia.com/.
#
# Compilada aqui e servida pela própria aplicação, em vez de por um pod nginx
# à parte: a página documenta a API — tabela de endpoints, exemplo de
# autenticação, as duas diferenças em relação ao SERPRO — e versionar as duas
# juntas é o que impede a página de descrever uma API que já mudou.
#
# O custo é conhecido: `strategy: Recreate` no manifest, então corrigir uma
# vírgula no texto reinicia o pod que serve a API. São segundos, e a alternativa
# custaria uma segunda imagem no containerd, que divide disco com a base.
FROM debian:bookworm-slim AS site

RUN apt-get update -qq \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends \
      wget ca-certificates \
 && HUGO_VERSION=$(wget -qO- https://api.github.com/repos/gohugoio/hugo/releases/latest \
      | grep -oP '"tag_name": "v\K[^"]+') \
 && wget -qO /tmp/hugo.tar.gz \
      "https://github.com/gohugoio/hugo/releases/download/v${HUGO_VERSION}/hugo_extended_${HUGO_VERSION}_linux-amd64.tar.gz" \
 && tar -xzf /tmp/hugo.tar.gz -C /usr/local/bin hugo \
 && rm -rf /var/lib/apt/lists/* /tmp/hugo.tar.gz

WORKDIR /src
COPY site/ ./
RUN hugo --minify


FROM python:3.13-slim-bookworm

# Bibliotecas de que o engine depende. Sem elas o libEngine12.so carrega e
# falha no dlopen, com erro que não diz qual símbolo faltou.
RUN apt-get update -qq \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends \
      libtommath1 libicu72 libncurses6 libatomic1 \
 && rm -rf /var/lib/apt/lists/*

COPY --from=engine /usr/lib/x86_64-linux-gnu/libfbclient.so.2* /usr/lib/x86_64-linux-gnu/

# A raiz do Firebird INTEIRA, não arquivo a arquivo.
#
# Custa ~9 MB e evita uma classe de erro difícil de diagnosticar: o engine
# resolve `intl/`, `firebird.msg` e os `.conf` a partir de `$(root)`, e cada
# peça faltando produz uma mensagem que aponta para outro lugar. Copiar só o
# `libEngine12.so` dá "CHARACTER SET WIN1252 is not defined", que não menciona
# arquivo nenhum; faltar o `firebird.msg` transforma qualquer erro seguinte num
# código numérico sem texto.
#
# O que pesa de verdade -- o executável do servidor, o security3.fdb, gbak,
# gfix e isql -- continua fora: nada disso está sob esta raiz.
COPY --from=engine /usr/lib/x86_64-linux-gnu/firebird/3.0/                    /usr/lib/x86_64-linux-gnu/firebird/3.0/
COPY --from=engine /etc/firebird/3.0/ /etc/firebird/3.0/
COPY --from=engine /usr/share/firebird/ /usr/share/firebird/

RUN ldconfig

COPY docker/firebird.conf /etc/firebird/3.0/firebird.conf

# O engine grava arquivos de lock mesmo com a base read-only. Num pod com raiz
# somente leitura isto precisa ser um tmpfs.
RUN mkdir -p /tmp/firebird && chmod 1777 /tmp/firebird

WORKDIR /app
COPY pyproject.toml ./
# MENOS pacotes do que o `pyproject.toml` declara, e isso e deliberado.
#
# O pod serve consulta e **nunca roda o ETL**. `polars` e `rich` sao do ETL e
# da CLI, e nao entram. Medido dentro da imagem anterior:
#
#     _polars_runtime_32   172 MB
#     polars                 9 MB
#     pygments               9 MB   (dependencia do rich)
#     rich                   3 MB
#     ---------------------------
#                          193 MB de site-packages
#
# Duas mudancas de CODIGO tiveram de vir antes, e sem elas isto nao funciona:
#
#   * `src/db/__init__.py` reexportava `.loader`, que importa `polars`. Como o
#     `__init__` roda no primeiro import de qualquer submodulo, um inocente
#     `from ..db import connection` arrastava os 181 MB;
#   * `src/consulta/empresa.py` importava `rich` no topo para a saida da CLI, e
#     a API importa esse modulo por causa de `consultar()`. Virou preguicoso.
#
# `tests/test_imagem_enxuta.py` trava as duas, rodando com os pacotes
# INSTALADOS e reprovando se a superficie do pod encostar neles -- porque em
# desenvolvimento o import passaria e o erro so apareceria ao subir o pod.
#
# `uvloop` (16 MB) FICA: ele troca o event loop do asyncio e e o pod usando.
#
# O `pip` sai depois de instalar. Um container de producao nao tem por que
# carregar um instalador de pacotes.
#
# Nao se apaga `__pycache__` aqui: o pod tem `strategy: Recreate`, entao cada
# deploy paga o start inteiro com a readiness esperando, e sem o .pyc cada
# import recompila. Trocar disco por latencia de subida e o lado errado da
# troca -- mas fica a nota de que foi considerado.
RUN pip install --no-cache-dir \
      "firebird-driver>=2.0.0" "python-dotenv>=1.0.0" \
      "fastapi>=0.115.0" "uvicorn[standard]>=0.30.0" \
 && pip uninstall -y pip setuptools wheel

COPY src/ ./src/
COPY --from=site /src/public/ ./site/

# FIREBIRD é a RAIZ do engine, não o diretório de configuração. Apontá-lo para
# /etc/firebird/3.0 faz o engine procurar `intl/` e `firebird.msg` lá dentro,
# não achar, e falhar com "CHARACTER SET WIN1252 is not defined" — mensagem que
# não menciona caminho nenhum.
ENV FB_CLIENT_LIBRARY=/usr/lib/x86_64-linux-gnu/libfbclient.so.2 \
    FIREBIRD=/usr/lib/x86_64-linux-gnu/firebird/3.0 \
    PYTHONUNBUFFERED=1

# /data   base CNPJ, montada read-only, trocada todo mês
# /creds  credenciais e estatísticas, ciclo próprio
# /logs   log analítico, mantido indefinidamente
VOLUME ["/data", "/creds", "/logs"]

ENV API_CREDENCIAIS_DIR=/creds \
    API_LOGS_DIR=/logs \
    API_SITE_DIR=/app/site \
    DB_NAME=/data/cnpj_xray.fdb \
    DB_CHARSET=WIN1252 \
    DB_COLLATION=WIN_PTBR

# FB_EMBEDDED muda a forma do DSN. Sem ele o caminho sairia como
# `inet://localhost:3050/...`, o Dispatcher tentaria o provider Remote e
# falharia com "unavailable database" — erro que não menciona o caminho e
# manda procurar no lugar errado. Embedded exige caminho sem host.
ENV FB_EMBEDDED=1

# A URI canônica do endpoint MCP, que é também a AUDIÊNCIA dos tokens dele.
# Aqui e não no manifest: os dois containers do pod (API e /manager) precisam
# concordar com ela byte a byte — um emite, o outro valida —, e a mesma imagem
# garante isso sem duas cópias para divergir. Ver src/api/mcp.py.
ENV API_MCP_URI=https://themis.ecomciencia.com/mcp

EXPOSE 8000
# A porta do /manager (8001) NÃO é exposta de propósito: é a barreira que não
# depende de nenhum segredo estar certo. Quem precisar dela publica
# explicitamente, sabendo o que está fazendo.

CMD ["python", "-m", "src.api.servir", "--host", "0.0.0.0", "--porta", "8000"]
