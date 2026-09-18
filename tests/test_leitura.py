"""Testes da leitura dos CSV da Receita.

As duas sutilezas aqui já custaram caro. O corte de bloco que ignora aspas
corrompe ~5% de `estabelecimento`, porque `complemento` traz `;` dentro de
valor entre aspas. E a confusão entre cp1252 e latin-1 matou uma carga de 40
minutos com `UnicodeEncodeError` no byte 0x8F — os dois encodings divergem
justamente na faixa 0x80–0x9F.

Nenhum dos dois aparece como erro: aparecem como linha com número errado de
campos ou como exceção quatro horas depois do início. Daí o teste.
"""

import io
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.etl import leitura  # noqa: E402


# ---------------------------------------------------------------------------
# corte de bloco
# ---------------------------------------------------------------------------

def _blocos(dados: bytes, tamanho: int) -> list[bytes]:
    return list(leitura._blocos_de_bytes(io.BytesIO(dados), tamanho))


def test_bloco_nunca_corta_no_meio_de_uma_linha():
    dados = b"".join(b"linha%03d;valor\n" % i for i in range(100))

    blocos = _blocos(dados, 100)

    assert b"".join(blocos) == dados, "o conteúdo mudou ao ser fatiado"
    for b in blocos:
        assert b.endswith(b"\n"), f"bloco termina no meio de uma linha: {b[-20:]!r}"


def test_bloco_nao_fecha_com_aspas_abertas():
    """Valor com quebra de linha embutida não pode ser partido em dois blocos.

    Se o corte acontecesse com número ímpar de aspas, o pedaço de cima teria um
    campo aberto e o de baixo começaria no meio de um valor — as duas metades
    viram lixo, sem erro nenhum.
    """
    # O tamanho importa: a linha que ABRE a aspa precisa, sozinha, já levar o
    # buffer além do limite do bloco. Senão o corte cairia depois da linha que
    # fecha, com contagem par, e a checagem nunca seria exercitada — foi assim
    # que a primeira versão deste teste passou mesmo com a checagem removida.
    dados = (
        b"a;b\n"
        + b'x;"' + b"A" * 60 + b"\n"          # abre aspa e estoura o bloco
        + b"B" * 60 + b'"\n'                  # só aqui a aspa fecha
        + b"c;d\n"
    )

    blocos = _blocos(dados, 20)

    assert b"".join(blocos) == dados
    for b in blocos:
        assert b.count(b'"') % 2 == 0, f"bloco com aspas ímpares: {b!r}"


def test_ponto_e_virgula_dentro_de_aspas_sobrevive_ao_fatiamento():
    """O caso real: `complemento` com "BLOCO: 01; APT: 144;"."""
    linha = b'08314885;0001;05;"BLOCO: 01; APT: 144;";SP\n'
    dados = linha * 30

    blocos = _blocos(dados, 60)

    assert b"".join(blocos) == dados
    for b in blocos:
        assert b.count(b'"') % 2 == 0


def test_entrada_vazia_nao_gera_bloco():
    assert _blocos(b"", 100) == []


def test_entrada_menor_que_o_bloco_sai_inteira():
    dados = b"uma;linha;so\n"
    assert _blocos(dados, 10_000) == [dados]


# ---------------------------------------------------------------------------
# encoding
# ---------------------------------------------------------------------------

def test_os_cinco_bytes_indefinidos_em_cp1252_viram_interrogacao():
    bruto = bytes([0x41, 0x81, 0x42, 0x8D, 0x43, 0x8F, 0x44, 0x90, 0x45, 0x9D, 0x46])

    limpo = bruto.translate(leitura._TABELA_LIMPEZA)

    assert limpo == b"A?B?C?D?E?F"
    # E o resultado agora é decodificável, que é o ponto de tudo isso.
    assert limpo.decode("cp1252") == "A?B?C?D?E?F"


def test_bytes_definidos_em_cp1252_nao_sao_tocados():
    """0x80 (€) e 0xE9 (é) são válidos e têm que passar intactos."""
    bruto = bytes([0x80, 0xE9, 0xC7, 0xE3])

    limpo = bruto.translate(leitura._TABELA_LIMPEZA)

    assert limpo == bruto
    assert limpo.decode("cp1252") == "€éÇã"


