"""Testes das rotas — pública e `/manager`.

Rodam com `TestClient`, sem servidor e sem Firebird: `consultar` é injetada. A
consulta real já é exercitada noutros testes e contra a base; o que estas rotas
acrescentam é o encanamento — autenticação, códigos de retorno, log — e é isso
que se testa aqui.

O teste que mais importa é o último da primeira seção: **o `/manager` não pode
existir na aplicação pública**. Se alguém montar as duas no mesmo app por
conveniência, a separação de portas vira decoração.
"""

import base64
import json
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.api import manager, publica  # noqa: E402
from src.api.config import ConfigAPI  # noqa: E402
from src.api.credenciais import Credenciais  # noqa: E402

NI = "11222333000181"
NI_INEXISTENTE = "11222333000262"          # DV válido, estabelecimento ausente
NI_DV_ERRADO = "11222333000182"


def _ficha():
    return {
        "cnpj_basico": "11222333",
        "empresa": {
            "cnpj_basico": "11222333", "razao_social": "ACME LTDA",
            "natureza_juridica": 2062, "natureza_juridica_descricao": "LTDA",
            "qualificacao_responsavel": 49, "qualificacao_responsavel_descricao": "Sócio",
            "capital_social": Decimal("1000.00"), "porte_empresa": 3,
            "porte_empresa_descricao": "EPP", "ente_federativo_responsavel": None,
        },
        "estabelecimentos": [{
            "cnpj_basico": "11222333", "cnpj_ordem": "0001", "cnpj_dv": "81",
            "cnpj_completo": NI, "identificador_matriz_filial": 1, "tipo": "MATRIZ",
            "nome_fantasia": "ACME", "situacao_cadastral": 2,
            "situacao_cadastral_descricao": "ATIVA",
            "data_situacao_cadastral": date(2020, 1, 1),
            "motivo_situacao_cadastral": 0, "motivo_descricao": None,
            "situacao_especial": None, "data_situacao_especial": None,
            "data_inicio_atividade": date(2010, 1, 1),
            "cnae_fiscal_principal": 1091101, "cnae_principal_descricao": "Padaria",
            "cnae_fiscal_secundaria": None,
            "tipo_logradouro": "RUA", "logradouro": "A", "numero": "1",
            "complemento": None, "bairro": "CENTRO", "cep": "01001000", "uf": "SP",
            "municipio": 7107, "municipio_descricao": "SAO PAULO",
            "pais": None, "pais_descricao": None,
            "ddd_1": "11", "telefone_1": "30001000",
            "ddd_2": None, "telefone_2": None, "correio_eletronico": None,
        }],
        "cnaes_secundarios": {},
        "socios": [{
            "identificador_socio": 2, "tipo": "PESSOA FÍSICA",
            "nome_socio": "MARIA", "cnpj_cpf_socio": "***794780**",
            "qualificacao_socio": 49, "qualificacao_descricao": "Sócio",
            "data_entrada_sociedade": date(2010, 1, 1),
            "pais": None, "pais_descricao": None, "faixa_etaria": 5,
            "faixa_etaria_descricao": "41 a 50", "nome_representante": None,
            "representante_legal": None, "qualificacao_repres_legal": None,
        }],
        "simples": None,
    }


@pytest.fixture
def cfg(tmp_path):
    return ConfigAPI(
        credenciais=tmp_path / "creds" / "credenciais.db",
        logs=tmp_path / "logs",
        estatisticas=tmp_path / "creds" / "estatisticas.db",
        jwt_segredo="segredo-de-teste",
    )


@pytest.fixture
def ambiente(cfg):
    cofre = Credenciais(cfg.credenciais)
    cred, secret = cofre.criar("ACME Ltda", quota_mensal=None)

    app = publica.criar_app(cfg, consultar=lambda basico: _ficha())
    cliente = TestClient(app, raise_server_exceptions=False)
    return {"cfg": cfg, "cofre": cofre, "cliente": cliente,
            "key": cred.consumer_key, "secret": secret}


def _basic(chave: str, segredo: str) -> dict:
    b = base64.b64encode(f"{chave}:{segredo}".encode()).decode()
    return {"Authorization": f"Basic {b}"}


