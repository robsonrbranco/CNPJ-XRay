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
