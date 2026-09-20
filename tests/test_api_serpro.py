"""Testes da tradução para o esquema do SERPRO.

Transformação pura, então os testes usam uma ficha montada à mão em vez do
banco. A ficha do fixture tem a forma exata que `consultar()` devolve — se
aquela função mudar de forma, estes testes continuam passando e mentindo, e é
por isso que há um teste que confere as chaves contra o módulo real.
"""

import sys
from decimal import Decimal
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.api import serpro  # noqa: E402
from src.api.serpro import EstabelecimentoNaoEncontrado  # noqa: E402


@pytest.fixture
def ficha():
    return {
        "cnpj_basico": "11222333",
        "empresa": {
            "cnpj_basico": "11222333",
            "razao_social": "ACME INDUSTRIA LTDA",
            "natureza_juridica": 2062,
            "natureza_juridica_descricao": "Sociedade Empresária Limitada",
            "qualificacao_responsavel": 49,
            "qualificacao_responsavel_descricao": "Sócio-Administrador",
            "capital_social": Decimal("1234.56"),
            "porte_empresa": 3,
            "porte_empresa_descricao": "Empresa de Pequeno Porte",
            "ente_federativo_responsavel": None,
        },
        "estabelecimentos": [
            {
                "cnpj_basico": "11222333", "cnpj_ordem": "0001", "cnpj_dv": "81",
                "cnpj_completo": "11222333000181",
                "identificador_matriz_filial": 1, "tipo": "MATRIZ",
                "nome_fantasia": "ACME",
                "situacao_cadastral": 2,
                "situacao_cadastral_descricao": "ATIVA",
                "data_situacao_cadastral": date(2020, 5, 10),
                "motivo_situacao_cadastral": 0, "motivo_descricao": None,
                "situacao_especial": None, "data_situacao_especial": None,
                "data_inicio_atividade": date(2010, 3, 1),
                "cnae_fiscal_principal": 1091101,
                "cnae_principal_descricao": "Fabricação de produtos de padaria",
                "cnae_fiscal_secundaria": "4721102,4729699",
                "tipo_logradouro": "RUA", "logradouro": "DAS FLORES",
                "numero": "100", "complemento": "SALA 2",
                "bairro": "CENTRO", "cep": "01001000", "uf": "SP",
                "municipio": 7107, "municipio_descricao": "SAO PAULO",
                "pais": None, "pais_descricao": None,
                "ddd_1": "11", "telefone_1": "30001000",
                "ddd_2": None, "telefone_2": None,
                "correio_eletronico": "contato@acme.com.br",
            },
            {
                "cnpj_basico": "11222333", "cnpj_ordem": "0002", "cnpj_dv": "62",
                "cnpj_completo": "11222333000262",
                "identificador_matriz_filial": 2, "tipo": "FILIAL",
                "nome_fantasia": "ACME FILIAL",
                "situacao_cadastral": 8,
                "situacao_cadastral_descricao": "BAIXADA",
                "data_situacao_cadastral": date(2023, 1, 5),
                "motivo_situacao_cadastral": 1, "motivo_descricao": "EXTINCAO",
                "situacao_especial": "EM LIQUIDACAO",
                "data_situacao_especial": date(2022, 12, 1),
                "data_inicio_atividade": date(2015, 8, 20),
                "cnae_fiscal_principal": 4721102,
                "cnae_principal_descricao": "Padaria e confeitaria",
                "cnae_fiscal_secundaria": None,
                "tipo_logradouro": "AV", "logradouro": "BRASIL",
                "numero": "S/N", "complemento": None,
                "bairro": "JARDIM", "cep": "02002000", "uf": "SP",
                "municipio": 7107, "municipio_descricao": "SAO PAULO",
                "pais": None, "pais_descricao": None,
                "ddd_1": None, "telefone_1": None,
                "ddd_2": "11", "telefone_2": "40004000",
                "correio_eletronico": None,
            },
        ],
        "cnaes_secundarios": {4721102: "Padaria e confeitaria",
                              4729699: "Comércio de produtos alimentícios"},
        "socios": [
            {
                "identificador_socio": 2, "tipo": "PESSOA FÍSICA",
                "nome_socio": "MARIA DA SILVA", "cnpj_cpf_socio": "***794780**",
                "qualificacao_socio": 49, "qualificacao_descricao": "Sócio-Administrador",
                "data_entrada_sociedade": date(2010, 3, 1),
                "pais": None, "pais_descricao": None,
                "faixa_etaria": 5, "faixa_etaria_descricao": "41 a 50 anos",
                "nome_representante": None,
                "representante_legal": None,
                "qualificacao_repres_legal": None,
            },
        ],
        "simples": {
            "opcao_pelo_simples": "S",
            "opcao_simples_descricao": "SIM",
            "data_opcao_simples": date(2010, 4, 1),
            "data_exclusao_simples": None,
            "opcao_mei": "N",
            "opcao_mei_descricao": "NÃO",
            "data_opcao_mei": None,
            "data_exclusao_mei": None,
        },
    }