def _token(amb) -> str:
    r = amb["cliente"].post("/token", headers=_basic(amb["key"], amb["secret"]),
                            data={"grant_type": "client_credentials"})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _bearer(amb) -> dict:
    return {"Authorization": f"Bearer {_token(amb)}"}


# ---------------------------------------------------------------------------
# separação das aplicações
# ---------------------------------------------------------------------------

def test_a_aplicacao_publica_nao_tem_manager(ambiente):
    """A barreira é o processo separado. Se o /manager existir aqui, a
    separação de portas vira decoração."""
    caminhos = {r.path for r in ambiente["cliente"].app.routes}
    assert not any(c.startswith("/manager") for c in caminhos), caminhos


def test_manager_nao_tem_as_rotas_publicas(cfg):
    caminhos = {r.path for r in manager.criar_app(cfg).routes}
    assert not any(c.startswith("/v2/") for c in caminhos)
    assert "/token" not in caminhos


# ---------------------------------------------------------------------------
# /token
# ---------------------------------------------------------------------------

def test_token_no_formato_do_serpro(ambiente):
    r = ambiente["cliente"].post("/token", headers=_basic(ambiente["key"], ambiente["secret"]),
                                 data={"grant_type": "client_credentials"})

    assert r.status_code == 200
    corpo = r.json()
    assert set(corpo) == {"access_token", "token_type", "expires_in", "scope"}
    assert corpo["token_type"] == "Bearer"
    assert corpo["expires_in"] == 3600
    assert corpo["scope"] == "default"


def test_token_recusa_segredo_errado(ambiente):
    r = ambiente["cliente"].post("/token", headers=_basic(ambiente["key"], "errado"),
                                 data={"grant_type": "client_credentials"})
    assert r.status_code == 401


def test_token_recusa_sem_authorization(ambiente):
    r = ambiente["cliente"].post("/token", data={"grant_type": "client_credentials"})
    assert r.status_code == 401


def test_token_recusa_credencial_revogada(ambiente):
    ambiente["cofre"].revogar(ambiente["key"])

    r = ambiente["cliente"].post("/token", headers=_basic(ambiente["key"], ambiente["secret"]),
                                 data={"grant_type": "client_credentials"})
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# consultas
# ---------------------------------------------------------------------------

def test_basica_responde_no_esquema_do_serpro(ambiente):
    r = ambiente["cliente"].get(f"/v2/basica/{NI}", headers=_bearer(ambiente))

    assert r.status_code == 200
    corpo = r.json()
    assert corpo["ni"] == NI
    assert corpo["nomeEmpresarial"] == "ACME LTDA"
    assert corpo["capitalSocial"] == 100000
    assert "socios" not in corpo


def test_qsa_traz_o_quadro(ambiente):
    r = ambiente["cliente"].get(f"/v2/qsa/{NI}", headers=_bearer(ambiente))

    assert r.status_code == 200
    assert r.json()["socios"][0]["nome"] == "MARIA"


def test_empresa_traz_cadastro_e_socios(ambiente):
    r = ambiente["cliente"].get(f"/v2/empresa/{NI}", headers=_bearer(ambiente))

    assert r.status_code == 200
    assert r.json()["nomeEmpresarial"] == "ACME LTDA"
    assert len(r.json()["socios"]) == 1


def test_dv_errado_da_400(ambiente):
    """400 e 404 são casos diferentes — e 404 é faturável, 400 não."""
    r = ambiente["cliente"].get(f"/v2/basica/{NI_DV_ERRADO}", headers=_bearer(ambiente))

    assert r.status_code == 400
    assert "não é válido" in r.json()["message"]


def test_cnpj_valido_sem_estabelecimento_da_404(ambiente):
    r = ambiente["cliente"].get(f"/v2/basica/{NI_INEXISTENTE}", headers=_bearer(ambiente))

    assert r.status_code == 404
    assert "Nenhum registro" in r.json()["message"]


def test_consulta_sem_token_da_401(ambiente):
    assert ambiente["cliente"].get(f"/v2/basica/{NI}").status_code == 401


def test_consulta_com_token_forjado_da_401(ambiente):
    forjado = "eyJhbGciOiJub25lIn0.eyJzdWIiOiJYIiwiZXhwIjo5OTk5OTk5OTk5fQ."
    r = ambiente["cliente"].get(f"/v2/basica/{NI}",
                                headers={"Authorization": f"Bearer {forjado}"})
    assert r.status_code == 401


