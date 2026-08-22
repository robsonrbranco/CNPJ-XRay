"""Carga em massa para Firebird 3.0.

Estratégia e por quê
--------------------
A carga usa `EXECUTE BLOCK` com N INSERTs por bloco, um statement preparado uma
única vez, dentro de transações longas, com o banco em Force Write ASYNC (ver
connection.modo_carga) e sem nenhum índice ativo.

As alternativas foram medidas contra o servidor real (Firebird 3.0.14, página
de 16 KB, cache de 2 GB, Force Write OFF, 200 mil linhas de `empresa`):

    executemany com SQL string ......  3.526 linhas/s
    executemany com Statement ......   3.550 linhas/s
    EXECUTE BLOCK,  64 inserts .....   7.185 linhas/s
    EXECUTE BLOCK, 128 inserts .....   7.508 linhas/s
    EXECUTE BLOCK, 256 inserts .....   7.572 linhas/s   <- adotado

O Firebird 3.0 não tem a batch API do 4.0, então `executemany` é um laço
client-side: uma ida ao servidor por linha. O EXECUTE BLOCK amortiza isso em um
round-trip por bloco e dobra a taxa. Acima de 256 INSERTs o ganho satura, e o
texto do statement começa a esbarrar no limite de tamanho do Firebird.

Duas medições que contrariam a intuição e definem o desenho deste módulo:

* Preparar o statement, sozinho, não muda nada (3.526 -> 3.550). O gargalo não
  é a preparação.
* O tempo é 99% servidor: numa carga de 200 mil linhas, montar as tuplas em
  Python levou 0,2 s e o `execute` levou 26,4 s. Otimizar o lado cliente não
  paga.

E a mais importante: **paralelismo piora**. Com o mesmo volume dividido entre
várias conexões concorrentes na mesma tabela:

    1 conexão ....  7.324 linhas/s   1,00x
    2 conexões ...  5.507 linhas/s   0,75x
    4 conexões ...  2.521 linhas/s   0,34x
    8 conexões ...  2.294 linhas/s   0,31x

É contenção do SuperServer na mesma tabela. Por isso a carga aqui é
deliberadamente sequencial — a versão PostgreSQL deste ETL usava 10 conexões
concorrentes, o que em Firebird seria o pior caminho possível.
"""

import logging
import sys
import time
from decimal import Decimal, InvalidOperation

import polars as pl

from . import schema
from .config import FirebirdConfig, load_config

logger = logging.getLogger(__name__)

# INSERTs por EXECUTE BLOCK.
#
# O teto não é escolha de tuning: é o limite de CONTEXTOS do engine, que
# aparece como "Too many Contexts of Relation/Procedure/Views. Maximum allowed
# is 256". Um INSERT consome 1 contexto, então cabem 256 por bloco — medido
# por bisseção contra o Firebird 3.0.14. Mexer nesse número sem medir de novo
# quebra a preparação do statement.
INSERTS_POR_BLOCO = 256


class TruncationReport:
    """Conta valores que estouraram o VARCHAR, por coluna.

    Truncar e avisar é deliberado: abortar uma carga de dezenas de milhões de
    linhas por causa de um nome fora do tamanho medido seria pior. O relatório
    sai no log ao fim de cada tabela para que a largura seja corrigida no
    schema na próxima carga.
    """

    def __init__(self):
        self.por_coluna: dict[str, int] = {}

    def registrar(self, coluna: str, n: int) -> None:
        if n:
            self.por_coluna[coluna] = self.por_coluna.get(coluna, 0) + n

    def __bool__(self) -> bool:
        return bool(self.por_coluna)

    def log(self, tabela: str) -> None:
        for coluna, n in sorted(self.por_coluna.items(), key=lambda kv: -kv[1]):
            logger.warning(
                "%s.%s: %d valores truncados — aumente a largura em db/schema.py",
                tabela, coluna, n,
            )


def normalizar(
    df: pl.DataFrame, tabela: str, report: TruncationReport | None = None
) -> pl.DataFrame:
    """Converte um DataFrame de strings cruas nos tipos do schema.

    Espera o CSV lido com todas as colunas como Utf8 — é assim que o ETL lê,
    para não deixar o parser adivinhar tipo errado.
    """
    report = report if report is not None else TruncationReport()
    exprs = []

    for col in schema.columns(tabela):
        nome = col.name
        if nome not in df.columns:
            continue
        e = pl.col(nome)

        if col.kind == schema.TEXT:
            e = e.str.strip_chars()
            # "" vira NULL: a RFB usa campo vazio como ausência de valor.
            e = pl.when(e.str.len_chars() == 0).then(None).otherwise(e)
            if col.max_len:
                excedentes = (
                    df.select(
                        pl.col(nome).str.strip_chars().str.len_chars() > col.max_len
                    ).to_series().sum()
                )
                report.registrar(nome, int(excedentes or 0))
                e = e.str.slice(0, col.max_len)

        elif col.kind == schema.INT:
            # strict=False: valor inválido/vazio vira NULL em vez de exceção.
            e = e.str.strip_chars().cast(pl.Int64, strict=False)

        elif col.kind == schema.DATE:
            # A RFB grava AAAAMMDD; "0", "" e "00000000" não casam e viram NULL.
            e = e.str.strip_chars().str.strptime(pl.Date, format="%Y%m%d", strict=False)

        elif col.kind == schema.NUMERIC:
            # Vírgula decimal -> ponto. Fica texto aqui e vira Decimal na
            # geração das tuplas, para não passar por float e perder centavo.
            e = e.str.strip_chars().str.replace(",", ".", literal=True)
            e = pl.when(e.str.len_chars() == 0).then(None).otherwise(e)

        exprs.append(e.alias(nome))

    return df.with_columns(exprs)


