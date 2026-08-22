"""Carga das tabelas CNPJ na base nova.

Substitui as cinco funções `process_*` que a versão PostgreSQL deste ETL tinha
(empresa, estabelecimento, socios, simples e "outros"). Elas eram praticamente
iguais entre si — mudava a lista de colunas e quais converter — e essa
repetição sumiu quando a conversão passou a ser derivada de db.schema por
db.loader.normalizar. Sobrou uma função só, dirigida pelo schema.

A carga roda em PROCESSOS paralelos, e o motivo está no perfil.

Perfilando a carga de um bloco de 60 mil linhas de `estabelecimento`:

    normalizar (Polars) .............   0,04s    0,1%
    montar tuplas ...................   0,09s    0,3%
    cur.execute .....................  32,08s   95,4%

E dentro do `cur.execute`, o banco quase não aparece: a chamada ao servidor
(`interfaces.py:805`) leva 0,687s enquanto o `_pack_input` do driver leva
11,78s. São ~2,1 milhões de chamadas a `_check`/`get_state` por bloco de 40 —
cerca de 7 por parâmetro, cada uma fazendo `__contains__` de enum. O
empacotamento de parâmetros em Python custa ~17x o trabalho do banco.

Durante uma carga real: Python a 71% de um núcleo, serviço do Firebird a 0%,
sete núcleos ociosos. O gargalo é o cliente, não o engine — então processos
paralelos (que fogem do GIL) escalam. Medido, mesma tabela, arquivos
diferentes:

    1 processo ...  6.347 linhas/s   1,00x
    2 processos .. 11.859 linhas/s   1,87x
    4 processos .. 21.207 linhas/s   3,34x
    6 processos .. 27.139 linhas/s   4,28x
    8 processos .. 29.989 linhas/s   4,72x

PROCESSOS, não threads: uma medição anterior com `threading.Thread` deu 0,31x
com 8 threads e me levou a concluir, errado, que "paralelismo piora em
Firebird". Aquilo era contenção de GIL entre threads que passam o tempo em
Python — não do banco. Com processos separados o efeito some.

O paralelismo é por BLOCO dentro do arquivo, não por arquivo: os arquivos da
Receita são muito desiguais (Estabelecimentos0.zip tem 2,2 GB contra ~340 MB
dos demais), e dividir por arquivo deixaria o pool inteiro esperando o maior.
Cada worker abre o mesmo arquivo e processa um bloco a cada `n_fatias` — o
custo é descomprimir o arquivo em duplicidade, mas ler e parsear é ~0,6% do
tempo, então a redundância é ruído perto do balanceamento que ela compra.
"""

import logging
import re
import time
from collections.abc import Iterable
from pathlib import Path

from src.db import loader, schema
from src.db.config import FirebirdConfig
from src.etl import leitura

logger = logging.getLogger(__name__)

# Trecho do nome do arquivo -> tabela de destino. Cobre tanto o nome do .zip
# publicado pela Receita (Empresas0.zip) quanto o do arquivo extraído de dentro
# dele (K3241.K03200Y3.D60808.EMPRECSV).
PADROES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("EMPRECSV", "EMPRESAS"), "empresa"),
    (("ESTABELE", "ESTABELECIMENTOS"), "estabelecimento"),
    (("SOCIOCSV", "SOCIOS"), "socios"),
    (("SIMPLES",), "simples"),
    (("CNAECSV", "CNAES"), "cnae"),
    (("MOTICSV", "MOTIVOS"), "motivo"),
    (("MUNICCSV", "MUNICIPIOS"), "municipio"),
    (("NATJUCSV", "NATUREZAS"), "natureza"),
    (("PAISCSV", "PAISES"), "pais"),
    (("QUALSCSV", "QUALIFICACOES"), "qualificacao"),
)

# Ordem de carga. As tabelas de domínio vêm primeiro porque são pequenas e
# rápidas: se algo estiver errado no ambiente, o erro aparece em segundos em
# vez de horas. `estabelecimento` fica por último por ser de longe a mais cara.
ORDEM = (
    "cnae", "motivo", "municipio", "natureza", "pais", "qualificacao",
    "empresa", "simples", "socios", "estabelecimento",
)


# Nome de pasta de competência publicada pela Receita: AAAA-MM.
COMPETENCIA = re.compile(r"^\d{4}-\d{2}$")