def test_token_de_credencial_revogada_para_de_valer(ambiente):
    """O token é autocontido e valeria até expirar. Como o armazenamento já
    está no caminho quente por causa do log, conferir o estado não custa nada
    novo — e é o que torna a revogação imediata."""
    cabecalho = _bearer(ambiente)
    assert ambiente["cliente"].get(f"/v2/basica/{NI}", headers=cabecalho).status_code == 200

    ambiente["cofre"].revogar(ambiente["key"])

    assert ambiente["cliente"].get(f"/v2/basica/{NI}", headers=cabecalho).status_code == 401


# ---------------------------------------------------------------------------
# quota
# ---------------------------------------------------------------------------

def test_quota_estourada_da_403(cfg):
    """403 e não 429: a documentação do SERPRO descreve 403 como acesso negado
    por 'contract restrictions', e 429 introduziria um código que um cliente
    escrito para eles não espera."""
    cofre = Credenciais(cfg.credenciais)
    cred, secret = cofre.criar("ACME", quota_mensal=2)
    app = publica.criar_app(cfg, consultar=lambda b: _ficha())
    cliente = TestClient(app, raise_server_exceptions=False)
    amb = {"cliente": cliente, "key": cred.consumer_key, "secret": secret}

    for _ in range(2):
        assert cliente.get(f"/v2/basica/{NI}", headers=_bearer(amb)).status_code == 200

    # Consolida o que foi gravado hoje, para a quota enxergar o consumo.
    cliente.app.state.estatisticas.consolidar(cfg.logs, incluir_hoje=True)

    r = cliente.get(f"/v2/basica/{NI}", headers=_bearer(amb))
    assert r.status_code == 403
    assert "Quota" in r.json()["message"]


# ---------------------------------------------------------------------------
# log
# ---------------------------------------------------------------------------

def test_consulta_bem_sucedida_e_registrada(ambiente):
    ambiente["cliente"].get(f"/v2/basica/{NI}", headers=_bearer(ambiente))

    linhas = _linhas_do_log(ambiente["cfg"])
    assert len(linhas) == 1
    assert linhas[0]["rota"] == "/v2/basica"
    assert linhas[0]["status_http"] == 200
    assert linhas[0]["faturavel"] is True
    assert linhas[0]["ni"] == NI


def test_erro_do_cliente_e_registrado_como_nao_faturavel(ambiente):
    """Sem registrar o erro não haveria como distinguir consulta prestada de
    requisição malformada na estatística."""
    ambiente["cliente"].get(f"/v2/basica/{NI_DV_ERRADO}", headers=_bearer(ambiente))

    linha = _linhas_do_log(ambiente["cfg"])[0]
    assert linha["status_http"] == 400
    assert linha["faturavel"] is False


def test_404_e_registrado_como_faturavel(ambiente):
    ambiente["cliente"].get(f"/v2/basica/{NI_INEXISTENTE}", headers=_bearer(ambiente))

    linha = _linhas_do_log(ambiente["cfg"])[0]
    assert linha["status_http"] == 404
    assert linha["faturavel"] is True


def _linhas_do_log(cfg) -> list[dict]:
    linhas = []
    for arq in sorted(Path(cfg.logs).glob("consultas-*.jsonl")):
        linhas += [json.loads(l) for l in arq.read_text(encoding="utf-8").splitlines() if l.strip()]
    return linhas


# ---------------------------------------------------------------------------
# /manager
# ---------------------------------------------------------------------------

@pytest.fixture
def gerente(cfg):
    return TestClient(manager.criar_app(cfg), raise_server_exceptions=False)


def test_incluir_devolve_o_segredo_uma_vez(gerente):
    r = gerente.post("/manager/credenciais",
                     json={"contratante_nome": "ACME Ltda", "quota_mensal": 1000})

    assert r.status_code == 201
    corpo = r.json()
    assert corpo["consumerSecret"]
    assert corpo["status"] == "ativa"

    # A consulta posterior NÃO traz o segredo.
    chave = corpo["consumerKey"]
    assert "consumerSecret" not in gerente.get(f"/manager/credenciais/{chave}").json()


