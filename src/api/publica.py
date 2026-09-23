"""API pública — compatível com a Consulta CNPJ v2 do SERPRO.

    POST /token                emite o Bearer a partir de Consumer Key/Secret
    GET  /v2/basica/{ni}       cadastrais, sem sócios
    GET  /v2/qsa/{ni}          quadro de sócios e administradores
    GET  /v2/empresa/{ni}      o conjunto completo

    POST /mcp                  camada para agentes (MCP), com token próprio —
                               ver `api/mcp.py`

Esta aplicação **não** tem a rota `/manager`: gestão de credenciais roda noutra
porta, não exposta. Uma credencial capaz de criar credenciais seria escalada de
privilégio, e separar por processo é a única barreira que não depende de nenhum
segredo estar certo.

Sobre os códigos de erro
------------------------
Os códigos seguem o contrato do SERPRO. O **corpo** da resposta de erro não foi
verificado contra uma resposta real deles — a documentação lista os códigos e
as mensagens, não o JSON. Se um cliente real depender do formato do corpo, é
aqui que se ajusta.

Quota estourada devolve **403**, não 429. A documentação do SERPRO descreve 403
como acesso negado por "permission issues (...) or contract restrictions", e
quota excedida é exatamente restrição contratual. 429 seria mais expressivo,
mas introduziria um código que um cliente escrito para eles não espera.
"""

from __future__ import annotations

import time
from pathlib import Path

from fastapi import Depends, FastAPI, Request, Response
from fastapi.openapi.utils import get_openapi
from fastapi.security import HTTPBasic, HTTPBearer
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from . import cnpj as mod_cnpj
from . import mcp as mod_mcp
from . import serpro, token as mod_token
from . import esquemas
from .conexao import ConexaoViva
from .config import ConfigAPI
from .credenciais import Credenciais
from .uso import Estatisticas, Log

MENSAGENS = {
    400: "O número de CNPJ informado não é válido",
    401: "Houve falha na autenticação",
    403: "Acesso negado",
    404: "Nenhum registro encontrado para o CNPJ informado",
    500: "Ocorreu um erro interno inesperado",
}


DESCRICAO = """API de consulta ao CNPJ compatível com a Consulta CNPJ v2 do SERPRO: mesmos
caminhos, mesmos nomes de campo, mesmos códigos de retorno.

**Duas diferenças que nenhuma implementação fecha**, porque são da fonte e não
do código:

* o **CPF dos sócios vem mascarado** — a Receita publica `***794780**` nos
  dados abertos; o SERPRO, como canal autorizado, devolve completo;
* a base é um **retrato mensal**, não tempo real. Uma empresa aberta ontem não
  está aqui, e uma baixa da semana passada ainda aparece ativa.
"""


class ErroAPI(Exception):
    def __init__(self, status: int, mensagem: str | None = None):
        self.status = status
        self.mensagem = mensagem or MENSAGENS.get(status, "Erro")
        super().__init__(self.mensagem)


