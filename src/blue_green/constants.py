"""Objetos que uma base recém-construída precisa ter para ser promovida.

Deriva de db.schema em vez de repetir a lista: se uma tabela ou índice for
adicionado lá, a validação do blue-green passa a exigi-lo automaticamente.
"""

from src.db import schema

EXPECTED_TABLES: list[str] = list(schema.TABLES)
EXPECTED_INDEXES: list[str] = list(schema.INDEXES)

# Tabelas que podem legitimamente vir vazias numa competência.
#
# Nenhuma, hoje: as seis tabelas de domínio e as quatro de fato sempre têm
# conteúdo. Existir vazia é sinal de carga interrompida, não de mês atípico.
MAY_BE_EMPTY: frozenset[str] = frozenset()
