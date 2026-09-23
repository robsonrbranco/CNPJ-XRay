"""A camada MCP (`POST /mcp`), de ponta a ponta, sem Firebird e sem rede.

Porte dos testes do CEP-XRay, onde o piloto foi validado. O que se defende, em
ordem de quanto custaria errar:

1. **As duas portas não se misturam.** O token de 90 dias do MCP não vale nas
   rotas REST, e o token de uma hora do REST não vale no MCP.
2. **Revogar e rotacionar derrubam o token de longa duração.**
3. **A cobrança bate com a do REST** — inclusive o CNPJ em `ni`, que aqui, ao
   contrário do CEP, é registrado de propósito — e `validar_cnpj` não é cobrado.
4. **Sócio é dado pessoal**: só vai com `incluir_socios`.
5. O protocolo.
"""

import base64
import copy
import json
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from src.api import token as mod_token  # noqa: E402
from src.api.config import ConfigAPI  # noqa: E402
from src.api.credenciais import Credenciais  # noqa: E402
from src.api.manager import criar_app as criar_manager  # noqa: E402
from src.api.publica import criar_app  # noqa: E402

URI = "https://themis.ecomciencia.com/mcp"
NI = "11222333000181"
NI_ESTAB_AUSENTE = "11222333000262"       # DV válido, estabelecimento ausente
NI_DV_ERRADO = "11222333000182"
NI_EMPRESA_AUSENTE = "11444777000161"      # DV válido, empresa inexistente
# CNPJ alfanumérico: o exemplo do manual de DV da Receita.
NI_ALFA = "12ABC34501DE35"
NI_ALFA_AUSENTE = "12ABC34502DE06"         # DV válido, estabelecimento ausente
NI_ALFA_DV_ERRADO = "12ABC34501DE36"


def _ficha():
    return {
        "cnpj_basico": "11222333",
        "empresa": {
            "cnpj_basico": "11222333", "razao_social": "ACME LTDA",
            "natureza_juridica": 2062, "natureza_juridica_descricao": "LTDA",
            "capital_social": Decimal("532000000000.10"), "porte_empresa": 3,
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
            "cnae_fiscal_secundaria": "4721102",
            "tipo_logradouro": "RUA", "logradouro": "A", "numero": "1",
            "complemento": None, "bairro": "CENTRO", "cep": "01001000", "uf": "SP",
            "municipio": 7107, "municipio_descricao": "SAO PAULO",
            "pais": None, "pais_descricao": None,
            "ddd_1": "11", "telefone_1": "30001000",
            "ddd_2": None, "telefone_2": None, "correio_eletronico": None,
        }],
        "cnaes_secundarios": {4721102: "Padaria e confeitaria"},
        "socios": [{
            "identificador_socio": 2, "tipo": "PESSOA FÍSICA",
            "nome_socio": "MARIA", "cnpj_cpf_socio": "***794780**",
            "qualificacao_socio": 49, "qualificacao_descricao": "Sócio-Administrador",
            "data_entrada_sociedade": date(2010, 1, 1),
            "pais": None, "pais_descricao": None, "faixa_etaria": 5,
            "faixa_etaria_descricao": "41 a 50", "nome_representante": None,
            "representante_legal": None, "qualificacao_repres_legal": None,
        }],
        "simples": {"opcao_pelo_simples": "S", "opcao_mei": "N",
                    "data_opcao_simples": date(2011, 1, 1),
                    "data_exclusao_simples": None},
    }


def _ficha_alfa():
    f = copy.deepcopy(_ficha())
    f["cnpj_basico"] = f["empresa"]["cnpj_basico"] = "12ABC345"
    f["empresa"]["razao_social"] = "ALFA LTDA"
    f["estabelecimentos"][0].update(cnpj_basico="12ABC345", cnpj_ordem="01DE",
                                    cnpj_dv="35", cnpj_completo=NI_ALFA)
    return f


class Base:
    def __init__(self):
        self.falhar = False
        self.leituras_metadados = 0
        self.consultados = []

    def consultar(self, basico):
        if self.falhar:
            raise RuntimeError("attachment perdido")
        self.consultados.append(basico)
        if basico == NI_EMPRESA_AUSENTE[:8]:
            return {"empresa": None, "estabelecimentos": [], "socios": [],
                    "cnaes_secundarios": {}, "simples": None}
        if basico == NI_ALFA[:8]:
            return _ficha_alfa()
        return _ficha()

    def metadados(self):
        self.leituras_metadados += 1
        return {"competencia": "2026-09", "construida_em": "2026-09-17T16:00:00+00:00",
                "linhas_total": "222197006"}


