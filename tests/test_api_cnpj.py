"""Testes da validação de CNPJ.

Isto separa HTTP 400 de 404 — e, pela regra do SERPRO, 404 é transação
faturável e 400 não. Errar aqui cobra do contratante por requisição que ele
mandou errada, ou deixa de cobrar por consulta legitimamente prestada.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.api.cnpj import (  # noqa: E402
    CNPJInvalido,
    digitos_verificadores,
    formatar,
    normalizar,
    partes,
    valido,
)

# CNPJ reais da base, conferidos contra ela.
REAIS = ["08314885000105", "08314885000296"]


def test_aceita_cnpj_real_da_base():
    for c in REAIS:
        assert valido(c), c


def test_calcula_os_dois_digitos_verificadores():
    for c in REAIS:
        assert digitos_verificadores(c[:12]) == c[12:]


def test_recusa_digito_verificador_errado():
    """Um dígito trocado no fim tem que reprovar — é o caso comum de erro de
    digitação, e o que justifica a validação existir."""
    assert not valido("08314885000106")
    assert not valido("08314885000115")


def test_recusa_comprimento_errado():
    for c in ("", "08314885", "0831488500010", "083148850001055"):
        assert not valido(c)


def test_recusa_sequencia_de_digito_repetido():
    """11111111111111 passa no módulo 11 por acidente aritmético. São valores
    de teste, não CNPJ — se passassem, virariam 404 em vez de 400."""
    for d in "0123456789":
        assert not valido(d * 14), d * 14


def test_normalizar_aceita_com_e_sem_pontuacao():
    assert normalizar("08.314.885/0001-05") == "08314885000105"
    assert normalizar("08314885000105") == "08314885000105"
    assert normalizar(" 08314885000105 ") == "08314885000105"


def test_normalizar_levanta_em_comprimento_errado():
    with pytest.raises(CNPJInvalido, match="14 dígitos"):
        normalizar("08314885")


def test_normalizar_levanta_em_dv_errado():
    with pytest.raises(CNPJInvalido, match="verificador"):
        normalizar("08314885000106")


def test_partes_quebra_como_as_colunas_da_base():
    assert partes("08314885000105") == ("08314885", "0001", "05")


def test_formatar():
    assert formatar("08314885000105") == "08.314.885/0001-05"
    assert formatar("08314885") == "08.314.885"


def test_dv_zero_e_tratado():
    """Resto < 2 produz DV 0. Um cálculo que não trate esse caso erra em ~18%
    dos CNPJ."""
    achou_zero = False
    for n in range(10_000):
        base = f"{n:012d}"
        dv = digitos_verificadores(base)
        assert valido(base + dv) or len(set(base + dv)) == 1
        if "0" in dv:
            achou_zero = True
    assert achou_zero, "a varredura não cobriu DV zero"
