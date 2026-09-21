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


# ---------------------------------------------------------------------------
# a ordem da publicação
# ---------------------------------------------------------------------------

def test_publicar_para_o_pod_antes_de_transmitir(monkeypatch):
    """A ordem é o ponto todo do desenho, e não se deduz lendo as funções.

    O host tem 41 GB livres e a base ocupa 34,2 GB: com ela em disco sobram
    6,8 GB, e as partes precisam de 12,2 GB. Transmitir antes de apagar encheria
    o disco do node — que num k3s não derruba só o Themis, gera DiskPressure e
    despeja pods dos outros cinco serviços.

    `preparar` fica antes de tudo por outro motivo: é a etapa longa e roda
    inteira na estação. Derrubar o serviço para só então começar a comprimir
    34 GB seria downtime de graça."""
    ordem = []

    def registra(nome, retorno=0):
        def f(*a, **k):
            ordem.append(nome)
            return retorno
        return f

    monkeypatch.setenv("OLYMPUS_HOST", "exemplo")
    monkeypatch.setattr(mod, "preparar", registra("preparar"))
    monkeypatch.setattr(mod, "parar", registra("parar"))
    monkeypatch.setattr(mod, "enviar", registra("enviar"))
    monkeypatch.setattr(mod, "trocar", registra("trocar"))
    monkeypatch.setattr(mod, "conferir_servico", registra("conferir"))
    monkeypatch.setattr(sys, "argv", ["enviar", "publicar"])

    assert mod.main() == 0
    assert ordem == ["preparar", "parar", "enviar", "trocar", "conferir"]


def test_publicar_nao_transmite_se_a_parada_falhar(monkeypatch):
    """Falhar em parar o pod e seguir transmitindo seria o pior caso: o Firebird
    com a base aberta, o scp enchendo o disco, e ninguém avisado."""
    ordem = []

    def registra(nome, retorno=0):
        def f(*a, **k):
            ordem.append(nome)
            return retorno
        return f

    monkeypatch.setenv("OLYMPUS_HOST", "exemplo")
    monkeypatch.setattr(mod, "preparar", registra("preparar"))
    monkeypatch.setattr(mod, "parar", registra("parar", 1))
    monkeypatch.setattr(mod, "enviar", registra("enviar"))
    monkeypatch.setattr(mod, "trocar", registra("trocar"))
    monkeypatch.setattr(mod, "conferir_servico", registra("conferir"))
    monkeypatch.setattr(sys, "argv", ["enviar", "publicar"])

    assert mod.main() != 0
    assert ordem == ["preparar", "parar"]


def test_enviar_recusa_quando_o_disco_do_host_nao_da(monkeypatch, tmp_path):
    """A conferência de espaço tem que ser ANTES da transmissão.

    O trocar-base.sh também confere, mas àquela altura já se gastou de 1 a 4 h
    transmitindo — e o scp já encheu o disco no caminho. Conferir depois é
    diagnóstico, não proteção."""
    trabalho = tmp_path / "envio"
    trabalho.mkdir()
    (trabalho / "manifesto-envio.json").write_text(json.dumps({
        "fdb_bytes": 34_200_000_000,
        "comprimido_bytes": 12_200_000_000,
        "partes": [{"nome": "base.gz.000", "bytes": 10, "sha256": "abc"}],
    }), encoding="utf-8")

    chamadas = []

    class Resposta:
        returncode = 0
        stdout = ""
        stderr = ""

    def falso_run(cmd, *a, **k):
        chamadas.append(cmd)
        return Resposta()

    monkeypatch.setattr(mod.subprocess, "run", falso_run)
    # 6,8 GB: o que sobra com a base de 34,2 GB ainda em disco.
    monkeypatch.setattr(mod, "espaco_livre_no_host", lambda d: 6_800_000_000)
    monkeypatch.setenv("OLYMPUS_HOST", "exemplo")

    falhas = mod.enviar(mod.Destino.do_ambiente(), trabalho)

    assert falhas > 0, "deveria ter recusado"
    assert not any("scp" in str(c) for c in chamadas), \
        "não pode ter transmitido nada"


def test_enviar_segue_quando_o_disco_do_host_da(monkeypatch, tmp_path):
    """O outro lado: recusar sempre também passaria no teste acima."""
    trabalho = tmp_path / "envio"
    trabalho.mkdir()
    (trabalho / "manifesto-envio.json").write_text(json.dumps({
        "fdb_bytes": 34_200_000_000,
        "comprimido_bytes": 12_200_000_000,
        "partes": [{"nome": "base.gz.000", "bytes": 10, "sha256": "abc"}],
    }), encoding="utf-8")
    (trabalho / "base.gz.000").write_bytes(b"0123456789")

    chamadas = []

    class Resposta:
        returncode = 0
        stdout = "abc"
        stderr = ""

    def falso_run(cmd, *a, **k):
        chamadas.append(cmd)
        return Resposta()

    monkeypatch.setattr(mod.subprocess, "run", falso_run)
    # 41 GB: o disco do host depois de a base antiga sair.
    monkeypatch.setattr(mod, "espaco_livre_no_host", lambda d: 41_000_000_000)
    monkeypatch.setenv("OLYMPUS_HOST", "exemplo")

    falhas = mod.enviar(mod.Destino.do_ambiente(), trabalho)

    assert falhas == 0
    assert any("scp" in str(c) for c in chamadas), "deveria ter transmitido"


# ---------------------------------------------------------------------------
# confirmação do serviço
# ---------------------------------------------------------------------------

def test_conferir_servico_manda_user_agent(monkeypatch):
    """Sem `User-Agent` próprio, o urllib manda `Python-urllib/3.x` e o
    Cloudflare responde 403 `error code: 1010`.

    Aconteceu de verdade na publicação de 2026-09: o Themis subiu certo,
    servindo a competência correta, e esta função reportou falha dez vezes.
    Confirmação que falha com o serviço saudável é pior que confirmação
    nenhuma — manda investigar o que está funcionando."""
    import urllib.request

    vistos = []

    class RespostaFalsa:
        def read(self, *a):
            return b'{"status":"ok","competencia":"2026-09"}'

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def falso_urlopen(pedido, **k):
        vistos.append(pedido)
        return RespostaFalsa()

    monkeypatch.setattr(urllib.request, "urlopen", falso_urlopen)
    monkeypatch.setenv("OLYMPUS_HOST", "exemplo")

    assert mod.conferir_servico(mod.Destino.do_ambiente()) == 0

    assert len(vistos) == 1
    agente = vistos[0].get_header("User-agent")
    assert agente, "o pedido saiu sem User-Agent"
    assert "urllib" not in agente.lower(), agente


def test_as_duas_pastas_do_host_sao_distintas(monkeypatch):
    """A base é trocada todo mês; credenciais e log não. Compartilhar a pasta
    faria um consumidor cadastrado sumir na virada."""
    monkeypatch.setenv("OLYMPUS_HOST", "exemplo")

    d = mod.Destino.do_ambiente()

    assert d.pasta_fdb == "/cnpj-xray-fdb"
    assert d.pasta_db == "/cnpj-xray-db"
    assert d.pasta_fdb != d.pasta_db
