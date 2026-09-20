#!/usr/bin/env python3
"""Consulta a ficha completa de uma empresa por CNPJ.

    python -m src.consulta.empresa 08314885
    python -m src.consulta.empresa 08.314.885/0001-00
    python -m src.consulta.empresa 08314885 --json

Porte da versão PostgreSQL (`auxiliary/python/consultar_empresa.py`). Duas
coisas mudaram além do dialeto, e ambas eram defeito lá:

* **Comparação de código com string.** O original filtrava
  `porte_empresa = '01'` e `situacao_cadastral = '02'` porque no PostgreSQL
  essas colunas eram texto. Aqui são SMALLINT, e comparar contra `'01'` daria
  erro de conversão ou, pior, silêncio. Os mapas de descrição abaixo são por
  inteiro, e ficam em Python em vez de num `CASE` gigante dentro do SQL —
  tabela de domínio que não muda não precisa ir e voltar do servidor.
* **JOIN com domínio tem que ser LEFT.** A base não tem FOREIGN KEY de
  propósito (a carga é otimista) e `src/validation/qualidade.py` mostra que
  existe código órfão real. `INNER JOIN` com domínio faria a linha sumir da
  ficha por causa de um código que a Receita publicou errado.
"""

import argparse
import json
import sys
from decimal import Decimal

from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table

from ..db import connection

load_dotenv()
console = Console()

PORTE = {1: "Microempresa", 3: "Empresa de Pequeno Porte", 5: "Demais"}
SITUACAO = {1: "NULA", 2: "ATIVA", 3: "SUSPENSA", 4: "INAPTA", 8: "BAIXADA"}
TIPO_SOCIO = {1: "PESSOA JURÍDICA", 2: "PESSOA FÍSICA", 3: "ESTRANGEIRO"}
FAIXA_ETARIA = {
    0: "Não se aplica", 1: "0 a 12 anos", 2: "13 a 20 anos", 3: "21 a 30 anos",
    4: "31 a 40 anos", 5: "41 a 50 anos", 6: "51 a 80 anos", 8: "Maior de 80 anos",
}
SIM_NAO = {"S": "SIM", "N": "NÃO"}


def _descrever(mapa: dict, valor, padrao: str = "não informado") -> str:
    return mapa.get(valor, padrao)


def formatar_cnpj(cnpj: str) -> str:
    if len(cnpj) == 14:
        return f"{cnpj[:2]}.{cnpj[2:5]}.{cnpj[5:8]}/{cnpj[8:12]}-{cnpj[12:]}"
    if len(cnpj) == 8:
        return f"{cnpj[:2]}.{cnpj[2:5]}.{cnpj[5:8]}"
    return cnpj


def normalizar_cnpj(entrada: str) -> str:
    """Aceita CNPJ com ou sem pontuação, básico (8) ou completo (14)."""
    digitos = "".join(c for c in entrada if c.isdigit())
    if len(digitos) not in (8, 14):
        raise ValueError(
            f"CNPJ deve ter 8 dígitos (básico) ou 14 (completo), recebeu {len(digitos)}"
        )
    return digitos


# ---------------------------------------------------------------------------
# consultas
# ---------------------------------------------------------------------------

SQL_EMPRESA = """
SELECT e.cnpj_basico, e.razao_social, e.natureza_juridica, nj.descricao,
       e.qualificacao_responsavel, qr.descricao, e.capital_social,
       e.porte_empresa, e.ente_federativo_responsavel
FROM empresa e
LEFT JOIN natureza nj ON e.natureza_juridica = nj.codigo
LEFT JOIN qualificacao qr ON e.qualificacao_responsavel = qr.codigo
WHERE e.cnpj_basico = ?
"""

