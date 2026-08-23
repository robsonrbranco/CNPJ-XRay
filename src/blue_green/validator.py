"""Validação da base recém-construída, antes de promovê-la a produção.

A promoção é irreversível na prática (o banco antigo é descartado logo depois),
então nada é promovido sem antes provar que está inteiro: todas as tabelas
existem, nenhuma está vazia e todos os índices foram criados.
"""

import logging
from dataclasses import dataclass, field

from firebird.driver import DatabaseError

from src.blue_green.constants import EXPECTED_INDEXES, EXPECTED_TABLES, MAY_BE_EMPTY
from src.db import manage, schema
from src.db.config import FirebirdConfig, load_config
from src.db.connection import conectar

logger = logging.getLogger(__name__)


@dataclass
class ValidationResult:
    is_valid: bool
    erro_conexao: str | None = None
    missing_tables: list[str] = field(default_factory=list)
    empty_tables: list[str] = field(default_factory=list)
    missing_indexes: list[str] = field(default_factory=list)
    contagens: dict[str, int] = field(default_factory=dict)
    duplicadas: dict[str, int] = field(default_factory=dict)

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
        if self.duplicadas:
            det = ", ".join(f"{t}: {n:,}" for t, n in self.duplicadas.items())
            problemas.append(f"CNPJ repetido (carga possivelmente duplicada) — {det}")
        return "INVÁLIDA — " + "; ".join(problemas)


# Fração de chaves repetidas a partir da qual a carga é considerada duplicada.
#
# A separação entre os dois casos é enorme, então o limiar não precisa ser
# preciso — precisa só cair no vão entre eles:
#
#   ruído da fonte ....... 1 CNPJ repetido em 69.523.304  = 0,0000014%
#   arquivo lido 2x ...... a menor partição de empresa    = 6,5%
#
# 0,5% fica 13x abaixo do menor caso de arquivo duplicado e centenas de
# milhares de vezes acima do ruído observado.
LIMIAR_DUPLICADAS = 0.005


def _cnpj_repetido(con, contagens: dict[str, int]) -> dict[str, int]:
    """Detecta carga duplicada pela fração de CNPJ repetido.

    Guarda contra carregar o mesmo dado duas vezes — o cenário concreto é a
    Receita mudar o particionamento dos arquivos. Hoje `Empresas0.zip` e
    `Empresas1..9.zip` são partições disjuntas (verificado: zero `cnpj_basico`
    em comum, e a soma das dez bate com o total), mas o arquivo 0 é 6x maior
    que os demais e parece o conjunto completo. Se um dia passar a ser, o ETL
    dobraria a base em silêncio.

    Não é validação do DADO. A base tem CNPJ repetido vindo da fonte e isso é
    esperado: a competência 2026-08 traz `08314885` duas vezes em
    `Empresas2.zip`, uma linha com dado real e outra praticamente vazia. Com
    PRIMARY KEY declarada, a carga morreria por causa dela depois de três
    horas. Por isso repetição isolada passa — e sai como aviso no log, para
    não sumir de vista.
    """
    achados: dict[str, int] = {}
    cur = con.cursor()

    for tabela, chave in schema.CHAVES_NATURAIS.items():
        total = contagens.get(tabela) or 0
        if not total:
            continue
        cols = ", ".join(chave)
        # As colunas da chave vão nomeadas no SELECT interno: o Firebird recusa
        # tabela derivada com coluna sem nome ("no column name specified for
        # column number 1 in derived table"), então `SELECT 1` não serve aqui.
        cur.execute(
            f"SELECT COUNT(*) FROM (SELECT {cols} FROM {tabela} "
            f"GROUP BY {cols} HAVING COUNT(*) > 1) d"
        )
        n = int(cur.fetchone()[0] or 0)
        if not n:
            continue

        fracao = n / total
        if fracao >= LIMIAR_DUPLICADAS:
            achados[tabela] = n
        else:
            logger.warning(
                "%s: %d chave(s) repetida(s) em %s linhas (%.6f%%) — abaixo do "
                "limiar de %.1f%%, tratado como ruído da fonte",
                tabela, n, f"{total:,}", fracao * 100, LIMIAR_DUPLICADAS * 100,
            )

    return achados


def validar(cfg: FirebirdConfig | None = None, database: str | None = None) -> ValidationResult:
    """Valida a base em `database` (padrão: a staging derivada da config)."""
    cfg = cfg or load_config()
    alvo = database or cfg.staging_database

    # Aponta a config para a base a validar sem alterar a original.
    cfg_alvo = FirebirdConfig(**{**cfg.__dict__, "database": alvo})

    # Só a abertura entra neste try: envolver a inspeção inteira faria um erro
    # de SQL na checagem sair como "não foi possível abrir a base", que manda
    # quem for depurar para o lado errado.
    try:
        gerenciador = conectar(cfg_alvo)
        con = gerenciador.__enter__()
    except DatabaseError as e:
        return ValidationResult(
            is_valid=False,
            erro_conexao=str(e).splitlines()[0],
            missing_tables=EXPECTED_TABLES[:],
            missing_indexes=EXPECTED_INDEXES[:],
        )

    try:
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

        duplicadas = _cnpj_repetido(con, contagens)

        return ValidationResult(
            is_valid=not (faltando or vazias or sem_indice or duplicadas),
            missing_tables=faltando,
            empty_tables=vazias,
            missing_indexes=sem_indice,
            contagens=contagens,
            duplicadas=duplicadas,
        )
    finally:
        gerenciador.__exit__(None, None, None)
