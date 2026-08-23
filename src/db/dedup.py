"""Remoção da repetição de chave que a fonte traz.

A Receita publica registro com chave repetida. Na competência 2026-08 é um só:
`cnpj_basico` 08314885 aparece duas vezes em `Empresas2.zip`, uma linha com
dado real ("FLAVIO PAVAO DE SOUZA", natureza 4120) e outra praticamente vazia
(razão social nula, natureza 0, porte nulo). Um registro-fantasma.

Isso não impede a carga — o schema não declara PRIMARY KEY justamente para que
uma linha assim não mate três horas de trabalho —, mas deixa a base com chave
ambígua para quem consulta. A limpeza acontece aqui, depois da carga, junto
com os índices.

Como
----
O idioma do Firebird para isso é `RDB$DB_KEY`, o identificador físico da linha,
que existe mesmo sem chave declarada:

    SELECT cnpj_basico, COUNT(*), MIN(RDB$DB_KEY)
      FROM empresa GROUP BY cnpj_basico HAVING COUNT(*) > 1

    DELETE FROM empresa
     WHERE cnpj_basico = ? AND RDB$DB_KEY <> ?

O `MIN(RDB$DB_KEY)` preserva a linha fisicamente primeira. Como as duplicatas
da RFB vêm em linhas consecutivas do mesmo arquivo, carregadas pelo mesmo
worker no mesmo bloco, a ordem física segue a ordem do arquivo — na prática,
fica a primeira ocorrência. Confirmado na 2026-08: os dois `RDB$DB_KEY` são
adjacentes (…d582e10d e …d682e10d) e o MIN é o da linha com dado.

Ressalva: isso não é garantido. Se a fonte publicasse a linha vazia antes da
preenchida, o MIN preservaria a vazia. Não há critério melhor sem inventar uma
regra de qualidade sobre o dado, o que este projeto deliberadamente não faz.

Ordem em relação à validação
-----------------------------
A fração de duplicatas é conferida ANTES desta limpeza, não depois. Se um dia
a Receita mudar o particionamento e o ETL carregar o mesmo arquivo duas vezes,
deduplicar "consertaria" o problema apagando milhões de linhas e a carga
errada passaria despercebida. Repetição em massa tem que abortar; só ruído
isolado é limpo.
"""

import logging
import time

from . import schema

logger = logging.getLogger(__name__)


def localizar(con, tabela: str, chave: tuple[str, ...]) -> list[tuple]:
    """Devolve (valores_da_chave..., db_key_a_preservar) por chave repetida."""
    cols = ", ".join(chave)
    cur = con.cursor()
    cur.execute(
        f"SELECT {cols}, MIN(RDB$DB_KEY) FROM {tabela} "
        f"GROUP BY {cols} HAVING COUNT(*) > 1"
    )
    return cur.fetchall()


def remover(con, tabelas: dict[str, tuple[str, ...]] | None = None) -> dict[str, int]:
    """Remove as linhas repetidas, preservando uma de cada chave.

    Devolve quantas linhas foram apagadas por tabela.
    """
    tabelas = tabelas or schema.CHAVES_NATURAIS
    removidas: dict[str, int] = {}

    for tabela, chave in tabelas.items():
        inicio = time.time()
        repetidas = localizar(con, tabela, chave)
        busca = time.time() - inicio

        if not repetidas:
            logger.info(
                "%s: nenhuma chave repetida (%s em %.0fs)",
                tabela, ", ".join(chave), busca,
            )
            continue

        onde = " AND ".join(f"{c} = ?" for c in chave)
        cur = con.cursor()
        total = 0
        for linha in repetidas:
            valores, db_key = list(linha[:-1]), linha[-1]
            cur.execute(
                f"DELETE FROM {tabela} WHERE {onde} AND RDB$DB_KEY <> ?",
                [*valores, db_key],
            )
            total += cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
        con.commit()

        removidas[tabela] = total
        logger.warning(
            "%s: %d chave(s) repetida(s) na fonte, %d linha(s) removida(s) "
            "(busca em %.0fs)",
            tabela, len(repetidas), total, busca,
        )

    return removidas