SQL_ESTABELECIMENTOS = """
SELECT est.cnpj_basico, est.cnpj_ordem, est.cnpj_dv,
       est.identificador_matriz_filial, est.nome_fantasia,
       est.situacao_cadastral, est.data_situacao_cadastral,
       est.motivo_situacao_cadastral, mo.descricao,
       est.situacao_especial, est.data_situacao_especial,
       est.data_inicio_atividade, est.cnae_fiscal_principal, cn.descricao,
       est.cnae_fiscal_secundaria, est.tipo_logradouro, est.logradouro,
       est.numero, est.complemento, est.bairro, est.cep, est.uf,
       est.municipio, mu.descricao, est.pais, pa.descricao,
       est.ddd_1, est.telefone_1, est.ddd_2, est.telefone_2,
       est.correio_eletronico
FROM estabelecimento est
LEFT JOIN motivo mo ON est.motivo_situacao_cadastral = mo.codigo
LEFT JOIN cnae cn ON est.cnae_fiscal_principal = cn.codigo
LEFT JOIN municipio mu ON est.municipio = mu.codigo
LEFT JOIN pais pa ON est.pais = pa.codigo
WHERE est.cnpj_basico = ?
ORDER BY est.cnpj_ordem
"""

SQL_SOCIOS = """
SELECT s.identificador_socio, s.nome_socio, s.cnpj_cpf_socio,
       s.qualificacao_socio, q.descricao, s.data_entrada_sociedade,
       s.pais, p.descricao, s.faixa_etaria, s.nome_representante
FROM socios s
LEFT JOIN qualificacao q ON s.qualificacao_socio = q.codigo
LEFT JOIN pais p ON s.pais = p.codigo
WHERE s.cnpj_basico = ?
ORDER BY s.nome_socio
"""

SQL_SIMPLES = """
SELECT opcao_pelo_simples, data_opcao_simples, data_exclusao_simples,
       opcao_mei, data_opcao_mei, data_exclusao_mei
FROM simples WHERE cnpj_basico = ?
"""

SQL_CNAES = """
SELECT codigo, descricao FROM cnae WHERE codigo IN ({}) ORDER BY codigo
"""


