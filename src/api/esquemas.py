"""Modelos do contrato, para o OpenAPI descrever a API de verdade.

Sem isto o FastAPI gera `/docs` com as rotas listadas e **nenhum contrato**: o
schema do 200 sai como `{}`, os códigos 400/401/403/404/500 não aparecem, e
surge um 422 que a API real nunca devolve.

Estes modelos existem só para DOCUMENTAR. As rotas devolvem `JSONResponse`
direto e o FastAPI não valida nem filtra nada por causa deles — declarar em
`responses={...}` documenta sem interferir na resposta.

A escolha é deliberada: um `response_model` que valida silenciosamente
descartaria campo ausente do modelo, e a promessa aqui é compatibilidade com um
contrato de terceiros. Errar por documentação desatualizada é barato; errar
mutilando a resposta não é.

Os nomes seguem o esquema v2 do SERPRO, inclusive o camelCase, que não é a
convenção do resto do projeto.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class Erro(BaseModel):
    message: str = Field(examples=["O número de CNPJ informado não é válido"])


class Token(BaseModel):
    access_token: str
    token_type: str = Field(default="Bearer", examples=["Bearer"])
    expires_in: int = Field(examples=[3600])
    scope: str = Field(default="default", examples=["default"])


class Dominio(BaseModel):
    """Par código/descrição das tabelas de domínio.

    `descricao` pode vir nula com código presente: a base tem código órfão real
    — 18.253 só em `motivo_situacao_cadastral` — e o JOIN é LEFT para a linha
    não sumir por causa disso.
    """
    codigo: str | None = Field(default=None, examples=["2062"])
    descricao: str | None = Field(default=None,
                                  examples=["Sociedade Empresária Limitada"])


class SituacaoCadastral(BaseModel):
    codigo: str | None = Field(default=None, examples=["02"])
    data: str | None = Field(default=None, examples=["2020-05-10"])
    motivo: str | None = None


class Endereco(BaseModel):
    tipoLogradouro: str | None = Field(default=None, examples=["RUA"])
    logradouro: str | None = Field(default=None, examples=["DAS FLORES"])
    numero: str | None = Field(default=None, examples=["100"])
    complemento: str | None = None
    cep: str | None = Field(default=None, examples=["01001000"])
    bairro: str | None = Field(default=None, examples=["CENTRO"])
    uf: str | None = Field(default=None, examples=["SP"])
    municipio: Dominio | None = None
    pais: Dominio | None = None
    municipioJurisdicao: Dominio | None = Field(
        default=None,
        description="Sempre nulo: o município de jurisdição fiscal não existe "
                    "nos dados abertos da Receita.",
    )


class Telefone(BaseModel):
    ddd: str | None = Field(default=None, examples=["11"])
    numero: str | None = Field(default=None, examples=["30001000"])


class PeriodoSimples(BaseModel):
    dataInicio: str | None = Field(default=None, examples=["2010-04-01"])
    dataFim: str | None = None


class InformacoesAdicionais(BaseModel):
    optanteSimples: str | None = Field(default=None, examples=["S"])
    optanteMei: str | None = Field(default=None, examples=["N"])
    listaPeriodosSimples: list[PeriodoSimples] = Field(
        default_factory=list,
        description="O SERPRO devolve o histórico de períodos; a Receita "
                    "publica um par opção/exclusão apenas, então esta lista "
                    "tem no máximo um elemento.",
    )


class RepresentanteLegal(BaseModel):
    cpf: str | None = None
    nome: str | None = None
    qualificacao: str | None = None


class Socio(BaseModel):
    tipoSocio: str | None = Field(default=None, examples=["2"])
    cpf: str | None = Field(
        default=None, examples=["***794780**"],
        description="MASCARADO. A Receita publica o CPF parcialmente oculto "
                    "nos dados abertos; o SERPRO, como canal autorizado, "
                    "devolve completo. Mesmo campo, conteúdo diferente.",
    )
    nome: str | None = Field(default=None, examples=["MARIA DA SILVA"])
    qualificacao: str | None = Field(default=None, examples=["49"])
    dataInclusao: str | None = Field(default=None, examples=["2010-03-01"])
    pais: Dominio | None = None
    representanteLegal: RepresentanteLegal | None = None


class Basica(BaseModel):
    ni: str = Field(examples=["11222333000181"])
    tipoEstabelecimento: str | None = Field(default=None, examples=["1"])
    nomeEmpresarial: str | None = Field(default=None, examples=["ACME LTDA"])
    nomeFantasia: str | None = Field(default=None, examples=["ACME"])
    dataAbertura: str | None = Field(default=None, examples=["2010-03-01"])
    situacaoCadastral: SituacaoCadastral | None = None
    naturezaJuridica: Dominio | None = None
    dataSituacaoEspecial: str | None = None
    situacaoEspecial: str | None = None
    capitalSocial: int = Field(
        default=0, examples=[123456],
        description="Inteiro em CENTAVOS, como no SERPRO. 123456 são "
                    "R$ 1.234,56.",
    )
    porte: str | None = Field(default=None, examples=["03"])
    endereco: Endereco | None = None
    telefone: list[Telefone] = Field(default_factory=list)
    correioEletronico: str | None = None
    cnaePrincipal: Dominio | None = None
    cnaeSecundarias: list[Dominio] = Field(default_factory=list)
    informacoesAdicionais: InformacoesAdicionais | None = None


class QSA(BaseModel):
    ni: str = Field(examples=["11222333000181"])
    nomeEmpresarial: str | None = None
    socios: list[Socio] = Field(default_factory=list)


class Empresa(Basica):
    socios: list[Socio] = Field(default_factory=list)


# Códigos do contrato do SERPRO. O 422 do FastAPI não entra: a API real não o
# devolve, e documentá-lo mandaria o cliente tratar um caso inexistente.
ERROS_CONSULTA = {
    400: {"model": Erro, "description": "O número de CNPJ informado não é válido"},
    401: {"model": Erro, "description": "Houve falha na autenticação"},
    403: {"model": Erro,
          "description": "Acesso negado — inclusive quota mensal excedida"},
    404: {"model": Erro,
          "description": "Nenhum registro encontrado para o CNPJ informado"},
    500: {"model": Erro, "description": "Ocorreu um erro interno inesperado"},
}

ERROS_TOKEN = {
    401: {"model": Erro, "description": "Houve falha na autenticação"},
}


# ---------------------------------------------------------------------------
# /manager — a parte nossa, não do contrato do SERPRO
# ---------------------------------------------------------------------------

class ErroManager(BaseModel):
    """O `/manager` usa `HTTPException` do FastAPI, que devolve `detail`.

    A pública devolve `message`, para casar com o SERPRO. Os dois formatos
    convivem de propósito: um imita contrato de terceiro, o outro é nosso.
    """
    detail: str = Field(examples=["credencial não encontrada"])


class Contratante(BaseModel):
    nome: str = Field(examples=["ACME Indústria Ltda"])
    documento: str | None = Field(default=None, examples=["11222333000181"])
    email: str | None = Field(default=None, examples=["ti@acme.com.br"])


class Credencial(BaseModel):
    consumerKey: str = Field(examples=["djaR21PGoYp1iyK2n2ACOH9REdUb"])
    contratante: Contratante
    criadaEm: str = Field(examples=["2026-09-20T18:30:00+00:00"])
    status: str = Field(examples=["ativa"],
                        description="`ativa`, `suspensa` ou `revogada`")
    revogadaEm: str | None = None
    quotaMensal: int | None = Field(
        default=None,
        description="Limite contratado de consultas faturáveis por mês. "
                    "Nulo = sem limite. Estourar devolve 403 na API pública.",
    )
    observacao: str | None = None


class CredencialComConsumo(Credencial):
    consumoDoMes: int = Field(
        default=0,
        description="Consultas faturáveis na competência corrente, vindas do "
                    "log consolidado.",
    )


class CredencialCriada(Credencial):
    consumerSecret: str = Field(
        examples=["ObRsAJWOL4fv2Tp27D1vd8fB3Ote"],
        description="APARECE UMA ÚNICA VEZ. Só o hash é guardado; não há como "
                    "recuperá-lo depois, apenas rotacionar.",
    )
    aviso: str


class SegredoRotacionado(BaseModel):
    consumerKey: str
    consumerSecret: str = Field(
        description="APARECE UMA ÚNICA VEZ. O segredo anterior deixa de valer "
                    "imediatamente.",
    )
    aviso: str


class ListaCredenciais(BaseModel):
    credenciais: list[Credencial] = Field(default_factory=list)


class UsoMensal(BaseModel):
    competencia: str = Field(examples=["2026-09"])
    consultas: int
    faturaveis: int


class Estatisticas(BaseModel):
    consumerKey: str | None = Field(
        default=None, description="Nulo no resumo geral.")
    competencia: str | None = None
    consultas: int = 0
    faturaveis: int = Field(
        default=0,
        description="Exclui 400, 401, 403, 500, 502 e 504 — os códigos que o "
                    "SERPRO documenta como transações não faturáveis.",
    )
    duracaoMediaMs: float = 0.0
    porRota: dict[str, int] = Field(default_factory=dict)
    porStatus: dict[str, int] = Field(default_factory=dict)
    porMes: list[UsoMensal] = Field(default_factory=list)


class EstatisticasDaCredencial(Estatisticas):
    quotaMensal: int | None = None
    consumoDoMes: int = 0
    percentualDaQuota: float | None = Field(
        default=None, description="Ausente quando não há quota contratada.")


class Consolidacao(BaseModel):
    arquivos: int = Field(description="Arquivos de log lidos nesta chamada.")
    linhas: int


class Poda(BaseModel):
    arquivosApagados: int


NAO_ENCONTRADA = {404: {"model": ErroManager,
                        "description": "Credencial não encontrada"}}
ENTRADA_INVALIDA = {400: {"model": ErroManager,
                          "description": "Requisição inválida"}}
