"""A página de apresentação montada em `/`, junto com a API.

Tudo aqui gira em torno de um risco só: `StaticFiles` montado em `/` casa com
**qualquer** caminho. Registrado antes das rotas, ele engoliria `/token`,
`/v2/...`, `/saude` e `/docs` — a API inteira viraria "arquivo não encontrado",
e o sintoma apareceria como 404 em cliente de produção, não como erro na
inicialização.

Por isso o teste central não confere que a página aparece; confere que a API
continua respondendo com a página montada.
"""

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.api import publica  # noqa: E402
from src.api.config import ConfigAPI  # noqa: E402
from src.api.credenciais import Credenciais  # noqa: E402

PAGINA = "<!DOCTYPE html><html><body>Themis</body></html>"
NAO_ACHEI = "<!DOCTYPE html><html><body>404 do Hugo</body></html>"


@pytest.fixture
def site(tmp_path):
    """O que o `hugo --minify` deixa em `public/`, no mínimo."""
    pasta = tmp_path / "site"
    pasta.mkdir()
    (pasta / "index.html").write_text(PAGINA, encoding="utf-8")
    (pasta / "404.html").write_text(NAO_ACHEI, encoding="utf-8")
    (pasta / "favicon.svg").write_text("<svg/>", encoding="utf-8")
    return pasta


def _cfg(tmp_path, site_dir=""):
    return ConfigAPI(
        credenciais=tmp_path / "creds" / "credenciais.db",
        logs=tmp_path / "logs",
        estatisticas=tmp_path / "creds" / "estatisticas.db",
        jwt_segredo="segredo-de-teste",
        site_dir=site_dir,
    )


@pytest.fixture
def cliente(tmp_path, site):
    cfg = _cfg(tmp_path, str(site))
    Credenciais(cfg.credenciais)
    app = publica.criar_app(cfg, consultar=lambda basico: None)
    return TestClient(app, raise_server_exceptions=False)


def test_a_pagina_responde_na_raiz(cliente):
    r = cliente.get("/")
    assert r.status_code == 200
    assert "Themis" in r.text
    assert r.headers["content-type"].startswith("text/html")


@pytest.mark.parametrize("rota", ["/saude", "/docs", "/redoc", "/openapi.json"])
def test_o_site_nao_engole_as_rotas_da_api(cliente, rota):
    """O teste que justifica o arquivo.

    `Mount("/")` casa com qualquer caminho. Se ele for registrado antes das
    rotas, todas estas viram 404 de arquivo — e a API some inteira sem nenhum
    erro na subida."""
    r = cliente.get(rota)

    assert r.status_code != 404, f"{rota} caiu no site em vez da API"
    assert "404 do Hugo" not in r.text, f"{rota} foi atendida pelo site"


def test_saude_continua_devolvendo_json(cliente):
    """Não basta não dar 404: se o site atendesse `/saude` com o index, viria
    200 de HTML e a sonda do Kubernetes passaria a validar a página."""
    r = cliente.get("/saude")

    assert r.headers["content-type"].startswith("application/json")
    assert "status" in r.json()


def test_token_continua_sendo_da_api(cliente):
    """`/token` é POST. Um mount em `/` responde 405 a POST, e não 401 — então
    o código distingue quem atendeu."""
    r = cliente.post("/token", data={"grant_type": "client_credentials"})

    assert r.status_code == 401, r.text
    assert r.json().get("error") or r.json().get("message")


def test_v2_sem_credencial_continua_401(cliente):
    """Rota de consulta com o site montado: quem responde tem que ser a API,
    com o 401 do contrato, e não o 404 do Hugo."""
    r = cliente.get("/v2/empresa/11222333000181")

    assert r.status_code == 401, r.text


def test_sem_pasta_de_site_a_api_sobe_igual(tmp_path):
    """Fora da imagem não há `public/`. Exigir o Hugo instalado para rodar a
    API — ou os testes — amarraria as duas coisas pelo lado errado."""
    cfg = _cfg(tmp_path, site_dir="")
    Credenciais(cfg.credenciais)
    c = TestClient(publica.criar_app(cfg, consultar=lambda b: None),
                   raise_server_exceptions=False)

    assert c.get("/saude").status_code in (200, 503)
    assert c.get("/").status_code == 404


def test_pasta_inexistente_nao_derruba_a_api(tmp_path):
    """`StaticFiles` levanta na construção se o diretório não existe. Com o
    caminho vindo de variável de ambiente, um valor errado no manifest
    derrubaria a API inteira por causa da página."""
    cfg = _cfg(tmp_path, site_dir=str(tmp_path / "nao-existe"))
    Credenciais(cfg.credenciais)
    c = TestClient(publica.criar_app(cfg, consultar=lambda b: None),
                   raise_server_exceptions=False)

    assert c.get("/saude").status_code in (200, 503)
