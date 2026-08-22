"""Validação da base recém-construída, antes de promovê-la a produção.

A promoção é irreversível na prática (o banco antigo é descartado logo depois),
então nada é promovido sem antes provar que está inteiro: todas as tabelas
existem, nenhuma está vazia e todos os índices foram criados.
"""

from dataclasses import dataclass, field

from firebird.driver import DatabaseError

from src.blue_green.constants import EXPECTED_INDEXES, EXPECTED_TABLES, MAY_BE_EMPTY
from src.db import manage
from src.db.config import FirebirdConfig, load_config
from src.db.connection import conectar


@dataclass
class ValidationResult:
    is_valid: bool
    erro_conexao: str | None = None
    missing_tables: list[str] = field(default_factory=list)
    empty_tables: list[str] = field(default_factory=list)
    missing_indexes: list[str] = field(default_factory=list)
    contagens: dict[str, int] = field(default_factory=dict)

    @property
    def summary(self) -> str:
        if self.is_valid:
            total = sum(self.contagens.values())
            return f"VÁLIDA — {total:,} linhas em {len(self.contagens)} tabelas, pronta para troca"
        if self.erro_conexao:
            return f"INVÁLIDA — não foi possível abrir a base: {self.erro_conexao}"
        problemas = []
        if self.missing_tables:
            problemas.append(f"tabelas ausentes: {', '.join(self.missing_tables)}")
        if self.empty_tables:
            problemas.append(f"tabelas vazias: {', '.join(self.empty_tables)}")
        if self.missing_indexes:
            problemas.append(f"índices ausentes: {', '.join(self.missing_indexes)}")
        return "INVÁLIDA — " + "; ".join(problemas)


def validar(cfg: FirebirdConfig | None = None, database: str | None = None) -> ValidationResult:
    """Valida a base em `database` (padrão: a staging derivada da config)."""
    cfg = cfg or load_config()
    alvo = database or cfg.staging_database

    # Aponta a config para a base a validar sem alterar a original.
    cfg_alvo = FirebirdConfig(**{**cfg.__dict__, "database": alvo})

    try:
        with conectar(cfg_alvo) as con:
            faltando: list[str] = []
            vazias: list[str] = []
            contagens: dict[str, int] = {}

            for tabela in EXPECTED_TABLES:
                if not manage.tabela_existe(con, tabela):
                    faltando.append(tabela)
                    continue
                n = manage.contar(con, tabela)
                contagens[tabela] = n
                if n == 0 and tabela not in MAY_BE_EMPTY:
                    vazias.append(tabela)

            sem_indice = [
                nome for nome in EXPECTED_INDEXES if not manage.indice_existe(con, nome)
            ]

            return ValidationResult(
                is_valid=not (faltando or vazias or sem_indice),
                missing_tables=faltando,
                empty_tables=vazias,
                missing_indexes=sem_indice,
                contagens=contagens,
            )
    except DatabaseError as e:
        return ValidationResult(
            is_valid=False,
            erro_conexao=str(e).splitlines()[0],
            missing_tables=EXPECTED_TABLES[:],
            missing_indexes=EXPECTED_INDEXES[:],
        )
