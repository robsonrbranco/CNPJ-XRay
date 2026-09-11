#!/usr/bin/env python3
"""Relatório de qualidade da base CNPJ carregada.

    python -m src.validation.qualidade              relatório da base nova
    python -m src.validation.qualidade --producao   relatório da produção

Por que isto existe e por que é RELATÓRIO, não validação
--------------------------------------------------------
Dado público brasileiro chega com inconsistência: código de domínio repetido,
estabelecimento cujo CNPJ básico não aparece em empresa, campo obrigatório
vazio. Nada disso é erro do ETL — é o que a Receita publicou.

Por isso o schema não declara PRIMARY KEY, NOT NULL nem FOREIGN KEY: uma trava
transformaria cada uma dessas ocorrências numa exceção no meio de uma carga de
220 milhões de linhas. A base recebe o que a fonte mandou, e a conferência
acontece aqui, depois, sobre a base pronta — medindo o estrago em vez de
abortar por causa dele.

O resultado serve para duas coisas: saber o que esperar ao consultar (código
órfão existe, então `JOIN` com domínio tem que ser `LEFT JOIN`) e comparar
competências (uma variação brusca no número de órfãos indica mudança de layout
na origem, não flutuação dos dados).

As contagens varrem tabelas de dezenas de milhões de linhas; o relatório
completo leva minutos.
"""

import argparse
import logging
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
load_dotenv(_PROJECT_ROOT / ".env")
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.db import schema  # noqa: E402
from src.db.config import FirebirdConfig, load_config  # noqa: E402
from src.db.connection import conectar  # noqa: E402

console = Console()
logger = logging.getLogger(__name__)

# Chave "natural" de cada tabela de fato. Não é PRIMARY KEY no banco — é o que
# a Receita descreve como identificador do registro, e serve para medir quanta
# duplicata a fonte trouxe.
CHAVES: dict[str, tuple[str, ...]] = {
    "empresa": ("cnpj_basico",),
    "estabelecimento": ("cnpj_basico", "cnpj_ordem", "cnpj_dv"),
    "simples": ("cnpj_basico",),
    **{t: ("codigo",) for t in schema.DOMAIN_TABLES},
    # `socios` fica de fora: o mesmo CNPJ tem vários sócios e a RFB mascara o
    # CPF, então não existe identificador de linha para conferir.
}

# Referências que deveriam apontar para uma tabela de domínio.
REFERENCIAS: tuple[tuple[str, str, str], ...] = (
    ("estabelecimento", "municipio", "municipio"),
    ("estabelecimento", "cnae_fiscal_principal", "cnae"),
    ("estabelecimento", "motivo_situacao_cadastral", "motivo"),
    ("estabelecimento", "pais", "pais"),
    ("empresa", "natureza_juridica", "natureza"),
    ("empresa", "qualificacao_responsavel", "qualificacao"),
    ("socios", "qualificacao_socio", "qualificacao"),
    ("socios", "qualificacao_repres_legal", "qualificacao"),
    ("socios", "pais", "pais"),
)

# Tabelas cujo cnpj_basico deveria existir em empresa.
FILHAS = ("estabelecimento", "socios", "simples")


@dataclass
class Achado:
    categoria: str
    alvo: str
    total: int
    afetados: int
    detalhe: str = ""

    @property
    def pct(self) -> float:
        return self.afetados * 100 / self.total if self.total else 0.0


@dataclass
class Relatorio:
    contagens: dict[str, int] = field(default_factory=dict)
    duplicatas: list[Achado] = field(default_factory=list)
    orfaos: list[Achado] = field(default_factory=list)
    sem_pai: list[Achado] = field(default_factory=list)
    nulos: list[Achado] = field(default_factory=list)
    segundos: float = 0.0

    @property
    def limpa(self) -> bool:
        return not any(a.afetados for a in self.duplicatas + self.orfaos + self.sem_pai)


def _um(cur, sql: str) -> int:
    cur.execute(sql)
    linha = cur.fetchone()
    return int(linha[0]) if linha and linha[0] is not None else 0


