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
    PESOS_DV1,
    PESOS_DV2,
    CNPJInvalido,
    digitos_verificadores,
    formatar,
    limpar,
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
    with pytest.raises(CNPJInvalido, match="14 caracteres"):
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


# ---------------------------------------------------------------------------
# CNPJ alfanumérico (IN RFB nº 2.229/2024)
# ---------------------------------------------------------------------------

# O exemplo do manual de DV da Receita: 12ABC34501DE -> 35.
EXEMPLO_OFICIAL = "12ABC34501DE35"
# O primeiro CNPJ alfanumérico emitido (31/07/2026, filial do Banco do Brasil),
# anunciado pela Receita e já presente na competência 2026-09 dos dados abertos
# (Estabelecimentos5.zip: "00000000";"E08G";"12").
PRIMEIRO_EMITIDO = "00000000E08G12"


def test_exemplo_do_manual_da_receita():
    assert digitos_verificadores("12ABC34501DE") == "35"
    assert valido(EXEMPLO_OFICIAL)
    assert valido("12.ABC.345/01DE-35")


def test_primeiro_cnpj_alfanumerico_emitido():
    """Dado real, publicado pela Receita — não fabricado a partir do algoritmo.
    É também o que distingue a regra certa da ingênua: com 'A'=10 (base 36) o
    DV daria 60, não 12."""
    assert digitos_verificadores("00000000E08G") == "12"
    assert valido(PRIMEIRO_EMITIDO)
    assert partes("00.000.000/E08G-12") == ("00000000", "E08G", "12")
    assert formatar(PRIMEIRO_EMITIDO) == "00.000.000/E08G-12"


def test_letra_vale_codigo_ascii_menos_48():
    """A=17 ... Z=42. Base só com uma letra na última posição da raiz, para o
    valor dela aparecer isolado na soma do primeiro DV."""
    for letra in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        base = "0000000" + letra + "0000"
        soma = (ord(letra) - 48) * PESOS_DV1[7]
        resto = soma % 11
        assert digitos_verificadores(base)[0] == str(0 if resto < 2 else 11 - resto)


def test_minusculas_e_pontuacao_sao_normalizadas():
    """A Receita só emite maiúscula; aceitar minúscula é a mesma cortesia da
    pontuação — a forma canônica que sai é sempre maiúscula."""
    assert normalizar("12.abc.345/01de-35") == EXEMPLO_OFICIAL
    assert normalizar("12aBc34501dE35") == EXEMPLO_OFICIAL
    assert normalizar(" 00.000.000/e08g-12 ") == PRIMEIRO_EMITIDO


def test_recusa_dv_errado_no_alfanumerico():
    for c in ("12ABC34501DE36", "12ABC34501DE53", "00000000E08G13",
              "00000000E08H12"):
        assert not valido(c), c
    with pytest.raises(CNPJInvalido, match="verificador"):
        normalizar("12ABC34501DE36")


def test_dv_e_sempre_numerico():
    with pytest.raises(CNPJInvalido, match="malformado"):
        normalizar("12ABC34501DE3A")
    assert not valido("12ABC34501DEAB")


def test_recusa_caractere_fora_de_0_9_A_Z():
    """Letra acentuada, dígito de outro alfabeto e letra que muda de tamanho
    ao subir de caixa não viram CNPJ válido. 'ß0000000001' + '24' tem 13
    caracteres, mas com `str.upper()` viraria SS0000000001-24, que é válido."""
    for c in ("12ÁBC34501DE35", "12ABC34501DE3٥", "ß000000000124",
              "0831488500010²"):
        assert not valido(c), c
        with pytest.raises(CNPJInvalido):
            normalizar(c)


def test_letra_nunca_e_descartada():
    """O defeito que isto corrige: descartar o que não era dígito podia
    transformar a entrada em OUTRO CNPJ, válido e existente — resposta errada
    e cobrada. Uma letra enfiada num CNPJ numérico tem que dar 400."""
    for c in ("08314885A000105", "A08314885000105", "0831488500010X5",
              "08.314.885/0001-05X"):
        assert not valido(c), c
    assert limpar("08.314.885/0001-05X") == "08314885000105X"


def test_limpar_so_tira_pontuacao():
    assert limpar("12.abc.345/01de-35") == EXEMPLO_OFICIAL
    assert limpar("08.314.885/0001-05") == "08314885000105"
    assert limpar("") == ""


# ---------------------------------------------------------------------------
# os numéricos não mudam
# ---------------------------------------------------------------------------

def _dv_numerico_antigo(digitos: str, pesos) -> int:
    # A implementação anterior à 3.3.0, só para dígitos, copiada literalmente.
    resto = sum(int(d) * p for d, p in zip(digitos, pesos)) % 11
    return 0 if resto < 2 else 11 - resto


def _dvs_antigos(base12: str) -> str:
    dv1 = _dv_numerico_antigo(base12, PESOS_DV1)
    dv2 = _dv_numerico_antigo(base12 + str(dv1), PESOS_DV2)
    return f"{dv1}{dv2}"


def test_numericos_tem_o_mesmo_dv_da_regra_antiga():
    """ASCII-48 de um dígito é o próprio dígito, então a regra nova tem que
    reproduzir a antiga para TODO CNPJ numérico. Conferido numa amostra fixa
    de 200 mil bases, cobrindo a faixa inteira de 12 dígitos."""
    import random
    rnd = random.Random(20260731)
    bases = [f"{rnd.randrange(10**12):012d}" for _ in range(200_000)]
    bases += [c[:12] for c in REAIS]
    for base in bases:
        assert digitos_verificadores(base) == _dvs_antigos(base), base


def test_numericos_normalizam_como_antes():
    for entrada, esperado in (
        ("08.314.885/0001-05", "08314885000105"),
        ("08314885000105", "08314885000105"),
        (" 08314885 0001 05 ", "08314885000105"),
        ("08314885/0002-96", "08314885000296"),
    ):
        assert normalizar(entrada) == esperado
