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
RUN pip install --no-cache-dir \
      "firebird-driver>=2.0.0" "python-dotenv>=1.0.0" "rich>=13.0.0" \
      "polars>=1.42.1" "fastapi>=0.115.0" "uvicorn[standard]>=0.30.0"

COPY src/ ./src/

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
    DB_NAME=/data/cnpj_xray.fdb \
    DB_CHARSET=WIN1252 \
    DB_COLLATION=WIN_PTBR

# FB_EMBEDDED muda a forma do DSN. Sem ele o caminho sairia como
# `inet://localhost:3050/...`, o Dispatcher tentaria o provider Remote e
# falharia com "unavailable database" — erro que não menciona o caminho e
# manda procurar no lugar errado. Embedded exige caminho sem host.
ENV FB_EMBEDDED=1

EXPOSE 8000
# A porta do /manager (8001) NÃO é exposta de propósito: é a barreira que não
# depende de nenhum segredo estar certo. Quem precisar dela publica
# explicitamente, sabendo o que está fazendo.

CMD ["python", "-m", "src.api.servir", "--host", "0.0.0.0", "--porta", "8000"]