def _linhas(df: pl.DataFrame, tabela: str):
    """Gera as tuplas de parâmetros na ordem das colunas do schema."""
    cols = schema.columns(tabela)
    nomes = [c.name for c in cols]
    numericas = {i for i, c in enumerate(cols) if c.kind == schema.NUMERIC}

    for linha in df.select(nomes).iter_rows():
        if not numericas:
            yield linha
            continue
        valores = list(linha)
        for i in numericas:
            v = valores[i]
            if v is None:
                continue
            try:
                valores[i] = Decimal(v)
            except (InvalidOperation, TypeError):
                valores[i] = None
        yield tuple(valores)


def execute_block_sql(tabela: str, n_linhas: int) -> str:
    """Monta um EXECUTE BLOCK que insere `n_linhas` de uma vez.

    Os parâmetros são declarados com `TYPE OF COLUMN`, então o próprio Firebird
    resolve o tipo de cada um a partir da tabela — não há tipo duplicado aqui e
    no schema.
    """
    cols = schema.columns(tabela)
    nomes = [c.name for c in cols]

    params: list[str] = []
    corpo: list[str] = []
    for i in range(n_linhas):
        ps = [f"p{i}_{j}" for j in range(len(cols))]
        params += [
            f"{p} TYPE OF COLUMN {tabela}.{c.name} = ?" for p, c in zip(ps, cols)
        ]
        valores = ", ".join(f":{p}" for p in ps)
        corpo.append(f"INSERT INTO {tabela} ({', '.join(nomes)}) VALUES ({valores});")

    return (
        "EXECUTE BLOCK (\n  "
        + ",\n  ".join(params)
        + "\n) AS BEGIN\n  "
        + "\n  ".join(corpo)
        + "\nEND"
    )


def carregar(
    con,
    df: pl.DataFrame,
    tabela: str,
    cfg: FirebirdConfig | None = None,
) -> int:
    """Insere o DataFrame na tabela. Devolve o número de linhas gravadas.

    A conexão é reaproveitada entre chamadas (o ETL carrega um arquivo por vez)
    e o commit acontece a cada `cfg.commit_every` linhas.
    """
    cfg = cfg or load_config()
    total = df.height
    if total == 0:
        return 0

    report = TruncationReport()
    df = normalizar(df, tabela, report)

    por_bloco = INSERTS_POR_BLOCO
    cur = con.cursor()
    bloco_stmt = cur.prepare(execute_block_sql(tabela, por_bloco))
    # Statement separado para a sobra: um EXECUTE BLOCK tem número fixo de
    # INSERTs, então o resto que não fecha um bloco vai linha a linha.
    linha_stmt = cur.prepare(execute_block_sql(tabela, 1))

    gravadas = 0
    desde_commit = 0
    inicio = time.time()
    lote: list = []

    try:
        for linha in _linhas(df, tabela):
            lote.append(linha)
            if len(lote) == por_bloco:
                cur.execute(bloco_stmt, [v for t in lote for v in t])
                gravadas += por_bloco
                desde_commit += por_bloco
                lote.clear()

                if desde_commit >= cfg.commit_every:
                    con.commit()
                    desde_commit = 0

                _progresso(tabela, gravadas, total, inicio)

        for linha in lote:
            cur.execute(linha_stmt, list(linha))
            gravadas += 1

        con.commit()
    finally:
        bloco_stmt.free()
        linha_stmt.free()

    _progresso(tabela, gravadas, total, inicio, fim=True)
    report.log(tabela)
    return gravadas


# Intervalo mínimo entre atualizações de progresso, em segundos.
#
# O "\r" só reescreve a linha num terminal; redirecionado para arquivo ou pipe,
# cada atualização vira uma linha nova. Sem esse limite, uma carga de 300 mil
# linhas produziu 204 KB de log só de progresso.
_INTERVALO_PROGRESSO = 1.0
_ultimo_progresso = 0.0


def _progresso(tabela: str, feitas: int, total: int, inicio: float, fim: bool = False) -> None:
    global _ultimo_progresso
    agora = time.time()
    if not fim and agora - _ultimo_progresso < _INTERVALO_PROGRESSO:
        return
    _ultimo_progresso = agora

    decorrido = max(agora - inicio, 1e-6)
    taxa = feitas / decorrido
    pct = (feitas * 100 / total) if total else 100.0

    if sys.stdout.isatty():
        fim_linha = "\n" if fim else ""
        print(
            f"\r{tabela} {pct:6.2f}%  {feitas:,}/{total:,}  {taxa:,.0f} linhas/s{fim_linha}",
            end="",
            flush=True,
        )
    elif fim:
        # Sem terminal, só o resultado final interessa no log.
        print(
            f"{tabela}: {feitas:,} linhas em {decorrido:,.1f}s ({taxa:,.0f} linhas/s)",
            flush=True,
        )
