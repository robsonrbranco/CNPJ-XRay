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

Charset
-------
As colunas de texto não declaram charset: herdam o padrão do banco, criado com
`DEFAULT CHARACTER SET WIN1252 COLLATION WIN_PTBR`.

WIN1252 e não UTF8 porque a fonte é o cadastro de pessoa jurídica brasileiro,
em português do Brasil, e os arquivos da Receita vêm em latin-1. UTF8 não
acrescenta nenhum caractere útil aqui e cobra 4 bytes por caractere no tamanho
declarado da coluna — em `estabelecimento`, 8,5 KB por registro contra 2,1 KB.
Medido: o banco fica 27% menor, com a mesma velocidade de carga.

A troca é segura porque WIN1252 e latin-1 só divergem na faixa 0x80-0x9F, e uma
varredura de ~800 MB da base (empresa, estabelecimento, socios, cnae,
municipio) não encontrou UM byte nessa faixa.

WIN_PTBR ordena acento como o português do Brasil espera: sem ela, ORDER BY
razao_social joga todo nome iniciado por acento para depois do Z.

Atenção ao parsear os CSV da RFB: o campo `complemento` contém ";" dentro de
valor entre aspas (ex.: "BLOCO: 01; APT: 144;"). Split ingênuo por ";" corrompe
~5% das linhas de estabelecimento — o parser precisa honrar aspas.

Ausência deliberada de travas
-----------------------------
Nenhuma tabela declara PRIMARY KEY, NOT NULL ou FOREIGN KEY. Não é esquecimento.

Dado público brasileiro chega com inconsistência: código de domínio repetido,
registro de tabela secundária sem o pai correspondente, campo obrigatório
vazio. Uma trava declarada aqui transformaria cada uma dessas ocorrências numa
exceção no meio de uma carga de 220 milhões de linhas que leva horas — e o ETL
morreria por causa de um dado que a Receita publicou assim.

A base carrega o que a fonte mandou. Os índices vêm depois da carga e existem
para performance de consulta, não para integridade. A conferência de qualidade
é feita à parte, sobre a base já carregada, e vira relatório — não aborto.

Quem consome a base precisa saber disso: `JOIN` com tabela de domínio deve ser
`LEFT JOIN`, porque código órfão existe.
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

    @property
    def ddl(self) -> str:
        # Sem PRIMARY KEY, sem NOT NULL, sem FK: a base de carga não impõe
        # nenhuma restrição de integridade. Ver a nota sobre travas no topo
        # do módulo.
        # Sem CHARACTER SET / COLLATE por coluna: o banco é criado com
        # DEFAULT CHARACTER SET WIN1252 COLLATION WIN_PTBR (ver
        # db.connection.create_database_sql), e toda coluna de texto herda daí.
        # Verificado: coluna declarada sem cláusula sai como WIN1252/WIN_PTBR.
        return f"{self.name} {self.fb_type}"


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
        _int("codigo"),
        _text("descricao", 200),
    ]


TABLES: dict[str, list[Column]] = {
    "empresa": [
        _text("cnpj_basico", 8),
        _text("razao_social", 200),
        _int("natureza_juridica"),
        _int("qualificacao_responsavel", "SMALLINT"),
        _numeric("capital_social"),
        _int("porte_empresa", "SMALLINT"),
        _text("ente_federativo_responsavel", 60),
    ],
    "estabelecimento": [
        _text("cnpj_basico", 8),
        _text("cnpj_ordem", 4),
        _text("cnpj_dv", 2),
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
        _text("cnpj_basico", 8),
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
        _text("cnpj_basico", 8),
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
    # Domínio por código. Enquanto as tabelas de domínio tinham PRIMARY KEY,
    # o índice vinha de graça junto com ela; ao remover as travas da carga o
    # índice foi junto, e sem ele todo JOIN de domínio vira varredura completa
    # da tabela de domínio para CADA linha do fato. Medido: contar órfãos de
    # `estabelecimento.municipio` levou 307s sem índice contra 1s com — e é o
    # mesmo custo que qualquer consulta de usuário pagaria.
    **{f"{t}_codigo": (t, "codigo") for t in DOMAIN_TABLES},
}


# Chave natural de cada tabela: o que a Receita descreve como identificador do
# registro. NÃO é PRIMARY KEY no banco -- serve para detectar carga duplicada e
# para a remoção de repetição que a fonte traz.
#
# `socios` fica de fora: o mesmo CNPJ tem vários sócios e a RFB mascara o CPF,
# então não existe identificador de linha.
CHAVES_NATURAIS: dict[str, tuple[str, ...]] = {
    "empresa": ("cnpj_basico",),
    "estabelecimento": ("cnpj_basico", "cnpj_ordem", "cnpj_dv"),
    "simples": ("cnpj_basico",),
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