def gerar(cfg: FirebirdConfig | None = None) -> Relatorio:
    cfg = cfg or load_config()
    rel = Relatorio()
    inicio = time.time()

    with conectar(cfg) as con:
        cur = con.cursor()

        for tabela in schema.TABLES:
            rel.contagens[tabela] = _um(cur, f"SELECT COUNT(*) FROM {tabela}")

        # -- duplicatas na chave natural --------------------------------
        for tabela, chave in CHAVES.items():
            total = rel.contagens.get(tabela, 0)
            if not total:
                continue
            cols = ", ".join(chave)
            # Linhas que participam de uma chave repetida, não número de chaves.
            excedentes = _um(cur, f"""
                SELECT COALESCE(SUM(n - 1), 0) FROM (
                    SELECT COUNT(*) AS n FROM {tabela}
                     GROUP BY {cols} HAVING COUNT(*) > 1
                )
            """)
            rel.duplicatas.append(
                Achado("duplicata", f"{tabela} ({cols})", total, excedentes)
            )

        # -- referências órfãs ------------------------------------------
        for tabela, coluna, dominio in REFERENCIAS:
            total = rel.contagens.get(tabela, 0)
            if not total:
                continue
            orfaos = _um(cur, f"""
                SELECT COUNT(*) FROM {tabela} f
                 WHERE f.{coluna} IS NOT NULL
                   AND NOT EXISTS (SELECT 1 FROM {dominio} d WHERE d.codigo = f.{coluna})
            """)
            rel.orfaos.append(
                Achado("órfão", f"{tabela}.{coluna}", total, orfaos, f"-> {dominio}")
            )

        # -- filhas sem empresa correspondente ---------------------------
        for tabela in FILHAS:
            total = rel.contagens.get(tabela, 0)
            if not total:
                continue
            sem = _um(cur, f"""
                SELECT COUNT(*) FROM {tabela} f
                 WHERE NOT EXISTS (
                     SELECT 1 FROM empresa e WHERE e.cnpj_basico = f.cnpj_basico
                 )
            """)
            rel.sem_pai.append(
                Achado("sem pai", tabela, total, sem, "-> empresa")
            )

        # -- nulos onde a RFB promete valor ------------------------------
        for tabela, coluna in (
            ("empresa", "cnpj_basico"), ("empresa", "razao_social"),
            ("estabelecimento", "cnpj_basico"), ("estabelecimento", "situacao_cadastral"),
            ("socios", "cnpj_basico"), ("simples", "cnpj_basico"),
        ):
            total = rel.contagens.get(tabela, 0)
            if not total:
                continue
            n = _um(cur, f"SELECT COUNT(*) FROM {tabela} WHERE {coluna} IS NULL")
            rel.nulos.append(Achado("nulo", f"{tabela}.{coluna}", total, n))

    rel.segundos = time.time() - inicio
    return rel


def imprimir(rel: Relatorio) -> None:
    t = Table(title="Linhas por tabela", show_header=True, header_style="bold cyan")
    t.add_column("Tabela")
    t.add_column("Linhas", justify="right")
    for tabela, n in rel.contagens.items():
        t.add_row(tabela, f"{n:,}")
    t.add_row("[bold]TOTAL[/bold]", f"[bold]{sum(rel.contagens.values()):,}[/bold]")
    console.print()
    console.print(t)

    for titulo, achados in (
        ("Duplicatas na chave natural", rel.duplicatas),
        ("Referências órfãs (código sem correspondente no domínio)", rel.orfaos),
        ("Registros sem empresa correspondente", rel.sem_pai),
        ("Nulos onde a Receita promete valor", rel.nulos),
    ):
        if not achados:
            continue
        t = Table(title=titulo, show_header=True, header_style="bold cyan")
        t.add_column("Alvo")
        t.add_column("Afetados", justify="right")
        t.add_column("%", justify="right")
        t.add_column("Total", justify="right")
        for a in achados:
            cor = "green" if a.afetados == 0 else ("yellow" if a.pct < 1 else "red")
            t.add_row(
                f"{a.alvo} {a.detalhe}".strip(),
                f"[{cor}]{a.afetados:,}[/{cor}]",
                f"[{cor}]{a.pct:.3f}%[/{cor}]",
                f"{a.total:,}",
            )
        console.print()
        console.print(t)

    console.print(f"\n[dim]Relatório em {rel.segundos:.1f}s[/dim]")
    if rel.limpa:
        console.print("[bold green]Nenhuma inconsistência encontrada.[/bold green]")
    else:
        console.print(
            "[yellow]Inconsistências acima vieram da fonte — a base carrega o que a "
            "Receita publicou. Use LEFT JOIN com as tabelas de domínio.[/yellow]"
        )


def main() -> None:
    p = argparse.ArgumentParser(prog="qualidade", description=__doc__.split("\n")[0])
    p.add_argument(
        "--producao", action="store_true",
        help="analisa a base em produção em vez da base nova (staging)",
    )
    args = p.parse_args()

    logging.basicConfig(level=logging.WARNING, format="%(levelname)-7s %(message)s")

    cfg = load_config()
    if not args.producao:
        cfg = FirebirdConfig(**{**cfg.__dict__, "database": cfg.staging_database})

    console.print(f"\n[bold magenta]Qualidade — {Path(cfg.database).name}[/bold magenta]")
    imprimir(gerar(cfg))


if __name__ == "__main__":
    main()
