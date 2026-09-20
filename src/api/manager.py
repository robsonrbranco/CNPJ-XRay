"""`/manager` — gestão de credenciais e estatísticas de uso.

**Aplicação separada, porta separada, não exposta.** Não é organização de
código: é a barreira de segurança. Uma credencial capaz de emitir credenciais é
escalada de privilégio por desenho, então o `/manager` não pode conviver com a
API pública nem compartilhar autenticação com ela.

Separar por processo é a única proteção que não depende de nenhum segredo estar
correto — se a porta não é alcançável, não há o que atacar. Quem publicar esta
aplicação na mesma porta da API pública, ou expô-la para fora do cluster, anula
a decisão inteira.

Por isso não há autenticação aqui: ela seria um segundo mecanismo dependendo de
um segredo, e daria a falsa impressão de que expor a porta é aceitável. Se um
dia for preciso defesa em profundidade, a credencial administrativa entra
**além** do isolamento de rede, nunca no lugar dele.
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from .config import ConfigAPI
from .credenciais import Credenciais, CredencialNaoEncontrada, STATUS_VALIDOS
from .uso import Estatisticas


class NovaCredencial(BaseModel):
    contratante_nome: str = Field(min_length=1)
    contratante_documento: str | None = None
    contratante_email: str | None = None
    quota_mensal: int | None = Field(default=None, ge=0)
    observacao: str | None = None


class AlteracaoCredencial(BaseModel):
    contratante_nome: str | None = Field(default=None, min_length=1)
    contratante_documento: str | None = None
    contratante_email: str | None = None
    quota_mensal: int | None = Field(default=None, ge=0)
    observacao: str | None = None
    status: str | None = None


def criar_app(cfg: ConfigAPI) -> FastAPI:
    app = FastAPI(title="CNPJ-XRay — manager", version="1.0")
    app.state.credenciais = Credenciais(cfg.credenciais)
    app.state.estatisticas = Estatisticas(cfg.estatisticas)
    app.state.cfg = cfg

    def _cred(request: Request, chave: str):
        try:
            return request.app.state.credenciais.obter(chave)
        except CredencialNaoEncontrada:
            raise HTTPException(404, "credencial não encontrada") from None

    # -- credenciais ----------------------------------------------------

    @app.get("/manager/credenciais")
    async def listar(request: Request, status: str | None = None):
        if status and status not in STATUS_VALIDOS:
            raise HTTPException(400, f"status inválido: {status}")
        creds = request.app.state.credenciais.listar(status)
        return {"credenciais": [c.para_json() for c in creds]}

    @app.post("/manager/credenciais", status_code=201)
    async def incluir(request: Request, dados: NovaCredencial):
        cred, secret = request.app.state.credenciais.criar(**dados.model_dump())
        # O segredo aparece UMA vez. Depois disto só existe o hash, e não há
        # como recuperá-lo -- só rotacionar.
        return {**cred.para_json(), "consumerSecret": secret,
                "aviso": "guarde o consumerSecret: ele não será exibido de novo"}

    @app.get("/manager/credenciais/{chave}")
    async def consultar(request: Request, chave: str):
        cred = _cred(request, chave)
        usado = request.app.state.estatisticas.consumo_do_mes(chave)
        return {**cred.para_json(), "consumoDoMes": usado}

    @app.patch("/manager/credenciais/{chave}")
    async def alterar(request: Request, chave: str, dados: AlteracaoCredencial):
        campos = {k: v for k, v in dados.model_dump().items() if v is not None}
        if not campos:
            raise HTTPException(400, "nenhum campo para alterar")
        _cred(request, chave)                       # 404 antes de tentar alterar
        try:
            alterada = request.app.state.credenciais.alterar(chave, **campos)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        return alterada.para_json()

    @app.post("/manager/credenciais/{chave}/rotacionar")
    async def rotacionar(request: Request, chave: str):
        _cred(request, chave)
        secret = request.app.state.credenciais.rotacionar(chave)
        return {"consumerKey": chave, "consumerSecret": secret,
                "aviso": "o segredo anterior deixou de valer imediatamente"}

    @app.delete("/manager/credenciais/{chave}")
    async def revogar(request: Request, chave: str):
        _cred(request, chave)
        # Revogar NÃO apaga: o uso histórico precisa continuar íntegro nas
        # estatísticas. Credencial apagada deixaria buraco no relatório do mês.
        return request.app.state.credenciais.revogar(chave).para_json()

    # -- estatísticas ---------------------------------------------------

    @app.get("/manager/estatisticas")
    async def estatisticas_gerais(request: Request, competencia: str | None = None):
        request.app.state.estatisticas.consolidar(cfg.logs)
        return request.app.state.estatisticas.resumo(competencia=competencia)

    @app.get("/manager/estatisticas/{chave}")
    async def estatisticas_da_credencial(request: Request, chave: str,
                                         competencia: str | None = None):
        cred = _cred(request, chave)
        request.app.state.estatisticas.consolidar(cfg.logs)
        resumo = request.app.state.estatisticas.resumo(chave, competencia)
        resumo["quotaMensal"] = cred.quota_mensal
        resumo["consumoDoMes"] = request.app.state.estatisticas.consumo_do_mes(chave)
        if cred.quota_mensal:
            resumo["percentualDaQuota"] = round(
                resumo["consumoDoMes"] / cred.quota_mensal * 100, 1
            )
        return resumo

    @app.post("/manager/manutencao/consolidar")
    async def consolidar(request: Request, incluir_hoje: bool = False):
        return request.app.state.estatisticas.consolidar(cfg.logs, incluir_hoje)

    @app.post("/manager/manutencao/podar")
    async def podar(request: Request, dias: int | None = None):
        """Apaga o analítico já consolidado mais velho que `dias`.

        **Nada poda sozinho.** Não há janela de retenção automática: o log
        analítico é mantido indefinidamente, e apagá-lo é sempre um ato
        deliberado com o prazo dito na chamada. `dias` sem valor, ou
        `retencao_dias = 0`, recusa em vez de assumir um padrão -- apagar
        histórico por engano não tem desfazer.
        """
        prazo = dias if dias is not None else cfg.retencao_dias
        if not prazo:
            raise HTTPException(
                400, "informe 'dias': não há janela de retenção automática"
            )
        return {"arquivosApagados": request.app.state.estatisticas.podar(cfg.logs, prazo)}

    return app