MATRIZ = "11222333000181"
FILIAL = "11222333000262"


# ---------------------------------------------------------------------------
# seleção do estabelecimento
# ---------------------------------------------------------------------------

def test_seleciona_o_estabelecimento_pelo_ni(ficha):
    """`consultar()` devolve todos; o SERPRO responde sobre um."""
    assert serpro.basica(ficha, MATRIZ)["nomeFantasia"] == "ACME"
    assert serpro.basica(ficha, FILIAL)["nomeFantasia"] == "ACME FILIAL"


def test_ni_valido_sem_estabelecimento_levanta(ficha):
    """O chamador traduz para 404 — não pode virar resposta vazia."""
    with pytest.raises(EstabelecimentoNaoEncontrado):
        serpro.basica(ficha, "11222333000999")


# ---------------------------------------------------------------------------
# campos
# ---------------------------------------------------------------------------

def test_capital_social_em_centavos(ficha):
    assert serpro.basica(ficha, MATRIZ)["capitalSocial"] == 123456


def test_capital_social_grande(ficha):
    ficha["empresa"]["capital_social"] = Decimal("532014391511.00")
    assert serpro.basica(ficha, MATRIZ)["capitalSocial"] == 53201439151100


@pytest.mark.parametrize("valor,centavos", [
    ("0.29", 29),     # float(0.29) * 100 = 28.999999999999996 -> int() = 28
    ("0.57", 57),
    ("1.13", 113),
    ("8.70", 870),
])
def test_capital_social_nao_passa_por_float(ficha, valor, centavos):
    """Os valores acima são os que de fato quebram com float.

    A primeira versão deste teste usava 1234.56 e afirmava que via float daria
    123455. Era falso: 1234.56 e 532014391511.00 sobrevivem ao float por sorte,
    e a mutação que trocava Decimal por float passou incólume. Teste que não
    distingue as duas implementações não testa nada.
    """
    ficha["empresa"]["capital_social"] = Decimal(valor)
    assert serpro.basica(ficha, MATRIZ)["capitalSocial"] == centavos


def test_datas_saem_em_iso(ficha):
    r = serpro.basica(ficha, MATRIZ)
    assert r["dataAbertura"] == "2010-03-01"
    assert r["situacaoCadastral"]["data"] == "2020-05-10"


def test_endereco_vem_decomposto(ficha):
    """O motivo de `consultar()` ter parado de formatar."""
    e = serpro.basica(ficha, MATRIZ)["endereco"]

    assert e["tipoLogradouro"] == "RUA"
    assert e["logradouro"] == "DAS FLORES"
    assert e["numero"] == "100"
    assert e["municipio"] == {"codigo": "7107", "descricao": "SAO PAULO"}


def test_municipio_jurisdicao_sai_nulo_e_presente(ficha):
    """Não existe nos dados abertos. Presente e nulo não quebra cliente que
    espera a chave; ausente quebraria."""
    e = serpro.basica(ficha, MATRIZ)["endereco"]
    assert "municipioJurisdicao" in e
    assert e["municipioJurisdicao"] is None


def test_telefone_e_lista_de_objetos(ficha):
    assert serpro.basica(ficha, MATRIZ)["telefone"] == [
        {"ddd": "11", "numero": "30001000"}
    ]


def test_telefone_usa_o_segundo_quando_o_primeiro_falta(ficha):
    assert serpro.basica(ficha, FILIAL)["telefone"] == [
        {"ddd": "11", "numero": "40004000"}
    ]


def test_cnaes_secundarias_resolvidas(ficha):
    assert serpro.basica(ficha, MATRIZ)["cnaeSecundarias"] == [
        {"codigo": "4721102", "descricao": "Padaria e confeitaria"},
        {"codigo": "4729699", "descricao": "Comércio de produtos alimentícios"},
    ]