def resolver_origem(caminho: Path) -> tuple[Path, str | None]:
    """Resolve o diretório de dados brutos e a competência correspondente.

    A Receita publica uma versão nova todo mês, e o layout em disco é uma pasta
    por competência (`Download/2026-08`, `Download/2026-09`, ...). Fixar o
    caminho da competência no .env obrigaria a editá-lo todo mês, então:

    * apontar para a raiz (`Download`) escolhe a competência mais recente;
    * apontar direto para uma competência (`Download/2026-08`) usa aquela.

    Devolve (diretório, competência) — competência é None quando o diretório
    não segue o padrão AAAA-MM.
    """
    caminho = Path(caminho)

    if COMPETENCIA.match(caminho.name):
        return caminho, caminho.name

    subpastas = sorted(
        (p for p in caminho.iterdir() if p.is_dir() and COMPETENCIA.match(p.name)),
        key=lambda p: p.name,
    )
    if subpastas:
        escolhida = subpastas[-1]
        if len(subpastas) > 1:
            logger.info(
                "Competências disponíveis: %s — usando a mais recente (%s)",
                ", ".join(p.name for p in subpastas), escolhida.name,
            )
        return escolhida, escolhida.name

    # Sem subpasta de competência: os arquivos estão soltos no próprio diretório.
    return caminho, None


def tabela_do_arquivo(nome: str) -> str | None:
    """Descobre a tabela de destino pelo nome do arquivo."""
    alvo = nome.upper()
    for trechos, tabela in PADROES:
        if any(t in alvo for t in trechos):
            return tabela
    return None


def mapear_arquivos(diretorio: Path) -> dict[str, list[Path]]:
    """Agrupa por tabela os arquivos de `diretorio`.

    Aceita o diretório dos .zip baixados ou o dos CSV já extraídos.
    """
    por_tabela: dict[str, list[Path]] = {t: [] for t in schema.TABLES}
    ignorados: list[str] = []

    for item in sorted(Path(diretorio).iterdir()):
        if item.is_dir():
            continue
        tabela = tabela_do_arquivo(item.name)
        if tabela is None:
            ignorados.append(item.name)
            continue
        por_tabela[tabela].append(item)

    if ignorados:
        logger.debug("Arquivos ignorados em %s: %s", diretorio, ", ".join(ignorados))
    return por_tabela


def carregar_tabela(
    con,
    tabela: str,
    arquivos: Iterable[Path],
    cfg: FirebirdConfig,
    ao_concluir_arquivo=None,
) -> int:
    """Carrega todos os arquivos de uma tabela. Devolve o total de linhas."""
    arquivos = list(arquivos)
    if not arquivos:
        logger.warning("Nenhum arquivo para a tabela '%s'", tabela)
        return 0

    colunas = schema.column_names(tabela)
    total = 0
    inicio = time.time()

    for i, arquivo in enumerate(arquivos):
        t_arquivo = time.time()
        linhas_arquivo = 0
        for bloco in leitura.blocos_csv(arquivo, colunas):
            linhas_arquivo += loader.carregar(con, bloco, tabela, cfg)
        total += linhas_arquivo

        dt = max(time.time() - t_arquivo, 1e-6)
        logger.info(
            "%s [%d/%d] %s: %s linhas em %.1fs (%s linhas/s)",
            tabela, i + 1, len(arquivos), arquivo.name,
            f"{linhas_arquivo:,}", dt, f"{linhas_arquivo / dt:,.0f}",
        )
        if ao_concluir_arquivo:
            ao_concluir_arquivo(tabela, i + 1, arquivo)

    dt = max(time.time() - inicio, 1e-6)
    logger.info(
        "%s concluída: %s linhas em %.1fs (%s linhas/s)",
        tabela, f"{total:,}", dt, f"{total / dt:,.0f}",
    )
    return total


def carregar_tudo(
    con,
    por_tabela: dict[str, list[Path]],
    cfg: FirebirdConfig,
    pular: set[str] | None = None,
    ao_concluir_arquivo=None,
    ao_concluir_tabela=None,
) -> dict[str, int]:
    """Carrega todas as tabelas na ordem de ORDEM, numa conexão só.

    Caminho sequencial, mantido para carga pequena e para depuração — a carga
    completa usa carregar_tudo_paralelo().
    """
    pular = pular or set()
    contagens: dict[str, int] = {}

    for tabela in ORDEM:
        if tabela in pular:
            logger.info("Tabela '%s' já carregada (checkpoint) — pulando", tabela)
            continue
        contagens[tabela] = carregar_tabela(
            con, tabela, por_tabela.get(tabela, []), cfg, ao_concluir_arquivo
        )
        if ao_concluir_tabela:
            ao_concluir_tabela(tabela, contagens[tabela])

    return contagens