@pytest.fixture
def amb(tmp_path):
    cfg = ConfigAPI(
        credenciais=tmp_path / "creds" / "credenciais.db",
        logs=tmp_path / "logs",
        estatisticas=tmp_path / "creds" / "estatisticas.db",
        jwt_segredo="segredo-de-teste",
        mcp_uri=URI,
    )
    creds = Credenciais(cfg.credenciais)
    cred, segredo = creds.criar(contratante_nome="Agente de teste")
    base = Base()
    api = TestClient(criar_app(cfg, consultar=base.consultar, metadados=base.metadados),
                     raise_server_exceptions=False)
    manager = TestClient(criar_manager(cfg), raise_server_exceptions=False)

    class Amb:
        pass

    a = Amb()
    a.cfg, a.creds, a.cred, a.segredo = cfg, creds, cred, segredo
    a.base, a.api, a.manager = base, api, manager
    return a


def _token_mcp(a, dias=90):
    r = a.manager.post(f"/manager/credenciais/{a.cred.consumer_key}/token-mcp",
                       params={"dias": dias})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def _cab(tok, **extra):
    h = {"Authorization": f"Bearer {tok}", "Accept": "application/json, text/event-stream"}
    h.update(extra)
    return h


def _rpc(a, tok, metodo, params=None, id_=1, **cab):
    corpo = {"jsonrpc": "2.0", "id": id_, "method": metodo}
    if params is not None:
        corpo["params"] = params
    return a.api.post("/mcp", headers=_cab(tok, **cab), json=corpo)


def _chamar(a, tok, nome, args):
    r = _rpc(a, tok, "tools/call", {"name": nome, "arguments": args})
    assert r.status_code == 200, r.text
    return r.json()["result"]


def _log(a):
    linhas = []
    for f in sorted(a.cfg.logs.glob("consultas-*.jsonl")):
        linhas += [json.loads(l) for l in f.read_text(encoding="utf-8").splitlines()]
    return linhas


def _token_rest(a):
    b = base64.b64encode(f"{a.cred.consumer_key}:{a.segredo}".encode()).decode()
    r = a.api.post("/token", headers={"Authorization": f"Basic {b}"},
                   data={"grant_type": "client_credentials"})
    assert r.status_code == 200
    return r.json()["access_token"]


# -- 1. as duas portas não se misturam --------------------------------------

def test_token_mcp_nao_vale_nas_rotas_rest(amb):
    r = amb.api.get(f"/v2/basica/{NI}",
                    headers={"Authorization": f"Bearer {_token_mcp(amb)}"})
    assert r.status_code == 401


def test_token_rest_nao_vale_no_mcp(amb):
    r = _rpc(amb, _token_rest(amb), "tools/list")
    assert r.status_code == 401
    assert "WWW-Authenticate" in r.headers


def test_token_do_hestia_nao_vale_no_themis(amb):
    """Mesmo formato, outra audiência: um token não atravessa de serviço."""
    geracao = amb.creds.geracao(amb.cred.consumer_key)
    tok, _ = mod_token.emitir(amb.cred.consumer_key, amb.cfg.jwt_segredo,
                              audiencia="https://hestia.ecomciencia.com/mcp",
                              geracao=geracao)
    assert _rpc(amb, tok, "tools/list").status_code == 401


def test_sem_token_da_401(amb):
    r = amb.api.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert r.status_code == 401


def test_o_token_rest_continua_funcionando(amb):
    r = amb.api.get(f"/v2/basica/{NI}",
                    headers={"Authorization": f"Bearer {_token_rest(amb)}"})
    assert r.status_code == 200


# -- 2. revogar e rotacionar -------------------------------------------------

def test_revogar_derruba_o_token_mcp(amb):
    tok = _token_mcp(amb)
    assert _rpc(amb, tok, "ping").status_code == 200
    amb.manager.delete(f"/manager/credenciais/{amb.cred.consumer_key}")
    assert _rpc(amb, tok, "ping").status_code == 401


