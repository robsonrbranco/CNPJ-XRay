"""
Definição única do schema CNPJ-XRay para Firebird 3.0.

É a fonte de verdade de onde saem o DDL das tabelas, o INSERT parametrizado
usado na carga e o DDL dos índices — assim as três coisas não saem de sincronia.

Dimensionamento dos VARCHAR
---------------------------
O layout oficial da Receita (cnpj-metadados.pdf) descreve os campos mas NÃO
publica tamanho de nenhum deles: os arquivos são CSV com ";" como separador.
As larguras abaixo foram medidas sobre a base real (competência 2026-08,
~53 milhões de linhas amostradas em todas as partes 0..9) e então arredondadas
para cima com folga, porque VARCHAR curto demais trunca dado em silêncio.

    coluna                        medido   adotado
    razao_social                     150      200
    ente_federativo_responsavel       37       60
    nome_fantasia                     55       80
    nome_cidade_exterior              52       80
    cnae_fiscal_secundaria           791     1200
    tipo_logradouro                   20       40
    logradouro                        60      100
    complemento                      156      250
    bairro                            50       80
    correio_eletronico                98      150
    situacao_especial                 25       60
    nome_socio                       149      200
    nome_representante                60      100
    descricao (domínio)              150      200

Atenção ao parsear os CSV da RFB: o campo `complemento` contém ";" dentro de
valor entre aspas (ex.: "BLOCO: 01; APT: 144;"). Split ingênuo por ";" corrompe
~5% das linhas de estabelecimento — o parser precisa honrar aspas.
"""

from dataclasses import dataclass

# Famílias de tipo. Determinam a conversão aplicada na carga.
TEXT = "text"
INT = "int"
DATE = "date"
NUMERIC = "numeric"


@dataclass(frozen=True)
class Column:
    name: str
    kind: str
    # Tipo da coluna no Firebird.
    fb_type: str
    # Comprimento máximo em caracteres, para colunas TEXT. None nos demais.
    max_len: int | None = None
    not_null: bool = False
    primary_key: bool = False

    @property
    def ddl(self) -> str:
        ddl = f"{self.name} {self.fb_type}"
        if self.primary_key:
            ddl += " NOT NULL PRIMARY KEY"
        elif self.not_null:
            ddl += " NOT NULL"
        return ddl


def _text(name, size, **kw):
    return Column(name, TEXT, f"VARCHAR({size})", max_len=size, **kw)


def _int(name, fb_type="INTEGER", **kw):
    return Column(name, INT, fb_type, **kw)


def _date(name):
    return Column(name, DATE, "DATE")


def _numeric(name, fb_type="NUMERIC(18,2)"):
    return Column(name, NUMERIC, fb_type)


def _dominio():
    """Tabela de domínio código/descrição (cnae, motivo, municipio, ...)."""
    return [
        _int("codigo", primary_key=True),
        _text("descricao", 200),
    ]


TABLES: dict[str, list[Column]] = {
    "empresa": [
        _text("cnpj_basico", 8, not_null=True),
        _text("razao_social", 200),
        _int("natureza_juridica"),
        _int("qualificacao_responsavel", "SMALLINT"),
        _numeric("capital_social"),
        _int("porte_empresa", "SMALLINT"),
        _text("ente_federativo_responsavel", 60),
    ],
    "estabelecimento": [
        _text("cnpj_basico", 8, not_null=True),
        _text("cnpj_ordem", 4, not_null=True),
        _text("cnpj_dv", 2, not_null=True),
        _int("identificador_matriz_filial", "SMALLINT"),
        _text("nome_fantasia", 80),
        _int("situacao_cadastral", "SMALLINT"),
        _date("data_situacao_cadastral"),
        _int("motivo_situacao_cadastral", "SMALLINT"),
        _text("nome_cidade_exterior", 80),
        _int("pais"),
        _date("data_inicio_atividade"),
        _int("cnae_fiscal_principal"),
        # Lista de CNAEs separada por vírgula: de longe a coluna mais larga.
        _text("cnae_fiscal_secundaria", 1200),
        _text("tipo_logradouro", 40),
        _text("logradouro", 100),
        _text("numero", 20),
        _text("complemento", 250),
        _text("bairro", 80),
        _text("cep", 10),
        _text("uf", 2),
        _int("municipio"),
        _text("ddd_1", 5),
        _text("telefone_1", 12),
        _text("ddd_2", 5),
        _text("telefone_2", 12),
        _text("ddd_fax", 5),
        _text("fax", 12),
        _text("correio_eletronico", 150),
        _text("situacao_especial", 60),
        _date("data_situacao_especial"),
    ],
    "socios": [
        _text("cnpj_basico", 8, not_null=True),
        _int("identificador_socio", "SMALLINT"),
        _text("nome_socio", 200),
        _text("cnpj_cpf_socio", 14),
        _int("qualificacao_socio", "SMALLINT"),
        _date("data_entrada_sociedade"),
        _int("pais"),
        _text("representante_legal", 14),
        _text("nome_representante", 100),
        _int("qualificacao_repres_legal", "SMALLINT"),
        _int("faixa_etaria", "SMALLINT"),
    ],
    "simples": [
        _text("cnpj_basico", 8, not_null=True),
        _text("opcao_pelo_simples", 1),
        _date("data_opcao_simples"),
        _date("data_exclusao_simples"),
        _text("opcao_mei", 1),
        _date("data_opcao_mei"),
        _date("data_exclusao_mei"),
    ],
    "cnae": _dominio(),
    "motivo": _dominio(),
    "municipio": _dominio(),
    "natureza": _dominio(),
    "pais": _dominio(),
    "qualificacao": _dominio(),
}