def consultar(cnpj_basico: str) -> dict:
    """Devolve a ficha inteira numa única conexão."""
    ficha: dict = {"cnpj_basico": cnpj_basico}
    with connection.conectar() as con:
        cur = con.cursor()

        cur.execute(SQL_EMPRESA, (cnpj_basico,))
        linha = cur.fetchone()
        ficha["empresa"] = None if linha is None else {
            "cnpj_basico": linha[0], "razao_social": linha[1],
            "natureza_juridica": linha[2], "natureza_juridica_descricao": linha[3],
            "qualificacao_responsavel": linha[4],
            "qualificacao_responsavel_descricao": linha[5],
            "capital_social": linha[6], "porte_empresa": linha[7],
            "porte_empresa_descricao": _descrever(PORTE, linha[7]),
            "ente_federativo_responsavel": linha[8],
        }

        cur.execute(SQL_ESTABELECIMENTOS, (cnpj_basico,))
        estabelecimentos = []
        for r in cur.fetchall():
            # Campos CRUS, um por coluna. Juntar `tipo_logradouro + logradouro
            # + numero` num campo só, ou montar o telefone como "(14) 3496...",
            # é decisão de apresentação — e destrói estrutura que outro
            # consumidor precisa. A API compatível com o SERPRO quer
            # `tipoLogradouro`, `logradouro` e `numero` separados, e
            # `telefone[]{ddd, numero}`. Quem exibe é que junta.
            estabelecimentos.append({
                "cnpj_basico": r[0], "cnpj_ordem": r[1], "cnpj_dv": r[2],
                "cnpj_completo": f"{r[0]}{r[1]}{r[2]}",
                "identificador_matriz_filial": r[3],
                "tipo": "MATRIZ" if r[3] == 1 else "FILIAL",
                "nome_fantasia": r[4],
                "situacao_cadastral": r[5],
                "situacao_cadastral_descricao": _descrever(SITUACAO, r[5], "DESCONHECIDA"),
                "data_situacao_cadastral": r[6],
                "motivo_situacao_cadastral": r[7], "motivo_descricao": r[8],
                "situacao_especial": r[9], "data_situacao_especial": r[10],
                "data_inicio_atividade": r[11],
                "cnae_fiscal_principal": r[12], "cnae_principal_descricao": r[13],
                "cnae_fiscal_secundaria": r[14],
                "tipo_logradouro": r[15], "logradouro": r[16], "numero": r[17],
                "complemento": r[18], "bairro": r[19], "cep": r[20],
                "uf": r[21], "municipio": r[22], "municipio_descricao": r[23],
                "pais": r[24], "pais_descricao": r[25],
                "ddd_1": r[26], "telefone_1": r[27],
                "ddd_2": r[28], "telefone_2": r[29],
                "correio_eletronico": r[30],
            })
        ficha["estabelecimentos"] = estabelecimentos

        codigos = {
            int(c) for est in estabelecimentos
            for c in (est["cnae_fiscal_secundaria"] or "").split(",")
            if c.strip().isdigit()
        }
        ficha["cnaes_secundarios"] = {}
        if codigos:
            marcas = ", ".join("?" * len(codigos))
            cur.execute(SQL_CNAES.format(marcas), tuple(sorted(codigos)))
            ficha["cnaes_secundarios"] = dict(cur.fetchall())

        cur.execute(SQL_SOCIOS, (cnpj_basico,))
        ficha["socios"] = [{
            "identificador_socio": r[0],
            "tipo": _descrever(TIPO_SOCIO, r[0], "DESCONHECIDO"),
            "nome_socio": r[1], "cnpj_cpf_socio": r[2],
            "qualificacao_socio": r[3], "qualificacao_descricao": r[4],
            "data_entrada_sociedade": r[5],
            "pais": r[6], "pais_descricao": r[7],
            "faixa_etaria": r[8],
            "faixa_etaria_descricao": _descrever(FAIXA_ETARIA, r[8]),
            "nome_representante": r[9],
        } for r in cur.fetchall()]

        cur.execute(SQL_SIMPLES, (cnpj_basico,))
        linha = cur.fetchone()
        ficha["simples"] = None if linha is None else {
            "opcao_pelo_simples": linha[0],
            "opcao_simples_descricao": _descrever(SIM_NAO, linha[0], "NÃO INFORMADO"),
            "data_opcao_simples": linha[1], "data_exclusao_simples": linha[2],
            "opcao_mei": linha[3],
            "opcao_mei_descricao": _descrever(SIM_NAO, linha[3], "NÃO INFORMADO"),
            "data_opcao_mei": linha[4], "data_exclusao_mei": linha[5],
        }
    return ficha


# ---------------------------------------------------------------------------
# apresentação
# ---------------------------------------------------------------------------

def _tabela(titulo: str) -> Table:
    t = Table(title=titulo, show_header=False, box=None, title_justify="left")
    t.add_column(style="cyan", no_wrap=True)
    t.add_column(style="white")
    return t