def test_rotacionar_o_segredo_derruba_o_token_mcp(amb):
    tok = _token_mcp(amb)
    amb.manager.post(f"/manager/credenciais/{amb.cred.consumer_key}/rotacionar")
    r = _rpc(amb, tok, "ping")
    assert r.status_code == 401
    assert "rotacionado" in r.json()["message"]
    assert _rpc(amb, _token_mcp(amb), "ping").status_code == 200


def test_token_expirado_e_recusado(amb):
    geracao = amb.creds.geracao(amb.cred.consumer_key)
    tok, _ = mod_token.emitir(amb.cred.consumer_key, amb.cfg.jwt_segredo,
                              validade_s=10, agora=1_000_000,
                              audiencia=URI, geracao=geracao)
    assert _rpc(amb, tok, "ping").status_code == 401


def test_manager_emite_token_com_audiencia_e_geracao(amb):
    r = amb.manager.post(f"/manager/credenciais/{amb.cred.consumer_key}/token-mcp",
                         params={"dias": 30})
    claims = mod_token.verificar(r.json()["token"], amb.cfg.jwt_segredo)
    assert claims["aud"] == URI and claims["scope"] == "mcp"
    assert claims["gen"] == amb.creds.geracao(amb.cred.consumer_key)
    assert claims["exp"] - claims["iat"] == 30 * 86400


def test_sem_mcp_configurado_a_rota_nao_existe(tmp_path):
    cfg = ConfigAPI(credenciais=tmp_path / "c.db", logs=tmp_path / "l",
                    estatisticas=tmp_path / "e.db", jwt_segredo="x")
    api = TestClient(criar_app(cfg, consultar=Base().consultar))
    assert api.post("/mcp", json={}).status_code == 404


# -- 3. cobrança -------------------------------------------------------------

def test_consulta_gera_linha_faturavel_com_o_cnpj(amb):
    _chamar(amb, _token_mcp(amb), "situacao_cadastral", {"cnpj": "11.222.333/0001-81"})
    [l] = _log(amb)
    assert l["rota"] == "mcp:situacao_cadastral"
    assert l["status_http"] == 200 and l["faturavel"] is True
    # Como no REST: o CNPJ entra, sem pontuação.
    assert l["ni"] == NI


def test_validar_cnpj_nao_gera_linha_nem_esbarra_na_cota(amb):
    tok = _token_mcp(amb)
    amb.creds.alterar(amb.cred.consumer_key, quota_mensal=0)
    ok = _chamar(amb, tok, "validar_cnpj", {"cnpj": NI})
    assert ok["structuredContent"] == {"cnpj": NI, "valido": True,
                                       "formatado": "11.222.333/0001-81"}
    ruim = _chamar(amb, tok, "validar_cnpj", {"cnpj": NI_DV_ERRADO})
    assert ruim["isError"] is False
    assert ruim["structuredContent"]["valido"] is False
    assert "verificador" in ruim["structuredContent"]["motivo"]
    assert _log(amb) == []


def test_validar_cnpj_alfanumerico(amb):
    tok = _token_mcp(amb)
    ok = _chamar(amb, tok, "validar_cnpj", {"cnpj": "12.abc.345/01de-35"})
    assert ok["structuredContent"] == {"cnpj": NI_ALFA, "valido": True,
                                       "formatado": "12.ABC.345/01DE-35"}
    ruim = _chamar(amb, tok, "validar_cnpj", {"cnpj": NI_ALFA_DV_ERRADO})
    assert ruim["structuredContent"]["valido"] is False
    assert "verificador" in ruim["structuredContent"]["motivo"]
    malformado = _chamar(amb, tok, "validar_cnpj", {"cnpj": "12abc34501de3a"})
    assert malformado["structuredContent"]["cnpj"] == "12ABC34501DE3A"
    assert "malformado" in malformado["structuredContent"]["motivo"]
    curto = _chamar(amb, tok, "validar_cnpj", {"cnpj": "12ABC345"})
    assert "14 caracteres" in curto["structuredContent"]["motivo"]
    assert _log(amb) == []


@pytest.mark.parametrize("ferramenta", ["situacao_cadastral", "consultar_empresa"])
def test_consulta_alfanumerica(amb, ferramenta):
    res = _chamar(amb, _token_mcp(amb), ferramenta, {"cnpj": "12.abc.345/01de-35"})
    assert res["isError"] is False
    dados = res["structuredContent"]
    assert dados["cnpj"] == NI_ALFA and dados["nomeEmpresarial"] == "ALFA LTDA"
    assert amb.base.consultados == ["12ABC345"]
    [l] = _log(amb)
    assert l["status_http"] == 200 and l["ni"] == NI_ALFA


