"""Testes do envio da base para o host.

A transmissão em si não é testada: exigiria o servidor de produção, e apontar
um teste para ele seria pior que não testar. O que é testado é tudo o que
acontece **antes** dela — compactar, fatiar, calcular hash e montar o
manifesto — mais a propriedade que dá sentido ao resto: as partes
concatenadas, descompactadas, devolvem a base byte a byte.

Se essa propriedade quebrar, o host reconstrói um arquivo errado com todos os
hashes de parte conferindo, e o defeito só apareceria semanas depois numa
consulta a um CNPJ específico.
"""

import gzip
import hashlib
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.publicacao import enviar as mod  # noqa: E402


@pytest.fixture
def base(tmp_path):
    """Arquivo pseudoaleatório mas compressível, como um .fdb de verdade:
    páginas com muito espaço repetido e trechos de dado único."""
    caminho = tmp_path / "cnpj_xray.fdb"
    with open(caminho, "wb") as f:
        for i in range(400):
            f.write(f"PAGINA{i:06d}".encode() + b"\x00" * 900 + os.urandom(124))
    return caminho


@pytest.fixture
def partes_pequenas(monkeypatch):
    """Fatia em 8 KB para o teste produzir várias partes sem gerar MB."""
    monkeypatch.setattr(mod, "TAMANHO_PARTE", 8 * 1024)


def test_preparar_gera_partes_e_manifesto(base, tmp_path, partes_pequenas):
    m = mod.preparar(base, tmp_path / "envio")

    assert len(m["partes"]) > 1, "deveria ter fatiado em mais de uma parte"
    assert m["fdb_bytes"] == base.stat().st_size
    assert (tmp_path / "envio" / "manifesto-envio.json").exists()


def test_as_partes_concatenadas_reconstroem_a_base(base, tmp_path, partes_pequenas):
    """A propriedade central. Hash de parte conferindo não prova que a
    concatenação delas é a base — só esta verificação prova."""
    trabalho = tmp_path / "envio"
    m = mod.preparar(base, trabalho)

    juntas = b"".join(
        (trabalho / p["nome"]).read_bytes() for p in m["partes"]
    )
    reconstruida = gzip.decompress(juntas)

    assert reconstruida == base.read_bytes()
    assert hashlib.sha256(reconstruida).hexdigest() == m["fdb_sha256"]


def test_a_ordem_das_partes_e_lexicografica(base, tmp_path, partes_pequenas):
    """O host concatena por índice formatado (`base.gz.%03d`). Se a ordenação
    do manifesto divergisse dela, a base sairia embaralhada."""
    m = mod.preparar(base, tmp_path / "envio")

    nomes = [p["nome"] for p in m["partes"]]
    assert nomes == sorted(nomes)
    assert nomes[0] == "base.gz.000"
    assert all(n.startswith("base.gz.") for n in nomes)


def test_cada_parte_tem_o_hash_do_proprio_conteudo(base, tmp_path, partes_pequenas):
    trabalho = tmp_path / "envio"
    m = mod.preparar(base, trabalho)

    for p in m["partes"]:
        real = hashlib.sha256((trabalho / p["nome"]).read_bytes()).hexdigest()
        assert real == p["sha256"], p["nome"]
        assert (trabalho / p["nome"]).stat().st_size == p["bytes"]


def test_so_a_ultima_parte_pode_ser_menor(base, tmp_path, partes_pequenas):
    """Parte curta no meio significaria fatiamento errado, e a concatenação
    ainda daria certo — o erro só apareceria no tamanho final."""
    m = mod.preparar(base, tmp_path / "envio")

    tamanhos = [p["bytes"] for p in m["partes"]]
    assert all(t == mod.TAMANHO_PARTE for t in tamanhos[:-1]), tamanhos
    assert 0 < tamanhos[-1] <= mod.TAMANHO_PARTE


def test_preparar_limpa_execucao_anterior(base, tmp_path, partes_pequenas):
    """Sobra de um envio anterior seria enviada junto e o host tentaria
    concatenar partes de duas bases diferentes."""
    trabalho = tmp_path / "envio"
    trabalho.mkdir()
    (trabalho / "base.gz.999").write_bytes(b"sobra de outra execucao")

    mod.preparar(base, trabalho)

    assert not (trabalho / "base.gz.999").exists()


def test_sem_parte_vazia_quando_o_corte_cai_no_fim(base, tmp_path, monkeypatch):
    """Caso de borda que a probabilidade esconde: se o fluxo comprimido terminar
    em múltiplo exato do tamanho da parte, o laço fecha a parte cheia e abre a
    seguinte, que fica com zero byte. Ela não corrompe a base — concatenar nada
    é nada — mas entra no manifesto como parte de verdade, e o host paga uma
    transmissão e uma conferência por ela.

    Aqui o múltiplo exato é forçado, medindo o fluxo antes e cortando nele."""
    m = mod.preparar(base, tmp_path / "medir")
    monkeypatch.setattr(mod, "TAMANHO_PARTE", m["comprimido_bytes"])

    m2 = mod.preparar(base, tmp_path / "envio")

    assert len(m2["partes"]) == 1, [p["nome"] for p in m2["partes"]]
    assert all(p["bytes"] > 0 for p in m2["partes"])
    assert not (tmp_path / "envio" / "base.gz.001").exists()


def test_manifesto_registra_a_razao_de_compressao(base, tmp_path, partes_pequenas):
    m = mod.preparar(base, tmp_path / "envio")
    assert m["razao"] > 1.0
    assert m["comprimido_bytes"] < m["fdb_bytes"]


# ---------------------------------------------------------------------------
# destino
# ---------------------------------------------------------------------------

def test_destino_exige_host(monkeypatch):
    """Sem host não há para onde enviar, e um padrão aqui poderia mandar a base
    para a máquina errada."""
    monkeypatch.delenv("OLYMPUS_HOST", raising=False)
    with pytest.raises(RuntimeError, match="OLYMPUS_HOST"):
        mod.Destino.do_ambiente()


def test_a_pasta_de_envio_e_separada_da_base_em_uso(monkeypatch):
    """`scp` direto por cima do .fdb que o pod está servindo corromperia a base
    em produção no meio de uma consulta."""
    monkeypatch.setenv("OLYMPUS_HOST", "exemplo")
    monkeypatch.setenv("OLYMPUS_PASTA_FDB", "/cnpj-xray-fdb")

    d = mod.Destino.do_ambiente()

    assert d.envio != d.pasta_fdb
    assert d.envio.startswith(d.pasta_fdb)


def test_as_duas_pastas_do_host_sao_distintas(monkeypatch):
    """A base é trocada todo mês; credenciais e log não. Compartilhar a pasta
    faria um consumidor cadastrado sumir na virada."""
    monkeypatch.setenv("OLYMPUS_HOST", "exemplo")

    d = mod.Destino.do_ambiente()

    assert d.pasta_fdb == "/cnpj-xray-fdb"
    assert d.pasta_db == "/cnpj-xray-db"
    assert d.pasta_fdb != d.pasta_db
