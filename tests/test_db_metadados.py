"""Testes da proveniência gravada dentro da base.

Sem banco: um cursor falso registra o SQL emitido. É teste fraco para
comportamento de dados, mas forte para o que importa aqui — que a gravação
**substitui** o conjunto anterior em vez de acrescentar a ele.

Esse é o ponto: se uma recarga deixasse a proveniência da carga anterior
convivendo com a nova, a base afirmaria duas competências ao mesmo tempo. E a
tabela existe justamente para responder "de quando é este dado?" com uma
resposta só.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.db import manage, schema  # noqa: E402


class CursorFalso:
    def __init__(self, registro, linhas=None):
        self._registro = registro
        self._linhas = linhas or []

    def execute(self, sql, params=None):
        self._registro.append((" ".join(sql.split()), params))

    def fetchall(self):
        return self._linhas

    def fetchone(self):
        return self._linhas[0] if self._linhas else None


class ConexaoFalsa:
    def __init__(self, tabela_existe=True, linhas=None):
        self.sql = []
        self.commits = 0
        self._existe = tabela_existe
        self._linhas = linhas or []

    def cursor(self):
        return CursorFalso(self.sql, self._linhas)

    def commit(self):
        self.commits += 1


@pytest.fixture
def con(monkeypatch):
    c = ConexaoFalsa()
    monkeypatch.setattr(manage, "tabela_existe", lambda con, nome: True)
    return c


def _comandos(con):
    return [sql for sql, _ in con.sql]


def test_gravar_apaga_antes_de_inserir(con):
    """A garantia central. Sem o DELETE, uma recarga deixaria a proveniência
    anterior convivendo com a nova, e a base afirmaria duas competências."""
    manage.gravar_metadados(con, {"competencia": "2026-09"})

    cmds = _comandos(con)
    assert any(c.startswith("DELETE FROM") for c in cmds), cmds
    primeiro_insert = next(i for i, c in enumerate(cmds) if c.startswith("INSERT"))
    primeiro_delete = next(i for i, c in enumerate(cmds) if c.startswith("DELETE"))
    assert primeiro_delete < primeiro_insert, "inseriu antes de apagar"


def test_gravar_insere_uma_linha_por_chave(con):
    manage.gravar_metadados(con, {"a": 1, "b": 2, "c": 3})

    inserts = [p for sql, p in con.sql if sql.startswith("INSERT")]
    assert len(inserts) == 3
    assert ("a", "1") in inserts


def test_valores_sao_convertidos_para_texto(con):
    """A coluna é VARCHAR. Passar int sem converter quebraria na gravação, e o
    pipeline passa contagens."""
    manage.gravar_metadados(con, {"linhas_total": 222197006})

    assert ("linhas_total", "222197006") in [p for sql, p in con.sql if sql.startswith("INSERT")]


def test_valor_longo_e_truncado_e_nao_estoura(con):
    """`origem` pode ser um caminho longo. VARCHAR(500) estourado abortaria a
    carga inteira no último passo, depois de seis horas."""
    manage.gravar_metadados(con, {"origem": "x" * 900})

    valor = [p[1] for sql, p in con.sql if sql.startswith("INSERT")][0]
    assert len(valor) == 500


def test_chave_longa_e_truncada(con):
    manage.gravar_metadados(con, {"k" * 90: "v"})
    chave = [p[0] for sql, p in con.sql if sql.startswith("INSERT")][0]
    assert len(chave) == 40


def test_ler_devolve_dicionario(monkeypatch):
    monkeypatch.setattr(manage, "tabela_existe", lambda con, nome: True)
    c = ConexaoFalsa(linhas=[("competencia  ", "2026-09 "), ("linhas_total", "10")])

    assert manage.ler_metadados(c) == {"competencia": "2026-09", "linhas_total": "10"}


def test_ler_em_base_sem_a_tabela_devolve_vazio(monkeypatch):
    """Base construída antes de a proveniência existir — a produção era uma
    dessas. Precisa devolver vazio, não estourar."""
    monkeypatch.setattr(manage, "tabela_existe", lambda con, nome: False)

    assert manage.ler_metadados(ConexaoFalsa()) == {}


def test_a_tabela_fica_fora_de_TABLES():
    """`TABLES` dirige o ETL. Se `metadados` entrasse ali, a carga procuraria
    um arquivo da Receita para ela."""
    assert schema.METADADOS not in schema.TABLES
