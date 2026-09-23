"""Validação e normalização de CNPJ — numérico e alfanumérico.

Existe por causa de uma distinção do contrato do SERPRO que o banco não sabe
fazer: **400 e 404 são casos diferentes**. CNPJ com dígito verificador errado é
400 (o cliente mandou lixo); CNPJ bem formado que não está na base é 404 (a
consulta foi legítima, não achou). Para o banco os dois são igualmente "não
achei", então a separação tem que acontecer antes da consulta.

A diferença não é cosmética: pela regra do SERPRO, 404 é transação faturável e
400 não é.

CNPJ alfanumérico
-----------------
A IN RFB nº 2.229/2024 instituiu o CNPJ alfanumérico, emitido a partir de julho
de 2026: as 12 primeiras posições (8 da raiz, 4 da ordem) aceitam letras
maiúsculas e dígitos, e os 2 dígitos verificadores continuam numéricos. O DV
continua sendo módulo 11 com os mesmos pesos; a única mudança é o valor de cada
posição, que passa a ser o código ASCII menos 48 — `'0'..'9'` valem 0..9 (o
cálculo dos CNPJ numéricos não muda em nada) e `'A'..'Z'` valem 17..42.

Os CNPJ numéricos existentes continuam válidos e não mudam; um CNPJ numérico é
só o caso particular sem letra. A competência 2026-09 já traz o primeiro com
letra: `00000000E08G12`, uma filial do Banco do Brasil aberta em 31/07/2026.

O cuidado que isso impõe à limpeza da entrada: **letra nunca é descartada.**
Antes, tudo que não fosse dígito ia embora, e um CNPJ com letras podia virar
outro número, bem formado, de outra empresa — resposta errada e faturada. Aqui
só a pontuação sai; letra e dígito ficam, e o que não for `[0-9A-Z]` depois da
troca de caixa torna o CNPJ malformado (400).
"""

from __future__ import annotations

import re
import string

# Pesos do módulo 11, aplicados da esquerda para a direita.
PESOS_DV1 = (5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2)
PESOS_DV2 = (6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2)

# 12 posições alfanuméricas e 2 dígitos verificadores. ASCII explícito: `\d` e
# `str.isdigit()` aceitam dígito de outros alfabetos ('٣', '²').
_FORMATO = re.compile(r"[0-9A-Z]{12}[0-9]{2}", re.ASCII)

# Só a–z ASCII sobe de caixa. `str.upper()` mudaria o comprimento de algumas
# letras ('ß' vira 'SS') e faria um caractere inválido parecer válido.
_MAIUSCULAS = str.maketrans(string.ascii_lowercase, string.ascii_uppercase)


class CNPJInvalido(ValueError):
    """CNPJ malformado ou com dígito verificador errado — vira HTTP 400."""


def limpar(valor: str) -> str:
    """Tira a pontuação e passa a maiúsculas, sem descartar letra nem dígito.

    Pontuação é tudo que não é letra nem dígito (`.`, `/`, `-`, espaço). O
    resultado ainda pode ser inválido — `limpar` não valida, só padroniza. É
    também a forma que vai para o log de uso.
    """
    return "".join(c for c in valor if c.isalnum()).translate(_MAIUSCULAS)


def _valor(caractere: str) -> int:
    # Regra da Receita: código ASCII menos 48. Para dígito, é o próprio dígito.
    return ord(caractere) - 48


def _dv(base: str, pesos: tuple[int, ...]) -> int:
    resto = sum(_valor(c) * p for c, p in zip(base, pesos)) % 11
    return 0 if resto < 2 else 11 - resto


def digitos_verificadores(base12: str) -> str:
    """Calcula os dois DV a partir das 12 primeiras posições."""
    dv1 = _dv(base12, PESOS_DV1)
    dv2 = _dv(base12 + str(dv1), PESOS_DV2)
    return f"{dv1}{dv2}"


def _motivo(limpo: str) -> str | None:
    """Por que `limpo` não é um CNPJ válido, ou None se é."""
    if len(limpo) != 14:
        return (
            "CNPJ deve ter 14 caracteres (12 letras ou dígitos e 2 dígitos "
            f"verificadores), recebeu {len(limpo)}"
        )
    if not _FORMATO.fullmatch(limpo):
        return (
            "CNPJ malformado: as 12 primeiras posições aceitam só letras A-Z e "
            "dígitos, e as 2 últimas só dígitos"
        )
    # Repetição de um caractere só passa no módulo 11 por acidente aritmético —
    # 11111111111111 tem DV consistente. São sequências de teste, não CNPJ.
    # (Com letra a repetição nem é possível: o DV é sempre dígito.)
    if len(set(limpo)) == 1:
        return "dígito verificador não confere"
    if limpo[12:] != digitos_verificadores(limpo[:12]):
        return "dígito verificador não confere"
    return None


def valido(cnpj: str) -> bool:
    return _motivo(limpar(cnpj)) is None


def normalizar(cnpj: str) -> str:
    """Devolve as 14 posições em maiúsculas, ou levanta CNPJInvalido.

    Aceita com ou sem pontuação e em qualquer caixa: `11.222.333/0001-81` e
    `11222333000181` são a mesma coisa, como `12.abc.345/01de-35` e
    `12ABC34501DE35`. O SERPRO recebe sem pontuação, mas rejeitar a forma
    pontuada só geraria atrito sem ganhar nada.
    """
    limpo = limpar(cnpj)
    motivo = _motivo(limpo)
    if motivo:
        raise CNPJInvalido(motivo)
    return limpo


def partes(cnpj: str) -> tuple[str, str, str]:
    """Quebra em (básico, ordem, dv) — como as colunas da base."""
    c = normalizar(cnpj)
    return c[:8], c[8:12], c[12:]


def formatar(cnpj: str) -> str:
    c = limpar(cnpj)
    if len(c) == 14:
        return f"{c[:2]}.{c[2:5]}.{c[5:8]}/{c[8:12]}-{c[12:]}"
    if len(c) == 8:
        return f"{c[:2]}.{c[2:5]}.{c[5:8]}"
    return cnpj
