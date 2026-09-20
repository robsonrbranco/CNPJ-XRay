"""Testes do OpenAPI gerado.

Documentação que ninguém confere apodrece em silêncio: a rota muda, o `/docs`
continua descrevendo o que havia antes, e o consumidor confia. Estes testes
tratam a especificação como parte do contrato.

O caso que mais importa é o do 422. O FastAPI o injeta em toda rota com
parâmetro, e esta API **nunca** o devolve — documentá-lo mandaria o cliente
tratar um caso inexistente, quebrando a promessa de que só aparecem os códigos
do SERPRO.
"""

import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.api import manager, publica  # noqa: E402
from src.api.config import ConfigAPI  # noqa: E402

CODIGOS_SERPRO = {"200", "400", "401", "403", "404", "500"}
CONSULTAS = ("/v2/basica/{ni}", "/v2/qsa/{ni}", "/v2/empresa/{ni}")


@pytest.fixture
def spec(tmp_path):
    cfg = ConfigAPI(credenciais=tmp_path / "c.db", logs=tmp_path / "l",
                    estatisticas=tmp_path / "e.db", jwt_segredo="x")
    return publica.criar_app(cfg, consultar=lambda b: {}).openapi()


def test_as_tres_rotas_do_serpro_estao_documentadas(spec):
    for caminho in CONSULTAS:
        assert caminho in spec["paths"], caminho
    assert "/token" in spec["paths"]


def test_consultas_documentam_exatamente_os_codigos_do_serpro(spec):
    for caminho in CONSULTAS:
        codigos = set(spec["paths"][caminho]["get"]["responses"])
        assert codigos == CODIGOS_SERPRO, f"{caminho}: {sorted(codigos)}"


def test_o_422_do_fastapi_nao_vaza(spec):
    """O FastAPI injeta 422 em toda rota com parâmetro. Esta API não o devolve:
    `ni` é string e não há validação que possa falhar antes do handler."""
    for caminho, ops in spec["paths"].items():
        for metodo, op in ops.items():
            assert "422" not in op["responses"], f"{metodo} {caminho}"


def test_os_modelos_de_validacao_do_fastapi_nao_vazam(spec):
    schemas = spec["components"]["schemas"]
    assert "HTTPValidationError" not in schemas
    assert "ValidationError" not in schemas


def test_o_200_aponta_para_um_modelo_de_verdade(spec):
    """Sem `response_model` o schema do 200 sai como `{}` — rota listada e
    contrato nenhum, que foi o estado inicial."""
    esperado = {"/v2/basica/{ni}": "Basica", "/v2/qsa/{ni}": "QSA",
                "/v2/empresa/{ni}": "Empresa"}
    for caminho, modelo in esperado.items():
        ref = spec["paths"][caminho]["get"]["responses"]["200"]["content"] \
                  ["application/json"]["schema"]["$ref"]
        assert ref.endswith(f"/{modelo}"), f"{caminho} -> {ref}"


def test_os_esquemas_de_seguranca_estao_declarados(spec):
    schemes = spec["components"]["securitySchemes"]
    assert "HTTPBasic" in schemes
    assert "HTTPBearer" in schemes


def test_campos_do_contrato_presentes_no_modelo(spec):
    """Os nomes são do SERPRO, inclusive o camelCase. Um erro aqui só
    apareceria no cliente."""
    basica = spec["components"]["schemas"]["Basica"]["properties"]
    for campo in ("ni", "tipoEstabelecimento", "nomeEmpresarial", "nomeFantasia",
                  "dataAbertura", "situacaoCadastral", "naturezaJuridica",
                  "capitalSocial", "porte", "endereco", "telefone",
                  "correioEletronico", "cnaePrincipal", "cnaeSecundarias",
                  "informacoesAdicionais", "situacaoEspecial",
                  "dataSituacaoEspecial"):
        assert campo in basica, campo


def test_endereco_traz_os_campos_decompostos(spec):
    """O motivo de `consultar()` ter parado de formatar."""
    endereco = spec["components"]["schemas"]["Endereco"]["properties"]
    for campo in ("tipoLogradouro", "logradouro", "numero", "municipio",
                  "municipioJurisdicao"):
        assert campo in endereco, campo


def test_capital_social_documenta_que_e_em_centavos(spec):
    """Sem isto o consumidor divide por 100 duas vezes, ou nenhuma."""
    d = spec["components"]["schemas"]["Basica"]["properties"]["capitalSocial"]
    assert "CENTAVOS" in d.get("description", "").upper()