# ---------------------------------------------------------------------------
# Carga paralela
# ---------------------------------------------------------------------------

def _worker(tarefa: tuple[str, str, int, int, str]) -> tuple[str, str, int, int, float]:
    """Carrega uma fatia de um arquivo. Roda em processo próprio.

    Cada processo abre a própria conexão: conexão do Firebird não atravessa
    fronteira de processo, e é justamente ter uma por processo que faz o
    paralelismo valer.

    O restante da configuração vem do ambiente, que o filho herda do pai — mas
    o CAMINHO DO BANCO viaja na tarefa, e não pode sair de `load_config()`.
    `DB_NAME` no ambiente aponta para a produção; a carga acontece na base
    nova. Ler do ambiente aqui faria todo worker escrever no banco errado.
    """
    tabela, arquivo, fatia, n_fatias, database = tarefa

    # Import tardio: com 'spawn' (padrão no Windows) o filho reimporta o
    # módulo, e importar o mundo no topo encareceria cada processo.
    from src.db import connection, loader, schema
    from src.db.config import FirebirdConfig, load_config

    cfg = FirebirdConfig(**{**load_config().__dict__, "database": database})
    colunas = schema.column_names(tabela)
    inicio = time.time()
    total = 0

    with connection.conectar(cfg) as con:
        for i, bloco in enumerate(leitura.blocos_csv(Path(arquivo), colunas)):
            # Este worker fica só com um bloco a cada n_fatias. Ler os demais
            # custa a descompressão, que é ruído perto do tempo de carga.
            if i % n_fatias != fatia:
                continue
            total += loader.carregar(con, bloco, tabela, cfg)

    return tabela, arquivo, fatia, total, time.time() - inicio


def montar_tarefas(
    por_tabela: dict[str, list[Path]],
    n_fatias: int,
    database: str,
    pular: set[str] | None = None,
) -> list[tuple[str, str, int, int, str]]:
    """Monta a lista de (tabela, arquivo, fatia, n_fatias, database).

    Na ordem de ORDEM, para que domínio e tabelas baratas terminem cedo e um
    erro de ambiente apareça em segundos em vez de horas.
    """
    pular = pular or set()
    tarefas: list[tuple[str, str, int, int, str]] = []
    for tabela in ORDEM:
        if tabela in pular:
            continue
        for arquivo in por_tabela.get(tabela, []):
            for fatia in range(n_fatias):
                tarefas.append((tabela, str(arquivo), fatia, n_fatias, database))
    return tarefas


def carregar_tudo_paralelo(
    por_tabela: dict[str, list[Path]],
    cfg: FirebirdConfig,
    processos: int,
    pular: set[str] | None = None,
    ao_concluir_fatia=None,
) -> dict[str, int]:
    """Carrega todas as tabelas com `processos` workers em paralelo.

    A conexão do pai NÃO é usada aqui: as tabelas já precisam existir antes
    (manage.criar_tabelas), e os índices são criados depois, pelo pai.
    """
    import multiprocessing as mp

    tarefas = montar_tarefas(por_tabela, processos, cfg.database, pular)
    contagens: dict[str, int] = {}
    inicio = time.time()

    # Uma tabela só está pronta quando TODAS as suas fatias voltaram — as
    # tarefas terminam fora de ordem, então o checkpoint não pode marcar a
    # tabela na primeira que chega.
    pendentes: dict[str, int] = {}
    for tabela, _, _, _, _ in tarefas:
        pendentes[tabela] = pendentes.get(tabela, 0) + 1

    logger.info(
        "Carga paralela: %d tarefas em %d processos", len(tarefas), processos
    )

    with mp.Pool(processos) as pool:
        for tabela, arquivo, fatia, linhas, dt in pool.imap_unordered(_worker, tarefas):
            contagens[tabela] = contagens.get(tabela, 0) + linhas
            pendentes[tabela] -= 1
            logger.info(
                "%s %s [fatia %d/%d]: %s linhas em %.1fs (%s linhas/s)",
                tabela, Path(arquivo).name, fatia + 1, processos,
                f"{linhas:,}", dt, f"{linhas / max(dt, 1e-6):,.0f}",
            )
            if pendentes[tabela] == 0 and ao_concluir_fatia:
                ao_concluir_fatia(tabela, contagens[tabela])

    dt = max(time.time() - inicio, 1e-6)
    total = sum(contagens.values())
    logger.info(
        "Carga concluída: %s linhas em %.1f min (%s linhas/s)",
        f"{total:,}", dt / 60, f"{total / dt:,.0f}",
    )
    return contagens