def exibir(ficha: dict) -> None:
    emp = ficha["empresa"]
    t = _tabela("EMPRESA")
    t.add_row("CNPJ básico", formatar_cnpj(emp["cnpj_basico"]))
    t.add_row("Razão social", emp["razao_social"] or "—")
    t.add_row("Natureza jurídica",
              f"{emp['natureza_juridica']} — {emp['natureza_juridica_descricao'] or '?'}")
    t.add_row("Porte", emp["porte_empresa_descricao"])
    t.add_row("Capital social", f"R$ {emp['capital_social'] or Decimal(0):,.2f}")
    t.add_row("Responsável", emp["qualificacao_responsavel_descricao"] or "—")
    if emp["ente_federativo_responsavel"]:
        t.add_row("Ente federativo", emp["ente_federativo_responsavel"])
    console.print(t)

    for est in ficha["estabelecimentos"]:
        console.print()
        t = _tabela(f"{est['tipo']} — {formatar_cnpj(est['cnpj_completo'])}")
        if est["nome_fantasia"]:
            t.add_row("Nome fantasia", est["nome_fantasia"])
        situacao = est["situacao_cadastral_descricao"]
        if est["motivo_descricao"]:
            situacao += f" ({est['motivo_descricao']})"
        t.add_row("Situação", f"{situacao} desde {est['data_situacao_cadastral'] or '?'}")
        t.add_row("Início atividade", str(est["data_inicio_atividade"] or "—"))
        t.add_row("CNAE principal",
                  f"{est['cnae_fiscal_principal']} — {est['cnae_principal_descricao'] or '?'}")
        secundarios = [
            f"{c} — {ficha['cnaes_secundarios'].get(int(c), '?')}"
            for c in (est["cnae_fiscal_secundaria"] or "").split(",") if c.strip().isdigit()
        ]
        if secundarios:
            t.add_row("CNAE secundários", "\n".join(secundarios))
        # A junção acontece AQUI, na exibição — `consultar()` devolve cru.
        rua = " ".join(x for x in (est["tipo_logradouro"], est["logradouro"],
                                   est["numero"]) if x)
        endereco = ", ".join(x for x in (
            rua, est["complemento"], est["bairro"],
            est["municipio_descricao"], est["uf"], est["cep"]) if x)
        t.add_row("Endereço", endereco or "—")
        fones = [f"({d}) {n}" if d else n
                 for d, n in ((est["ddd_1"], est["telefone_1"]),
                              (est["ddd_2"], est["telefone_2"])) if n]
        if fones:
            t.add_row("Telefone", "  ".join(fones))
        if est["correio_eletronico"]:
            t.add_row("E-mail", est["correio_eletronico"])
        console.print(t)

    if ficha["socios"]:
        console.print()
        t = Table(title=f"SÓCIOS ({len(ficha['socios'])})", title_justify="left")
        for col in ("Nome", "Tipo", "CPF/CNPJ", "Qualificação", "Entrada", "Faixa etária"):
            t.add_column(col)
        for s in ficha["socios"]:
            t.add_row(s["nome_socio"] or "—", s["tipo"], s["cnpj_cpf_socio"] or "—",
                      s["qualificacao_descricao"] or "—",
                      str(s["data_entrada_sociedade"] or "—"),
                      s["faixa_etaria_descricao"])
        console.print(t)

    sim = ficha["simples"]
    console.print()
    t = _tabela("SIMPLES NACIONAL")
    if sim is None:
        t.add_row("Situação", "sem registro na tabela do Simples")
    else:
        t.add_row("Optante", sim["opcao_simples_descricao"])
        t.add_row("Opção Simples", str(sim["data_opcao_simples"] or "—"))
        t.add_row("Exclusão Simples", str(sim["data_exclusao_simples"] or "—"))
        t.add_row("MEI", sim["opcao_mei_descricao"])
        t.add_row("Opção MEI", str(sim["data_opcao_mei"] or "—"))
        t.add_row("Exclusão MEI", str(sim["data_exclusao_mei"] or "—"))
    console.print(t)


def _serializar(o):
    if isinstance(o, Decimal):
        return float(o)
    return str(o)


def main() -> int:
    p = argparse.ArgumentParser(
        prog="consulta.empresa",
        description="Ficha completa de uma empresa por CNPJ",
    )
    p.add_argument("cnpj", help="CNPJ básico (8 dígitos) ou completo (14), com ou sem pontuação")
    p.add_argument("--json", action="store_true", help="imprime a ficha em JSON")
    args = p.parse_args()

    try:
        digitos = normalizar_cnpj(args.cnpj)
    except ValueError as e:
        console.print(f"[bold red]{e}[/bold red]")
        return 2

    ficha = consultar(digitos[:8])
    if ficha["empresa"] is None:
        console.print(f"[bold red]CNPJ {formatar_cnpj(digitos)} não está na base[/bold red]")
        return 1

    if args.json:
        print(json.dumps(ficha, ensure_ascii=False, indent=2, default=_serializar))
    else:
        exibir(ficha)
    return 0


if __name__ == "__main__":
    sys.exit(main())