def test_cpf_do_socio_documenta_que_vem_mascarado(spec):
    """É a incompatibilidade que mais gera chamado: o campo existe, tem o mesmo
    nome do SERPRO, e o conteúdo é parcial."""
    d = spec["components"]["schemas"]["Socio"]["properties"]["cpf"]
    assert "MASCARADO" in d.get("description", "").upper()


def test_a_descricao_avisa_que_a_base_e_mensal(spec):
    assert "mensal" in spec["info"]["description"].lower()


def test_manager_nao_aparece_na_especificacao_publica(spec):
    """Mesma garantia do teste de rotas, agora na documentação: o `/docs`
    público não pode revelar a superfície administrativa."""
    assert not any(c.startswith("/manager") for c in spec["paths"])


# ---------------------------------------------------------------------------
# /manager
# ---------------------------------------------------------------------------

@pytest.fixture
def spec_manager(tmp_path):
    cfg = ConfigAPI(credenciais=tmp_path / "c.db", logs=tmp_path / "l",
                    estatisticas=tmp_path / "e.db", jwt_segredo="x")
    return manager.criar_app(cfg).openapi()


def test_manager_documenta_as_proprias_rotas(spec_manager):
    for caminho in ("/manager/credenciais", "/manager/credenciais/{chave}",
                    "/manager/credenciais/{chave}/rotacionar",
                    "/manager/estatisticas", "/manager/estatisticas/{chave}",
                    "/manager/manutencao/consolidar",
                    "/manager/manutencao/podar"):
        assert caminho in spec_manager["paths"], caminho
    assert not any(c.startswith("/v2/") for c in spec_manager["paths"])


def test_toda_resposta_de_sucesso_do_manager_tem_modelo(spec_manager):
    """Sem modelo o schema sai `{}` — rota listada e contrato nenhum."""
    for caminho, ops in spec_manager["paths"].items():
        for metodo, op in ops.items():
            sucesso = [c for c in op["responses"] if c.startswith("2")]
            assert sucesso, f"{metodo} {caminho} sem resposta de sucesso"
            schema = op["responses"][sucesso[0]]["content"]["application/json"]["schema"]
            assert "$ref" in schema, f"{metodo} {caminho}: schema vazio"


def test_manager_documenta_o_404_onde_ele_ocorre(spec_manager):
    """Rotas que recebem uma chave podem não achar a credencial."""
    for caminho in ("/manager/credenciais/{chave}",
                    "/manager/credenciais/{chave}/rotacionar",
                    "/manager/estatisticas/{chave}"):
        for op in spec_manager["paths"][caminho].values():
            assert "404" in op["responses"], caminho


def test_o_422_do_manager_e_legitimo_e_permanece(spec_manager):
    """Diferente da API pública: aqui os corpos são modelos Pydantic com
    validação — `contratante_nome` tem tamanho mínimo e `quota_mensal` não
    aceita negativo. Corpo inválido devolve 422 de verdade, então documentá-lo
    é honesto. Na pública era ficção do framework."""
    op = spec_manager["paths"]["/manager/credenciais"]["post"]
    assert "422" in op["responses"]


def test_o_segredo_documenta_que_aparece_uma_vez(spec_manager):
    """É a diferença entre o operador guardar o valor e perdê-lo."""
    for modelo in ("CredencialCriada", "SegredoRotacionado"):
        d = spec_manager["components"]["schemas"][modelo]["properties"]["consumerSecret"]
        assert "UMA ÚNICA VEZ" in d.get("description", "").upper()


def test_a_descricao_avisa_que_nao_deve_ser_exposta(spec_manager):
    """A barreira do /manager é isolamento de rede. Quem publicar a porta anula
    a decisão inteira, e a documentação tem que dizer isso."""
    assert "não deve ser exposta" in spec_manager["info"]["description"]


def test_revogar_documenta_que_nao_apaga(spec_manager):
    d = spec_manager["paths"]["/manager/credenciais/{chave}"]["delete"]
    assert "não apaga" in d.get("description", "").lower()


def test_podar_documenta_que_dias_e_obrigatorio(spec_manager):
    d = spec_manager["paths"]["/manager/manutencao/podar"]["post"]
    assert "retenção automática" in d.get("description", "")
