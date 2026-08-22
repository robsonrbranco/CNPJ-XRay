"""Carga das tabelas CNPJ na base nova.

Substitui as cinco funções `process_*` que a versão PostgreSQL deste ETL tinha
(empresa, estabelecimento, socios, simples e "outros"). Elas eram praticamente
iguais entre si — mudava a lista de colunas e quais converter — e essa
repetição sumiu quando a conversão passou a ser derivada de db.schema por
db.loader.normalizar. Sobrou uma função só, dirigida pelo schema.

A carga é sequencial de propósito. Medido contra o Firebird 3.0.14, dividir o
mesmo volume entre conexões concorrentes na mesma tabela derruba a taxa: 2
conexões 0,75x, 4 conexões 0,34x, 8 conexões 0,31x. Uma conexão só é o
caminho rápido em Firebird.
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
    """Carrega todas as tabelas na ordem definida em ORDEM.

    `pular` são tabelas que um checkpoint indica já carregadas.
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
