"""Leitura em blocos dos CSV da Receita Federal.

Os arquivos vêm em latin-1, separados por ";", sem cabeçalho, e o maior deles
passa de 10 GB descompactado — carregar inteiro na memória não é opção.

Por que não `pl.scan_csv`: o leitor lazy do Polars só aceita `utf8` e
`utf8-lossy` ("csv `encoding` must be one of {'utf8', 'utf8-lossy'}"). Era por
isso que a versão anterior deste ETL copiava cada arquivo inteiro para um
irmão em UTF-8 antes de ler — uma reescrita completa de dezenas de GB só para
alimentar o parser. `pl.read_csv` aceita latin-1, então aqui o arquivo é
fatiado em blocos de linhas inteiras e cada bloco vai para o `read_csv`. O
custo de disco extra some.

Ler direto de dentro do .zip também é suportado, o que dispensa a fase de
extração: os ~7 GB compactados deixam de virar ~40 GB de CSV em disco.
"""

import io
import logging
import zipfile
from collections.abc import Iterator
from pathlib import Path

import polars as pl

logger = logging.getLogger(__name__)

# Bytes de CSV por bloco. Define o pico de memória da leitura: um bloco vira um
# DataFrame, e o carregador consome dele em lotes menores.
BYTES_POR_BLOCO = 128 * 1024 * 1024


def _abrir(origem: Path):
    """Abre a origem como stream binário de CSV.

    Aceita tanto o .zip da Receita (lê o primeiro membro) quanto um .csv já
    extraído.
    """
    if origem.suffix.lower() == ".zip":
        zf = zipfile.ZipFile(origem)
        nomes = zf.namelist()
        if not nomes:
            zf.close()
            raise ValueError(f"{origem.name} está vazio")
        return zf.open(nomes[0]), zf
    return open(origem, "rb"), None


def _blocos_de_bytes(stream, bytes_por_bloco: int) -> Iterator[bytes]:
    """Fatia o stream em blocos que terminam sempre em fim de linha.

    Cortar no meio de uma linha corromperia o registro. O contador de aspas
    cobre também o caso de um valor com quebra de linha embutida: enquanto o
    número de aspas do bloco for ímpar, ainda estamos dentro de um campo
    aberto e o bloco continua crescendo. Nos arquivos atuais da RFB isso não
    ocorre (300 mil registros conferidos em empresa, estabelecimento e socios
    batem 1:1 com as linhas físicas), mas a checagem é barata e evita que uma
    mudança de formato corrompa a carga em silêncio.
    """
    buffer = bytearray()
    for linha in stream:
        buffer += linha
        if len(buffer) >= bytes_por_bloco and buffer.count(b'"') % 2 == 0:
            yield bytes(buffer)
            buffer.clear()
    if buffer:
        yield bytes(buffer)


def blocos_csv(
    origem: Path,
    colunas: list[str],
    bytes_por_bloco: int = BYTES_POR_BLOCO,
) -> Iterator[pl.DataFrame]:
    """Gera DataFrames de strings cruas a partir do CSV em `origem`.

    Todas as colunas saem como Utf8: a conversão de tipo é do
    db.loader.normalizar, que a deriva do schema. Deixar o parser adivinhar
    tipo aqui levaria a decisões diferentes bloco a bloco.
    """
    stream, zf = _abrir(Path(origem))
    try:
        for bruto in _blocos_de_bytes(stream, bytes_por_bloco):
            if not bruto.strip():
                continue
            try:
                yield pl.read_csv(
                    io.BytesIO(bruto),
                    separator=";",
                    has_header=False,
                    new_columns=colunas,
                    schema_overrides=[pl.Utf8] * len(colunas),
                    encoding="latin-1",
                    # O campo `complemento` contém ";" dentro de valor entre
                    # aspas ("BLOCO: 01; APT: 144;"). Sem honrar aspas, ~5% das
                    # linhas de estabelecimento saem com o número errado de
                    # campos.
                    quote_char='"',
                )
            except pl.exceptions.NoDataError:
                continue
    finally:
        stream.close()
        if zf is not None:
            zf.close()