def test_listar_e_filtrar(gerente):
    a = gerente.post("/manager/credenciais", json={"contratante_nome": "A"}).json()
    gerente.post("/manager/credenciais", json={"contratante_nome": "B"})
    gerente.delete(f"/manager/credenciais/{a['consumerKey']}")

    assert len(gerente.get("/manager/credenciais").json()["credenciais"]) == 2
    assert len(gerente.get("/manager/credenciais?status=ativa").json()["credenciais"]) == 1


def test_alterar(gerente):
    chave = gerente.post("/manager/credenciais", json={"contratante_nome": "A"}).json()["consumerKey"]

    r = gerente.patch(f"/manager/credenciais/{chave}",
                      json={"quota_mensal": 500, "contratante_email": "a@b.com"})

    assert r.status_code == 200
    assert r.json()["quotaMensal"] == 500
    assert r.json()["contratante"]["email"] == "a@b.com"


def test_revogar_marca_e_preserva(gerente):
    chave = gerente.post("/manager/credenciais", json={"contratante_nome": "A"}).json()["consumerKey"]

    r = gerente.delete(f"/manager/credenciais/{chave}")

    assert r.status_code == 200
    assert r.json()["status"] == "revogada"
    assert r.json()["revogadaEm"]
    assert gerente.get(f"/manager/credenciais/{chave}").status_code == 200


def test_rotacionar_devolve_segredo_novo(gerente):
    criada = gerente.post("/manager/credenciais", json={"contratante_nome": "A"}).json()

    r = gerente.post(f"/manager/credenciais/{criada['consumerKey']}/rotacionar")

    assert r.status_code == 200
    assert r.json()["consumerSecret"] != criada["consumerSecret"]


def test_credencial_inexistente_da_404(gerente):
    assert gerente.get("/manager/credenciais/naoexiste").status_code == 404
    assert gerente.delete("/manager/credenciais/naoexiste").status_code == 404
    assert gerente.patch("/manager/credenciais/naoexiste",
                         json={"quota_mensal": 1}).status_code == 404


def test_estatisticas_por_credencial_trazem_a_quota(cfg, gerente):
    cofre = Credenciais(cfg.credenciais)
    cred, secret = cofre.criar("ACME", quota_mensal=10)
    app = publica.criar_app(cfg, consultar=lambda b: _ficha())
    cliente = TestClient(app, raise_server_exceptions=False)
    amb = {"cliente": cliente, "key": cred.consumer_key, "secret": secret}
    for _ in range(3):
        cliente.get(f"/v2/basica/{NI}", headers=_bearer(amb))

    gerente.post("/manager/manutencao/consolidar?incluir_hoje=true")
    r = gerente.get(f"/manager/estatisticas/{cred.consumer_key}")

    assert r.status_code == 200
    assert r.json()["consultas"] == 3
    assert r.json()["quotaMensal"] == 10
    assert r.json()["percentualDaQuota"] == 30.0


def test_estatisticas_gerais(cfg, gerente):
    cofre = Credenciais(cfg.credenciais)
    cred, secret = cofre.criar("ACME")
    app = publica.criar_app(cfg, consultar=lambda b: _ficha())
    cliente = TestClient(app, raise_server_exceptions=False)
    amb = {"cliente": cliente, "key": cred.consumer_key, "secret": secret}
    cliente.get(f"/v2/basica/{NI}", headers=_bearer(amb))
    cliente.get(f"/v2/qsa/{NI}", headers=_bearer(amb))

    gerente.post("/manager/manutencao/consolidar?incluir_hoje=true")
    r = gerente.get("/manager/estatisticas")

    assert r.json()["consultas"] == 2
    assert r.json()["porRota"] == {"/v2/basica": 1, "/v2/qsa": 1}


def test_podar_sem_dias_recusa(gerente):
    """Não há janela de retenção automática: apagar histórico é sempre ato
    deliberado com o prazo dito na chamada. Assumir um padrão aqui apagaria
    log por engano, e isso não tem desfazer."""
    r = gerente.post("/manager/manutencao/podar")

    assert r.status_code == 400
    assert "dias" in r.json()["detail"]


def test_podar_com_dias_explicito_funciona(gerente):
    r = gerente.post("/manager/manutencao/podar?dias=30")

    assert r.status_code == 200
    assert r.json() == {"arquivosApagados": 0}