def criar_app(cfg: ConfigAPI, consultar=None, metadados=None) -> FastAPI:
    """Monta a aplicação.

    `consultar` é injetável para o teste rodar sem Firebird. O padrão é a
    consulta real; substituí-la não muda nenhum caminho de código testado
    abaixo, só de onde o dado vem.

    `metadados` idem: devolve a proveniência da base (competência, data de
    construção). Só a camada MCP a usa — o `/saude` lê a mesma coisa pela
    conexão, junto com a flag de somente-leitura.
    """
    # Conexão viva por worker. Abrir attachment em embedded custa ~387 ms e a
    # consulta indexada custa 0,4 ms -- sem isto a API paga o attachment a cada
    # requisição. Medido: 772 ms por ficha contra 11,6 ms.
    viva: ConexaoViva | None = None
    if consultar is None:
        from ..consulta.empresa import consultar as _consultar
        viva = ConexaoViva()

        def consultar(cnpj_basico):
            return viva.executar(lambda con: _consultar(cnpj_basico, con=con))

    if metadados is None and viva is not None:
        def metadados():
            from ..db import manage
            return viva.executar(manage.ler_metadados)

    # `auto_error=False` porque quem decide o código é a API: sem isto o
    # FastAPI devolveria 403 onde o contrato do SERPRO manda 401.
    basic = HTTPBasic(auto_error=False, description="Consumer Key e Consumer Secret")
    bearer = HTTPBearer(auto_error=False, description="Token obtido em POST /token")

    app = FastAPI(
        title="CNPJ-XRay — Consulta CNPJ",
        version="2.0",
        description=DESCRICAO,
    )

    # Abertos na construção, não no lifespan. Nada aqui precisa de ciclo de
    # vida assíncrono -- SQLite abre conexão por chamada e o Log é um caminho --
    # e deixar no lifespan significaria que importar a aplicação sem executá-lo
    # devolve um objeto quebrado. Cada worker constrói o seu, e é isso que
    # dispensa lock no log: nenhum arquivo é compartilhado entre processos.
    app.state.conexao = viva
    app.state.consultar = consultar
    app.state.metadados = metadados
    app.state.credenciais = Credenciais(cfg.credenciais)
    app.state.log = Log(cfg.logs)
    app.state.estatisticas = Estatisticas(cfg.estatisticas)

    # O FastAPI injeta 422 em toda rota com parâmetro, e esta API nunca o
    # devolve: `ni` é string e não há validação que possa falhar antes do
    # handler. Documentá-lo mandaria o cliente tratar um caso inexistente, e a
    # promessa aqui é que só aparecem os códigos do contrato do SERPRO.
    def _openapi_sem_422():
        if app.openapi_schema:
            return app.openapi_schema
        spec = get_openapi(title=app.title, version=app.version,
                           description=app.description, routes=app.routes)
        for caminho in spec.get("paths", {}).values():
            for operacao in caminho.values():
                operacao.get("responses", {}).pop("422", None)
        spec.get("components", {}).get("schemas", {}).pop("HTTPValidationError", None)
        spec.get("components", {}).get("schemas", {}).pop("ValidationError", None)
        app.openapi_schema = spec
        return spec

    app.openapi = _openapi_sem_422

    # -- erros ----------------------------------------------------------

    @app.exception_handler(ErroAPI)
    async def _erro_api(request: Request, exc: ErroAPI):
        return JSONResponse(status_code=exc.status, content={"message": exc.mensagem})

    @app.exception_handler(Exception)
    async def _erro_interno(request: Request, exc: Exception):
        # Nunca vaza a exceção: mensagem de erro é superfície de informação.
        return JSONResponse(status_code=500, content={"message": MENSAGENS[500]})

    # -- saúde e proveniência -------------------------------------------

    @app.get(
        "/saude",
        response_model=esquemas.Saude,
        summary="Estado do pod e competência dos dados servidos",
        description="**Não faz parte do contrato do SERPRO** — é nossa, e fica "
                    "fora de `/v2/` para não colidir com nenhum caminho futuro "
                    "deles.\n\n"
                    "Sem autenticação: serve de sonda de vivacidade, e a "
                    "competência não é informação sensível — é o oposto, quem "
                    "consome precisa dela para saber a idade do dado.",
        tags=["operação"],
    )
    async def saude(request: Request):
        def ler(con):
            from ..db import manage
            meta = manage.ler_metadados(con)
            cur = con.cursor()
            cur.execute("SELECT MON$READ_ONLY FROM MON$DATABASE")
            return meta, bool(cur.fetchone()[0])

        try:
            meta, somente_leitura = (
                request.app.state.conexao.executar(ler)
                if request.app.state.conexao is not None else ({}, None)
            )
        except Exception:                                    # noqa: BLE001
            # Sonda de vivacidade não pode derrubar o pod por causa do banco;
            # ela existe para REPORTAR que ele está fora.
            return JSONResponse(status_code=503,
                                content={"status": "base inacessível"})

        linhas = meta.get("linhas_total")
        return {
            "status": "ok",
            "competencia": meta.get("competencia") or None,
            "construidaEm": meta.get("construida_em") or None,
            "linhas": int(linhas) if linhas and linhas.isdigit() else None,
            "baseSomenteLeitura": somente_leitura,
        }

    # -- token ----------------------------------------------------------

    @app.post(
        "/token",
        response_model=esquemas.Token,
        responses=esquemas.ERROS_TOKEN,
        summary="Emite o token de acesso",
        description="OAuth2 `client_credentials`. Envie "
                    "`Authorization: Basic base64(ConsumerKey:ConsumerSecret)` "
                    "e `grant_type=client_credentials`.",
        tags=["autenticação"],
    )
    async def emitir_token(request: Request, credencial=Depends(basic)):
        authorization = request.headers.get("authorization")
        try:
            chave, segredo = mod_token.credenciais_basic(authorization)
        except mod_token.TokenInvalido as e:
            raise ErroAPI(401) from e

        cred = request.app.state.credenciais.autenticar(chave, segredo)
        if cred is None:
            raise ErroAPI(401)

        acesso, expira = mod_token.emitir(
            cred.consumer_key, cfg.jwt_segredo, cfg.validade_token_s
        )
        return {
            "access_token": acesso,
            "token_type": "Bearer",
            "expires_in": expira,
            "scope": "default",
        }

    # -- autenticação das consultas -------------------------------------

    async def autenticado(request: Request, credencial=Depends(bearer)) -> str:
        """Devolve o consumer_key, ou levanta 401/403.

        A revogação é conferida aqui, e não só na emissão. O token é
        autocontido e valeria até expirar; como o armazenamento já está no
        caminho quente por causa do log, conferir o estado não custa nada novo.
        """
        authorization = request.headers.get("authorization")
        try:
            bruto = mod_token.do_cabecalho(authorization)
            claims = mod_token.verificar(bruto, cfg.jwt_segredo)
        except mod_token.TokenInvalido as e:
            raise ErroAPI(401) from e

        # Token com `aud` é do MCP (ver `api/mcp.py`): vive até 365 dias e foi
        # emitido para agentes, não para o contrato do SERPRO. Aceitá-lo aqui
        # daria a um token de meses o alcance das rotas REST.
        if "aud" in claims:
            raise ErroAPI(401)

        chave = claims.get("sub", "")
        try:
            cred = request.app.state.credenciais.obter(chave)
        except LookupError as e:
            raise ErroAPI(401) from e
        if not cred.ativa:
            raise ErroAPI(401)

        if cred.quota_mensal is not None:
            usado = request.app.state.estatisticas.consumo_do_mes(chave)
            if usado >= cred.quota_mensal:
                raise ErroAPI(403, "Quota mensal do contrato excedida")

        return chave

    # -- consultas ------------------------------------------------------

    def _responder(recorte, ni_bruto: str, chave: str, rota: str,
                   request: Request) -> Response:
        inicio = time.perf_counter()
        status, corpo = 200, None
        try:
            ni = mod_cnpj.normalizar(ni_bruto)          # DV errado -> 400
            ficha = consultar(ni[:8])
            if ficha.get("empresa") is None:
                raise ErroAPI(404)
            corpo = recorte(ficha, ni)
        except mod_cnpj.CNPJInvalido:
            status = 400
        except serpro.EstabelecimentoNaoEncontrado:
            status = 404
        except ErroAPI as e:
            status = e.status
        except Exception:                                # noqa: BLE001
            status = 500
        finally:
            # O log acontece SEMPRE, inclusive em erro: é o que permite
            # distinguir consulta faturável de erro do cliente na estatística.
            request.app.state.log.registrar(
                consumer_key=chave, rota=rota, status_http=status,
                duracao_ms=int((time.perf_counter() - inicio) * 1000),
                ni=mod_cnpj.so_digitos(ni_bruto) or None,
            )

        if status != 200:
            return JSONResponse(status_code=status,
                                content={"message": MENSAGENS.get(status, "Erro")})
        return JSONResponse(status_code=200, content=corpo)

    @app.get(
        "/v2/basica/{ni}",
        response_model=esquemas.Basica,
        responses=esquemas.ERROS_CONSULTA,
        summary="Dados cadastrais, sem o quadro societário",
        description="Equivale ao `/v2/basica/{ni}` do SERPRO. `ni` é o CNPJ com 14 dígitos.",
        tags=["consulta"],
    )
    async def basica(ni: str, request: Request, chave: str = Depends(autenticado)):
        return _responder(serpro.basica, ni, chave, "/v2/basica", request)

    @app.get(
        "/v2/qsa/{ni}",
        response_model=esquemas.QSA,
        responses=esquemas.ERROS_CONSULTA,
        summary="Quadro de sócios e administradores",
        description="Equivale ao `/v2/qsa/{ni}` do SERPRO. O CPF dos sócios vem mascarado. `ni` é o CNPJ com 14 dígitos.",
        tags=["consulta"],
    )
    async def qsa(ni: str, request: Request, chave: str = Depends(autenticado)):
        return _responder(serpro.qsa, ni, chave, "/v2/qsa", request)

    @app.get(
        "/v2/empresa/{ni}",
        response_model=esquemas.Empresa,
        responses=esquemas.ERROS_CONSULTA,
        summary="Dados cadastrais e quadro societário",
        description="Equivale ao `/v2/empresa/{ni}` do SERPRO. `ni` é o CNPJ com 14 dígitos.",
        tags=["consulta"],
    )
    async def empresa(ni: str, request: Request, chave: str = Depends(autenticado)):
        return _responder(serpro.empresa, ni, chave, "/v2/empresa", request)

    # A camada para agentes, em `/mcp`. Fica fora do OpenAPI: o OpenAPI
    # documenta o contrato do SERPRO, e o MCP é contrato nosso. Não é montada
    # sem `cfg.mcp_uri`. Registrada antes do site pela razão abaixo.
    mod_mcp.registrar(app, cfg)

    # A página de apresentação, em `/`.
    #
    # Montada DEPOIS de todas as rotas, e isso não é estilo: o Starlette
    # percorre as rotas na ordem em que foram registradas, e um `Mount("/")`
    # casa com qualquer caminho. Montado antes, engoliria `/token`, `/v2/...`,
    # `/saude` e `/docs` — a API inteira viraria 404 de arquivo não encontrado.
    #
    # `html=True` faz `/` servir o `index.html` e dá o `404.html` do Hugo para
    # caminho desconhecido. Cliente que erra a rota da API recebe HTML em vez
    # de JSON, o que é o preço de servir as duas coisas no mesmo domínio; quem
    # acerta a rota continua recebendo os erros no formato do SERPRO.
    #
    # Se a pasta não existir — que é o caso fora da imagem, em teste e em
    # desenvolvimento — não monta nada. A API não depende da página para
    # funcionar, e exigir um `hugo` instalado para rodar os testes seria
    # amarrar as duas coisas pelo lado errado.
    if cfg.site_dir and Path(cfg.site_dir).is_dir():
        app.mount("/", StaticFiles(directory=cfg.site_dir, html=True),
                  name="site")

    return app
