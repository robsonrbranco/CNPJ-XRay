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
import os
import zipfile
from collections.abc import Iterator
from pathlib import Path

import polars as pl

logger = logging.getLogger(__name__)

# Bytes de CSV por bloco. Define o pico de memória de cada worker: um bloco
# vira um DataFrame, e o carregador consome dele em lotes de 256 linhas.
#
# Com N workers em paralelo o consumo é N vezes isso, e é a memória — não a
# CPU — que limita quantos workers cabem. Blocos menores só ficaram baratos
# depois que o Carregador passou a reaproveitar o statement preparado: antes,
# reduzir o bloco multiplicava o custo de preparação (3,9% do tempo por bloco).
BYTES_POR_BLOCO = int(os.getenv("ETL_BYTES_POR_BLOCO", 32 * 1024 * 1024))

# Encoding dos arquivos da Receita.
#
# cp1252 e não latin-1, apesar de os dois serem idênticos em quase toda a
# tabela. Eles divergem na faixa 0x80-0x9F: latin-1 mapeia para caracteres de
# controle C1, cp1252 mapeia para símbolos imprimíveis (aspas curvas, travessão,
# reticências). Como o banco é WIN1252, ler como latin-1 e gravar como WIN1252
# quebra — um caractere de controle C1 não existe em WIN1252 — enquanto ler
# como cp1252 faz o byte voltar idêntico ao que veio do arquivo.
ENCODING = "cp1252"

# Bytes sem definição em cp1252, e o que colocar no lugar.
#
# São cinco: 0x81, 0x8D, 0x8F, 0x90 e 0x9D. Não representam caractere nenhum,
# nem em cp1252 nem em WIN1252, então não há conversão possível — só substituir
# ou abortar a carga.
#
# Uma varredura completa das 37 partes da competência 2026-08 (26,5 GB
# descompactados) achou exatamente 5 ocorrências, todas de 0x8F, em
# Estabelecimentos 0, 1 e 4. É lixo na origem. Mas cinco bytes bastam para
# derrubar uma carga de três horas no fim dela, então a limpeza é obrigatória.
#
# Feita em bytes e não em string: `bytes.translate` é uma passada em C sobre o
# bloco inteiro. Inspecionar 220 milhões de strings em Python custaria mais que
# a carga.
SUBSTITUTO = ord("?")
_INDEFINIDOS_CP1252 = (0x81, 0x8D, 0x8F, 0x90, 0x9D)
_TABELA_LIMPEZA = bytes(
    SUBSTITUTO if b in _INDEFINIDOS_CP1252 else b for b in range(256)
)
# Para contar quantos foram trocados: apaga tudo que não é indefinido.
_SO_INDEFINIDOS = bytes(b for b in range(256) if b not in _INDEFINIDOS_CP1252)


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
    nome = Path(origem).name
    try:
        for bruto in _blocos_de_bytes(stream, bytes_por_bloco):
            if not bruto.strip():
                continue

            sujos = len(bruto.translate(None, delete=_SO_INDEFINIDOS))
            if sujos:
                logger.warning(
                    "%s: %d byte(s) sem definição em %s trocado(s) por %r",
                    nome, sujos, ENCODING, chr(SUBSTITUTO),
                )
                bruto = bruto.translate(_TABELA_LIMPEZA)

            try:
                yield pl.read_csv(
                    io.BytesIO(bruto),
                    separator=";",
                    has_header=False,
                    new_columns=colunas,
                    schema_overrides=[pl.Utf8] * len(colunas),
                    encoding=ENCODING,
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
