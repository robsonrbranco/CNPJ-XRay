"""Validação e normalização de CNPJ.

Existe por causa de uma distinção do contrato do SERPRO que o banco não sabe
fazer: **400 e 404 são casos diferentes**. CNPJ com dígito verificador errado é
400 (o cliente mandou lixo); CNPJ bem formado que não está na base é 404 (a
consulta foi legítima, não achou). Para o banco os dois são igualmente "não
achei", então a separação tem que acontecer antes da consulta.

A diferença não é cosmética: pela regra do SERPRO, 404 é transação faturável e
400 não é.
"""

from __future__ import annotations

# Pesos do módulo 11, aplicados da esquerda para a direita.
PESOS_DV1 = (5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2)
PESOS_DV2 = (6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2)


class CNPJInvalido(ValueError):
    """CNPJ malformado ou com dígito verificador errado — vira HTTP 400."""


def so_digitos(valor: str) -> str:
    return "".join(c for c in valor if c.isdigit())


def _dv(digitos: str, pesos: tuple[int, ...]) -> int:
    resto = sum(int(d) * p for d, p in zip(digitos, pesos)) % 11
    return 0 if resto < 2 else 11 - resto


def digitos_verificadores(base12: str) -> str:
    """Calcula os dois DV a partir dos 12 primeiros dígitos."""
    dv1 = _dv(base12, PESOS_DV1)
    dv2 = _dv(base12 + str(dv1), PESOS_DV2)
    return f"{dv1}{dv2}"


def valido(cnpj: str) -> bool:
    digitos = so_digitos(cnpj)
    if len(digitos) != 14:
        return False
    # Repetição de um dígito só passa no módulo 11 por acidente aritmético —
    # 11111111111111 tem DV consistente. São sequências de teste, não CNPJ.
    if len(set(digitos)) == 1:
        return False
    return digitos[12:] == digitos_verificadores(digitos[:12])


def normalizar(cnpj: str) -> str:
    """Devolve os 14 dígitos, ou levanta CNPJInvalido.

    Aceita com ou sem pontuação: `11.222.333/0001-81` e `11222333000181` são a
    mesma coisa. O SERPRO recebe sem pontuação, mas rejeitar a forma pontuada
    só geraria atrito sem ganhar nada.
    """
    digitos = so_digitos(cnpj)
    if len(digitos) != 14:
        raise CNPJInvalido(
            f"CNPJ deve ter 14 dígitos, recebeu {len(digitos)}"
        )
    if not valido(digitos):
        raise CNPJInvalido("dígito verificador não confere")
    return digitos


def partes(cnpj: str) -> tuple[str, str, str]:
    """Quebra em (básico, ordem, dv) — como as colunas da base."""
    d = normalizar(cnpj)
    return d[:8], d[8:12], d[12:]


def formatar(cnpj: str) -> str:
    d = so_digitos(cnpj)
    if len(d) == 14:
        return f"{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}"
    if len(d) == 8:
        return f"{d[:2]}.{d[2:5]}.{d[5:8]}"
    return cnpj
