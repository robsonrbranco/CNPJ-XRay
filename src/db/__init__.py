"""Camada de acesso a dados do CNPJ-XRay (Firebird 3.0)."""

from .config import FirebirdConfig, load_config
from .connection import (
    conectar,
    conectar_servidor,
    create_if_not_exists,
    database_exists,
    estatisticas,
    modo_carga,
    modo_producao,
)
from .loader import carregar, normalizar
from .manage import contar, criar_indices, criar_tabelas, dropar_tabela, indice_existe, tabela_existe

__all__ = [
    "FirebirdConfig",
    "load_config",
    "conectar",
    "conectar_servidor",
    "database_exists",
    "create_if_not_exists",
    "modo_carga",
    "modo_producao",
    "estatisticas",
    "carregar",
    "normalizar",
    "criar_tabelas",
    "criar_indices",
    "dropar_tabela",
    "tabela_existe",
    "indice_existe",
    "contar",
]