def test_sem_cnae_secundaria_devolve_lista_vazia(ficha):
    assert serpro.basica(ficha, FILIAL)["cnaeSecundarias"] == []


def test_tipo_estabelecimento_vem_do_identificador(ficha):
    assert serpro.basica(ficha, MATRIZ)["tipoEstabelecimento"] == "1"
    assert serpro.basica(ficha, FILIAL)["tipoEstabelecimento"] == "2"


def test_situacao_especial_preservada(ficha):
    r = serpro.basica(ficha, FILIAL)
    assert r["situacaoEspecial"] == "EM LIQUIDACAO"
    assert r["dataSituacaoEspecial"] == "2022-12-01"


def test_dominio_com_codigo_orfao_mantem_o_codigo(ficha):
    """Código órfão existe na base — 18.253 só em motivo. O campo não pode
    sumir por causa de uma descrição ausente."""
    ficha["empresa"]["natureza_juridica_descricao"] = None

    n = serpro.basica(ficha, MATRIZ)["naturezaJuridica"]

    assert n == {"codigo": "2062", "descricao": None}


def test_periodos_do_simples_saem_como_lista(ficha):
    ia = serpro.basica(ficha, MATRIZ)["informacoesAdicionais"]
    assert ia["optanteSimples"] == "S"
    assert ia["listaPeriodosSimples"] == [
        {"dataInicio": "2010-04-01", "dataFim": None}
    ]


def test_sem_registro_no_simples_nao_quebra(ficha):
    ficha["simples"] = None
    ia = serpro.basica(ficha, MATRIZ)["informacoesAdicionais"]
    assert ia == {"optanteSimples": None, "optanteMei": None,
                  "listaPeriodosSimples": []}


# ---------------------------------------------------------------------------
# os três recortes
# ---------------------------------------------------------------------------

def test_basica_nao_traz_socios(ficha):
    assert "socios" not in serpro.basica(ficha, MATRIZ)


def test_qsa_traz_so_o_quadro(ficha):
    r = serpro.qsa(ficha, MATRIZ)

    assert set(r) == {"ni", "nomeEmpresarial", "socios"}
    assert r["socios"][0]["nome"] == "MARIA DA SILVA"


def test_qsa_valida_o_estabelecimento(ficha):
    """Sem isto, um `ni` inexistente devolveria os sócios da empresa inteira."""
    with pytest.raises(EstabelecimentoNaoEncontrado):
        serpro.qsa(ficha, "11222333000999")


def test_empresa_traz_cadastro_e_socios(ficha):
    r = serpro.empresa(ficha, MATRIZ)

    assert r["nomeEmpresarial"] == "ACME INDUSTRIA LTDA"
    assert len(r["socios"]) == 1


def test_cpf_do_socio_sai_como_a_fonte_manda(ficha):
    """Mascarado. É incompatibilidade de fonte, não defeito — mas tem que
    aparecer no campo certo, não sumir."""
    assert serpro.qsa(ficha, MATRIZ)["socios"][0]["cpf"] == "***794780**"


def test_socio_sem_representante_legal(ficha):
    assert serpro.qsa(ficha, MATRIZ)["socios"][0]["representanteLegal"] is None


def test_socio_com_representante_legal(ficha):
    ficha["socios"][0].update(
        representante_legal="***111222**", nome_representante="JOAO SOUZA",
        qualificacao_repres_legal=5,
    )

    rep = serpro.qsa(ficha, MATRIZ)["socios"][0]["representanteLegal"]

    assert rep == {"cpf": "***111222**", "nome": "JOAO SOUZA",
                   "qualificacao": "05"}


# ---------------------------------------------------------------------------
# contra deriva entre os módulos
# ---------------------------------------------------------------------------

def test_a_ficha_do_fixture_tem_as_chaves_que_consultar_produz():
    """O fixture é montado à mão. Se `consultar()` renomear um campo, os testes
    acima continuariam passando sobre uma forma que não existe mais — este
    teste é o que impede isso.
    """
    from src.consulta import empresa as consulta

    fonte = Path(consulta.__file__).read_text(encoding="utf-8")
    for chave in ("cnpj_ordem", "cnpj_dv", "identificador_matriz_filial",
                  "tipo_logradouro", "numero", "situacao_especial",
                  "data_situacao_especial", "ddd_2", "telefone_2",
                  "pais_descricao", "municipio_descricao"):
        assert f'"{chave}"' in fonte, f"consultar() não produz mais {chave}"
