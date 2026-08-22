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


# Tabelas onde o CNPJ identifica a linha, e por isso repetição indica que o
# mesmo arquivo entrou duas vezes.
#
# `socios` fica de fora: o mesmo CNPJ tem vários sócios, então repetição ali é
# normal. `estabelecimento` usa a tripla completa.
CHAVE_UNICA: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("empresa", ("cnpj_basico",)),
    ("estabelecimento", ("cnpj_basico", "cnpj_ordem", "cnpj_dv")),
    ("simples", ("cnpj_basico",)),
)


def _cnpj_repetido(con, contagens: dict[str, int]) -> dict[str, int]:
    """Detecta o mesmo CNPJ gravado mais de uma vez.

    Guarda contra carregar o mesmo dado duas vezes — o cenário concreto é a
    Receita mudar o particionamento dos arquivos. Hoje `Empresas0.zip` e
    `Empresas1..9.zip` são partições disjuntas (verificado: zero `cnpj_basico`
    em comum, e a soma das dez bate com o total), mas o arquivo 0 é 6x maior
    que os demais e parece o conjunto completo. Se um dia passar a ser, o ETL
    dobraria a base em silêncio.

    Não é validação do DADO — o schema continua sem trava nenhuma, e código
    órfão ou campo vazio seguem passando. É validação da CARGA: CNPJ repetido
    não é inconsistência da fonte, é o mesmo arquivo lido duas vezes.
    """
    achados: dict[str, int] = {}
    cur = con.cursor()

    for tabela, chave in CHAVE_UNICA:
        if not contagens.get(tabela):
            continue
        cols = ", ".join(chave)
        # As colunas da chave vão nomeadas no SELECT interno: o Firebird recusa
        # tabela derivada com coluna sem nome ("no column name specified for
        # column number 1 in derived table"), então `SELECT 1` não serve aqui.
        cur.execute(
            f"SELECT COUNT(*) FROM (SELECT {cols} FROM {tabela} "
            f"GROUP BY {cols} HAVING COUNT(*) > 1) d"
        )
        n = cur.fetchone()[0] or 0
        if n:
            achados[tabela] = int(n)

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