def test_contagem_de_bytes_indefinidos():
    """`_SO_INDEFINIDOS` apaga todo o resto, sobrando só o que será trocado."""
    bruto = b"ABC" + bytes([0x81, 0x8F]) + b"DEF" + bytes([0x9D])

    sujos = len(bruto.translate(None, delete=leitura._SO_INDEFINIDOS))

    assert sujos == 3


def test_le_o_zip_como_cp1252_e_nao_como_latin1():
    """A divergência que matou a carga de 40 minutos.

    0x93 e 0x94 são aspas curvas em cp1252 e caracteres de controle em
    latin-1. Ler como latin-1 e gravar em WIN1252 produz texto errado — ou
    exceção, quando cai num dos cinco indefinidos.
    """
    assert leitura.ENCODING == "cp1252"

    bruto = bytes([0x93]) + "RAZAO".encode() + bytes([0x94])
    assert bruto.decode("cp1252") == "“RAZAO”"
    assert bruto.decode("latin-1") != bruto.decode("cp1252")


# ---------------------------------------------------------------------------
# ponta a ponta, a partir de um .zip de verdade
# ---------------------------------------------------------------------------

@pytest.fixture
def zip_de_teste(tmp_path):
    def criar(nome: str, conteudo: bytes) -> Path:
        caminho = tmp_path / nome
        with zipfile.ZipFile(caminho, "w") as zf:
            zf.writestr("K3241.K03200Y0.D50809.EMPRECSV", conteudo)
        return caminho
    return criar


def test_blocos_csv_le_direto_do_zip(zip_de_teste):
    conteudo = (
        b'"08314885";"FLAVIO PAVAO DE SOUZA";"4120";"16";"0,00";"05";""\n'
        b'"12340763";"ZELI DA SILVA MACEDO";"2062";"49";"1000,00";"01";""\n'
    )
    caminho = zip_de_teste("Empresas0.zip", conteudo)
    colunas = ["cnpj_basico", "razao_social", "natureza_juridica",
               "qualificacao_responsavel", "capital_social", "porte_empresa",
               "ente_federativo_responsavel"]

    dfs = list(leitura.blocos_csv(caminho, colunas))

    assert len(dfs) == 1
    df = dfs[0]
    assert df.height == 2
    assert df.columns == colunas
    assert df["razao_social"].to_list() == ["FLAVIO PAVAO DE SOUZA", "ZELI DA SILVA MACEDO"]
    # Tudo sai como texto; a conversão de tipo é do loader, derivada do schema.
    assert df["capital_social"].to_list() == ["0,00", "1000,00"]


def test_blocos_csv_preserva_acento_vindo_de_cp1252(zip_de_teste):
    conteudo = '"11111111";"JOSÉ DA CONCEIÇÃO";"2062";"49";"0,00";"01";""\n'.encode("cp1252")
    caminho = zip_de_teste("Empresas0.zip", conteudo)
    colunas = ["cnpj_basico", "razao_social", "natureza_juridica",
               "qualificacao_responsavel", "capital_social", "porte_empresa",
               "ente_federativo_responsavel"]

    df = next(iter(leitura.blocos_csv(caminho, colunas)))

    assert df["razao_social"][0] == "JOSÉ DA CONCEIÇÃO"


def test_blocos_csv_nao_quebra_com_byte_indefinido(zip_de_teste):
    """Antes da correção, isto era `UnicodeEncodeError` no meio da carga."""
    conteudo = (b'"11111111";"NOME' + bytes([0x8F]) + b'RUIM";"2062";"49";"0,00";"01";""\n')
    caminho = zip_de_teste("Empresas0.zip", conteudo)
    colunas = ["cnpj_basico", "razao_social", "natureza_juridica",
               "qualificacao_responsavel", "capital_social", "porte_empresa",
               "ente_federativo_responsavel"]

    df = next(iter(leitura.blocos_csv(caminho, colunas)))

    assert df["razao_social"][0] == "NOME?RUIM"


def test_zip_vazio_e_erro_explicito(tmp_path):
    caminho = tmp_path / "vazio.zip"
    with zipfile.ZipFile(caminho, "w"):
        pass

    with pytest.raises(ValueError, match="vazio"):
        list(leitura.blocos_csv(caminho, ["a"]))