def test_alfanumerico_ausente_e_resultado_faturavel(amb):
    res = _chamar(amb, _token_mcp(amb), "situacao_cadastral", {"cnpj": NI_ALFA_AUSENTE})
    assert res["structuredContent"] == {"encontrado": False, "cnpj": NI_ALFA_AUSENTE,
                                        "competencia": "2026-09"}
    [l] = _log(amb)
    assert l["status_http"] == 404 and l["faturavel"] is True


@pytest.mark.parametrize("cnpj", [NI_ALFA_DV_ERRADO, "11222333A000181"])
def test_alfanumerico_invalido_e_falha_nao_faturavel(amb, cnpj):
    """`11222333A000181` é o caso que a limpeza antiga respondia com a ACME."""
    res = _chamar(amb, _token_mcp(amb), "situacao_cadastral", {"cnpj": cnpj})
    assert res["isError"] is True
    assert amb.base.consultados == []
    [l] = _log(amb)
    assert l["status_http"] == 400 and l["faturavel"] is False and l["ni"] == cnpj


def test_dv_errado_na_consulta_e_falha_nao_faturavel(amb):
    res = _chamar(amb, _token_mcp(amb), "situacao_cadastral", {"cnpj": NI_DV_ERRADO})
    assert res["isError"] is True
    [l] = _log(amb)
    assert l["status_http"] == 400 and l["faturavel"] is False


@pytest.mark.parametrize("ni", [NI_ESTAB_AUSENTE, NI_EMPRESA_AUSENTE])
def test_nao_encontrado_e_resultado_e_faturavel(amb, ni):
    res = _chamar(amb, _token_mcp(amb), "consultar_empresa", {"cnpj": ni})
    assert res["isError"] is False
    assert res["structuredContent"] == {"encontrado": False, "cnpj": ni,
                                        "competencia": "2026-09"}
    [l] = _log(amb)
    assert l["status_http"] == 404 and l["faturavel"] is True


def test_base_fora_do_ar_e_falha_legivel_e_nao_faturavel(amb):
    tok = _token_mcp(amb)
    amb.base.falhar = True
    res = _chamar(amb, tok, "situacao_cadastral", {"cnpj": NI})
    assert res["isError"] is True
    assert "DESCONHECIDO" in res["content"][0]["text"]
    [l] = _log(amb)
    assert l["status_http"] == 500 and l["faturavel"] is False


def test_cota_esgotada_vira_falha_de_ferramenta_sem_log(amb):
    tok = _token_mcp(amb)
    amb.creds.alterar(amb.cred.consumer_key, quota_mensal=0)
    assert _rpc(amb, tok, "tools/list").status_code == 200
    res = _chamar(amb, tok, "situacao_cadastral", {"cnpj": NI})
    assert res["isError"] is True and "Quota" in res["content"][0]["text"]
    assert _log(amb) == []


def test_descoberta_nao_gera_linha_de_log(amb):
    tok = _token_mcp(amb)
    _rpc(amb, tok, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                  "clientInfo": {"name": "t", "version": "1"}})
    _rpc(amb, tok, "tools/list")
    _rpc(amb, tok, "resources/read", {"uri": "themis://competencia"})
    assert _log(amb) == []


# -- 4. as ferramentas, e o dado pessoal -------------------------------------

def test_situacao_cadastral_e_curta(amb):
    d = _chamar(amb, _token_mcp(amb), "situacao_cadastral", {"cnpj": NI})["structuredContent"]
    assert d == {
        "encontrado": True, "cnpj": NI, "nomeEmpresarial": "ACME LTDA",
        "nomeFantasia": "ACME", "tipo": "matriz",
        "situacao": {"descricao": "ATIVA", "data": "2020-01-01"},
        "dataAbertura": "2010-01-01", "municipio": "SAO PAULO", "uf": "SP",
        "competencia": "2026-09",
    }


