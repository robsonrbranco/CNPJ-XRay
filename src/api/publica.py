"""API pública — compatível com a Consulta CNPJ v2 do SERPRO.

    POST /token                emite o Bearer a partir de Consumer Key/Secret
    GET  /v2/basica/{ni}       cadastrais, sem sócios
    GET  /v2/qsa/{ni}          quadro de sócios e administradores
    GET  /v2/empresa/{ni}      o conjunto completo

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

from fastapi import Depends, FastAPI, Header, Request, Response
from fastapi.responses import JSONResponse

from . import cnpj as mod_cnpj
from . import serpro, token as mod_token
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


class ErroAPI(Exception):
    def __init__(self, status: int, mensagem: str | None = None):
        self.status = status
        self.mensagem = mensagem or MENSAGENS.get(status, "Erro")
        super().__init__(self.mensagem)


def criar_app(cfg: ConfigAPI, consultar=None) -> FastAPI:
    """Monta a aplicação.

    `consultar` é injetável para o teste rodar sem Firebird. O padrão é a
    consulta real; substituí-la não muda nenhum caminho de código testado
    abaixo, só de onde o dado vem.
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

    app = FastAPI(title="CNPJ-XRay — Consulta CNPJ", version="2.0")

    # Abertos na construção, não no lifespan. Nada aqui precisa de ciclo de
    # vida assíncrono -- SQLite abre conexão por chamada e o Log é um caminho --
    # e deixar no lifespan significaria que importar a aplicação sem executá-lo
    # devolve um objeto quebrado. Cada worker constrói o seu, e é isso que
    # dispensa lock no log: nenhum arquivo é compartilhado entre processos.
    app.state.conexao = viva
    app.state.credenciais = Credenciais(cfg.credenciais)
    app.state.log = Log(cfg.logs)
    app.state.estatisticas = Estatisticas(cfg.estatisticas)

    # -- erros ----------------------------------------------------------

    @app.exception_handler(ErroAPI)
    async def _erro_api(request: Request, exc: ErroAPI):
        return JSONResponse(status_code=exc.status, content={"message": exc.mensagem})

    @app.exception_handler(Exception)
    async def _erro_interno(request: Request, exc: Exception):
        # Nunca vaza a exceção: mensagem de erro é superfície de informação.
        return JSONResponse(status_code=500, content={"message": MENSAGENS[500]})

    # -- token ----------------------------------------------------------

    @app.post("/token")
    async def emitir_token(request: Request, authorization: str = Header(None)):
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

    async def autenticado(request: Request, authorization: str = Header(None)) -> str:
        """Devolve o consumer_key, ou levanta 401/403.

        A revogação é conferida aqui, e não só na emissão. O token é
        autocontido e valeria até expirar; como o armazenamento já está no
        caminho quente por causa do log, conferir o estado não custa nada novo.
        """
        try:
            bruto = mod_token.do_cabecalho(authorization)
            claims = mod_token.verificar(bruto, cfg.jwt_segredo)
        except mod_token.TokenInvalido as e:
            raise ErroAPI(401) from e

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

    @app.get("/v2/basica/{ni}")
    async def basica(ni: str, request: Request, chave: str = Depends(autenticado)):
        return _responder(serpro.basica, ni, chave, "/v2/basica", request)

    @app.get("/v2/qsa/{ni}")
    async def qsa(ni: str, request: Request, chave: str = Depends(autenticado)):
        return _responder(serpro.qsa, ni, chave, "/v2/qsa", request)

    @app.get("/v2/empresa/{ni}")
    async def empresa(ni: str, request: Request, chave: str = Depends(autenticado)):
        return _responder(serpro.empresa, ni, chave, "/v2/empresa", request)

    return app