# Tabelas de domínio: chegam num único arquivo pequeno cada uma.
DOMAIN_TABLES = ("cnae", "motivo", "municipio", "natureza", "pais", "qualificacao")

# Tabelas de fato: chegam particionadas em 10 arquivos (exceto simples).
FACT_TABLES = ("empresa", "estabelecimento", "socios", "simples")


# ---------------------------------------------------------------------------
# Índices
#
# Criados SEMPRE depois da carga: manter índice ativo durante INSERT em massa
# é o que mais custa numa carga Firebird. Não há equivalente a GIN/pg_trgm no
# Firebird 3.0, então a busca textual por razão social / nome fantasia que
# existia na versão PostgreSQL não tem contrapartida aqui.
# ---------------------------------------------------------------------------
INDEXES: dict[str, tuple[str, ...]] = {
    "empresa_cnpj": ("empresa", "cnpj_basico"),
    "estabelecimento_cnpj": ("estabelecimento", "cnpj_basico"),
    "estabelecimento_cnpj_completo": (
        "estabelecimento",
        "cnpj_basico",
        "cnpj_ordem",
        "cnpj_dv",
    ),
    "socios_cnpj": ("socios", "cnpj_basico"),
    "simples_cnpj": ("simples", "cnpj_basico"),
    "estabelecimento_situacao": ("estabelecimento", "situacao_cadastral"),
    "estabelecimento_municipio": ("estabelecimento", "municipio"),
}


# ---------------------------------------------------------------------------
# Chaves para UPDATE OR INSERT (a cláusula MATCHING).
#
# UPDATE OR INSERT tem a sintaxe do INSERT mas não levanta exceção quando a
# chave já existe — é a mesma instrução para a carga inicial e para a
# atualização mensal da base, o que evita ter dois caminhos de código.
#
# Só vale a pena com índice ÚNICO sobre a chave: sem ele o Firebird não tem
# como decidir o que atualizar e a semântica com duplicata fica indefinida.
# Por isso o modo upsert exige criar os índices ANTES da carga, ao contrário
# da carga inicial — que é sempre mais rápida com a tabela sem índice nenhum.
#
# `socios` não tem chave natural: o mesmo CNPJ pode ter vários sócios e a
# própria RFB mascara o CPF, então não há como identificar a linha. Atualizar
# sócios é recarga completa da tabela, não upsert.
# ---------------------------------------------------------------------------
MATCHING: dict[str, tuple[str, ...]] = {
    "empresa": ("cnpj_basico",),
    "estabelecimento": ("cnpj_basico", "cnpj_ordem", "cnpj_dv"),
    "simples": ("cnpj_basico",),
    **{t: ("codigo",) for t in DOMAIN_TABLES},
}


def columns(table: str) -> list[Column]:
    return TABLES[table]


def column_names(table: str) -> list[str]:
    return [c.name for c in TABLES[table]]


def create_table_sql(table: str) -> str:
    cols = ",\n    ".join(c.ddl for c in TABLES[table])
    return f"CREATE TABLE {table} (\n    {cols}\n)"


def insert_sql(table: str) -> str:
    """INSERT parametrizado usado pelo carregador em massa."""
    cols = column_names(table)
    placeholders = ", ".join("?" * len(cols))
    return f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders})"


def create_index_sql(name: str) -> str:
    table, *cols = INDEXES[name]
    return f"CREATE INDEX {name} ON {table} ({', '.join(cols)})"


# ---------------------------------------------------------------------------
# Trava de sanidade: o Firebird 3.0 limita identificador a 31 caracteres
# (o 4.0 subiu para 63). Estourar isso só aparece como "-104 Name longer than
# database column size" na hora do CREATE TABLE, o que é um erro caro de
# descobrir tarde. Por isso a checagem roda na importação do módulo.
#
# Foi o que obrigou a renomear `qualificacao_representante_legal` (32 chars) da
# versão PostgreSQL para `qualificacao_repres_legal` (25).
# ---------------------------------------------------------------------------
MAX_IDENTIFIER = 31


def _validar_identificadores() -> None:
    longos = []
    for tabela, cols in TABLES.items():
        if len(tabela) > MAX_IDENTIFIER:
            longos.append(f"tabela {tabela} ({len(tabela)})")
        for c in cols:
            if len(c.name) > MAX_IDENTIFIER:
                longos.append(f"{tabela}.{c.name} ({len(c.name)})")
    for idx in INDEXES:
        if len(idx) > MAX_IDENTIFIER:
            longos.append(f"índice {idx} ({len(idx)})")
    if longos:
        raise ValueError(
            f"identificadores acima de {MAX_IDENTIFIER} caracteres, "
            f"limite do Firebird 3.0: {', '.join(longos)}"
        )


_validar_identificadores()