def test_consultar_empresa_sem_socios_por_padrao(amb):
    d = _chamar(amb, _token_mcp(amb), "consultar_empresa", {"cnpj": NI})["structuredContent"]
    assert "socios" not in d
    assert "MARIA" not in json.dumps(d)
    assert d["capitalSocial"] == "532000000000.10"      # exato, sem float
    assert d["porte"] == "EPP"
    assert d["endereco"] == {"logradouro": "RUA A", "numero": "1", "bairro": "CENTRO",
                             "cep": "01001000", "municipio": "SAO PAULO", "uf": "SP"}
    assert d["telefones"] == ["(11) 30001000"]
    assert d["cnaesSecundarios"] == [{"codigo": "4721102",
                                      "descricao": "Padaria e confeitaria"}]
    assert d["simples"] == {"optanteSimples": "S", "optanteMei": "N"}
    # O que o contrato do SERPRO manda sempre nulo não viaja.
    assert "municipioJurisdicao" not in json.dumps(d)


def test_consultar_empresa_com_socios(amb):
    d = _chamar(amb, _token_mcp(amb), "consultar_empresa",
                {"cnpj": NI, "incluir_socios": True})["structuredContent"]
    assert d["socios"] == [{
        "nome": "MARIA", "tipo": "PESSOA FÍSICA", "cpfCnpj": "***794780**",
        "qualificacao": "Sócio-Administrador", "dataEntrada": "2010-01-01",
        "faixaEtaria": "41 a 50",
    }]


@pytest.mark.parametrize("args", [
    {"cnpj": NI, "incluir_socios": "sim"},
    {"cnpj": 11222333000181},
    {},
    {"cnpj": NI, "desconhecido": 1},
])
def test_argumento_errado_e_falha_que_o_modelo_le(amb, args):
    res = _chamar(amb, _token_mcp(amb), "consultar_empresa", args)
    assert res["isError"] is True


def test_competencia_e_lida_uma_vez_por_processo(amb):
    tok = _token_mcp(amb)
    for _ in range(3):
        _chamar(amb, tok, "situacao_cadastral", {"cnpj": NI})
    assert amb.base.leituras_metadados == 1


# -- 5. o protocolo ----------------------------------------------------------

def test_initialize(amb):
    r = _rpc(amb, _token_mcp(amb), "initialize",
             {"protocolVersion": "2025-11-25", "capabilities": {},
              "clientInfo": {"name": "t", "version": "1"}})
    res = r.json()["result"]
    assert res["protocolVersion"] == "2025-11-25"
    assert res["serverInfo"]["name"] == "themis-cnpj"
    assert "2026-09" in res["instructions"]
    assert "mcp-session-id" not in {k.lower() for k in r.headers}


def test_tools_list(amb):
    ferramentas = _rpc(amb, _token_mcp(amb), "tools/list").json()["result"]["tools"]
    assert {f["name"] for f in ferramentas} == {
        "validar_cnpj", "situacao_cadastral", "consultar_empresa"}
    assert all(f["annotations"]["readOnlyHint"] for f in ferramentas)


def test_recurso_competencia(amb):
    tok = _token_mcp(amb)
    c = _rpc(amb, tok, "resources/read", {"uri": "themis://competencia"}).json()
    assert json.loads(c["result"]["contents"][0]["text"]) == {
        "competencia": "2026-09", "construidaEm": "2026-09-17T16:00:00+00:00",
        "linhas": 222197006}
    assert _rpc(amb, tok, "resources/read", {"uri": "x://y"}).json()["error"]["code"] == -32002


def test_recusas_de_protocolo(amb):
    tok = _token_mcp(amb)
    lote = amb.api.post("/mcp", headers=_cab(tok),
                        json=[{"jsonrpc": "2.0", "id": 1, "method": "ping"}])
    assert lote.status_code == 400 and lote.json()["error"]["code"] == -32600
    invalido = amb.api.post("/mcp", headers=_cab(tok), content=b"{nao")
    assert invalido.json()["error"]["code"] == -32700
    assert _rpc(amb, tok, "ping", **{"MCP-Protocol-Version": "1999-01-01"}).status_code == 400
    assert _rpc(amb, tok, "tools/call", {"name": "x"}).json()["error"]["code"] == -32602
    assert _rpc(amb, tok, "nada").json()["error"]["code"] == -32601
    notif = amb.api.post("/mcp", headers=_cab(tok),
                         json={"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert notif.status_code == 202 and notif.content == b""
    assert amb.api.get("/mcp", headers=_cab(tok)).status_code == 405
    assert _rpc(amb, tok, "ping", Origin="https://atacante.example").status_code == 403


def test_mcp_fica_fora_do_openapi(amb):
    assert "/mcp" not in amb.api.get("/openapi.json").json()["paths"]
