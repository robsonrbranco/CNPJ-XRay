"""Tradução da ficha interna para o esquema da Consulta CNPJ v2 do SERPRO.

Transformação pura: entra o dicionário que `src.consulta.empresa.consultar()`
devolve, sai o objeto no formato deles. Não toca banco nem HTTP, o que a torna
testável sem nenhum dos dois.

Três endpoints, três recortes do mesmo dado:

    basica(ficha, ni)    dados cadastrais, SEM sócios
    qsa(ficha, ni)       apenas o quadro de sócios e administradores
    empresa(ficha, ni)   o conjunto completo

Uma diferença de forma entre as duas pontas
-------------------------------------------
`consultar()` recebe o CNPJ básico (8 posições) e devolve **todos** os
estabelecimentos. O SERPRO recebe o `ni` completo (14 posições) e responde sobre
**um**. A seleção acontece aqui, e um `ni` bem formado cujo estabelecimento não
existe é `EstabelecimentoNaoEncontrado` — que o chamador traduz para 404.

Sobre os códigos
----------------
Os valores de `porte` e `tipoEstabelecimento` seguem a codificação do layout da
Receita, da qual o SERPRO também deriva. **Não verifiquei contra uma resposta
real do SERPRO** se eles emitem o código com zero à esquerda (`"01"`) ou sem
(`"1"`). Adotei a forma de dois dígitos do layout; se um cliente real reclamar,
é aqui que se ajusta, e é ajuste de uma linha.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal


class EstabelecimentoNaoEncontrado(LookupError):
    """O CNPJ é válido mas este estabelecimento não está na base — vira 404."""


def _data(valor) -> str | None:
    """Datas saem como AAAA-MM-DD, como no contrato."""
    if valor is None:
        return None
    if isinstance(valor, date):
        return valor.isoformat()
    return str(valor)[:10]


def _codigo(valor, largura: int = 2) -> str | None:
    return None if valor is None else str(valor).zfill(largura)


def _centavos(valor) -> int:
    """`capitalSocial` do SERPRO é inteiro em centavos.

    A conversão passa por Decimal e não por float: `float(1234.56) * 100` dá
    123455.99999999999, e truncar isso perde um centavo. Em capital social de
    532 bilhões — que existe na base — o erro deixa de ser acadêmico.
    """
    if valor is None:
        return 0
    return int((Decimal(str(valor)) * 100).to_integral_value())


def _dominio(codigo, descricao) -> dict | None:
    """Objeto {codigo, descricao}, ou None quando não há código.

    `descricao` pode vir None mesmo com código presente: a base tem código
    órfão real, e o JOIN com domínio é LEFT justamente para a linha não sumir.
    """
    if codigo is None:
        return None
    return {"codigo": str(codigo), "descricao": descricao}


def _selecionar(ficha: dict, ni: str) -> dict:
    # A ordem pode ter letra (CNPJ alfanumérico). A comparação aqui é sensível
    # a caixa e a do banco (WIN_PTBR) não, mas as duas pontas chegam em
    # maiúsculas: `ni` por `cnpj.normalizar`, e a base porque a Receita só
    # emite letra maiúscula e publica assim (`00000000;E08G;12`, 2026-09).
    ordem, dv = ni[8:12], ni[12:]
    for est in ficha["estabelecimentos"]:
        if est["cnpj_ordem"] == ordem and est["cnpj_dv"] == dv:
            return est
    raise EstabelecimentoNaoEncontrado(ni)


def _endereco(est: dict) -> dict:
    return {
        "tipoLogradouro": est["tipo_logradouro"],
        "logradouro": est["logradouro"],
        "numero": est["numero"],
        "complemento": est["complemento"],
        "cep": est["cep"],
        "bairro": est["bairro"],
        "uf": est["uf"],
        "municipio": _dominio(est["municipio"], est["municipio_descricao"]),
        "pais": _dominio(est["pais"], est["pais_descricao"]),
        # Município de jurisdição fiscal não existe nos dados abertos. O campo
        # sai presente e nulo, e não ausente: um cliente que espera a chave não
        # quebra, e a ausência de valor é a informação honesta.
        "municipioJurisdicao": None,
    }


def _telefones(est: dict) -> list[dict]:
    return [
        {"ddd": ddd, "numero": numero}
        for ddd, numero in ((est["ddd_1"], est["telefone_1"]),
                            (est["ddd_2"], est["telefone_2"]))
        if numero
    ]


def _cnaes_secundarias(est: dict, descricoes: dict) -> list[dict]:
    codigos = [c.strip() for c in (est["cnae_fiscal_secundaria"] or "").split(",")
               if c.strip().isdigit()]
    return [
        {"codigo": c, "descricao": descricoes.get(int(c))}
        for c in codigos
    ]


def _informacoes_adicionais(simples: dict | None) -> dict:
    if simples is None:
        return {"optanteSimples": None, "optanteMei": None,
                "listaPeriodosSimples": []}

    periodos = []
    if simples["data_opcao_simples"]:
        periodos.append({
            "dataInicio": _data(simples["data_opcao_simples"]),
            "dataFim": _data(simples["data_exclusao_simples"]),
        })
    return {
        "optanteSimples": simples["opcao_pelo_simples"],
        "optanteMei": simples["opcao_mei"],
        # O SERPRO devolve o HISTÓRICO de períodos; a Receita publica um par
        # opção/exclusão só. A lista sai com no máximo um elemento, e isso está
        # documentado como incompatibilidade conhecida.
        "listaPeriodosSimples": periodos,
    }


def _socio(s: dict) -> dict:
    representante = None
    if s.get("nome_representante") or s.get("representante_legal"):
        representante = {
            "cpf": s.get("representante_legal"),
            "nome": s.get("nome_representante"),
            "qualificacao": _codigo(s.get("qualificacao_repres_legal")),
        }
    return {
        "tipoSocio": _codigo(s["identificador_socio"], 1),
        # A Receita mascara o CPF nos dados abertos (***794780**). O SERPRO,
        # como canal autorizado, devolve completo. Mesmo campo, conteúdo
        # parcial -- incompatibilidade de fonte, não de código.
        "cpf": s["cnpj_cpf_socio"],
        "nome": s["nome_socio"],
        "qualificacao": _codigo(s["qualificacao_socio"]),
        "dataInclusao": _data(s["data_entrada_sociedade"]),
        "pais": _dominio(s["pais"], s["pais_descricao"]),
        "representanteLegal": representante,
    }


# ---------------------------------------------------------------------------
# os três endpoints
# ---------------------------------------------------------------------------

def basica(ficha: dict, ni: str) -> dict:
    est = _selecionar(ficha, ni)
    emp = ficha["empresa"]

    return {
        "ni": ni,
        "tipoEstabelecimento": _codigo(est["identificador_matriz_filial"], 1),
        "nomeEmpresarial": emp["razao_social"],
        "nomeFantasia": est["nome_fantasia"],
        "dataAbertura": _data(est["data_inicio_atividade"]),
        "situacaoCadastral": {
            "codigo": _codigo(est["situacao_cadastral"]),
            "data": _data(est["data_situacao_cadastral"]),
            "motivo": est["motivo_descricao"],
        },
        "naturezaJuridica": _dominio(emp["natureza_juridica"],
                                     emp["natureza_juridica_descricao"]),
        "dataSituacaoEspecial": _data(est["data_situacao_especial"]),
        "situacaoEspecial": est["situacao_especial"],
        "capitalSocial": _centavos(emp["capital_social"]),
        "porte": _codigo(emp["porte_empresa"]),
        "endereco": _endereco(est),
        "telefone": _telefones(est),
        "correioEletronico": est["correio_eletronico"],
        "cnaePrincipal": _dominio(est["cnae_fiscal_principal"],
                                  est["cnae_principal_descricao"]),
        "cnaeSecundarias": _cnaes_secundarias(est, ficha["cnaes_secundarios"]),
        "informacoesAdicionais": _informacoes_adicionais(ficha["simples"]),
    }


def qsa(ficha: dict, ni: str) -> dict:
    """Quadro de sócios e administradores.

    `_selecionar` roda mesmo sem o resultado ser usado: um `ni` cujo
    estabelecimento não existe tem que dar 404 aqui também, e não uma lista
    vazia de sócios da empresa inteira.
    """
    _selecionar(ficha, ni)
    return {
        "ni": ni,
        "nomeEmpresarial": ficha["empresa"]["razao_social"],
        "socios": [_socio(s) for s in ficha["socios"]],
    }


def empresa(ficha: dict, ni: str) -> dict:
    """Dados cadastrais mais o quadro societário."""
    resposta = basica(ficha, ni)
    resposta["socios"] = [_socio(s) for s in ficha["socios"]]
    return resposta
