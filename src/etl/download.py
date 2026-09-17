"""Baixa uma competência do CNPJ direto do compartilhamento público da Receita.

A Receita publica os 37 arquivos num Nextcloud, exposto por WebDAV. O caminho
óbvio — um GET por arquivo, alguns em paralelo — falha de um jeito específico e
caro: o servidor aceita a conexão, entrega alguns megabytes e para de mandar
bytes sem fechar o socket. Um cliente sem detecção de travamento fica pendurado
para sempre. Foi o que aconteceu aqui: quatro conexões travadas em 22 MB por
mais de quatro minutos, enquanto uma conexão nova no mesmo instante rendia
2,6 MB/s. O problema nunca foi banda.

Daí as três decisões deste módulo:

* **Multipart** — cada arquivo é pedido em faixas (`Range`) de 64 MB. O
  servidor responde 206 mesmo sem anunciar `Accept-Ranges`. Uma faixa que trava
  custa no máximo o que já baixou dela, não o arquivo inteiro.
* **Paralelismo por pedaço, não por arquivo** — a fila é global e contém todos
  os pedaços de todos os arquivos. Isso importa porque os arquivos são
  desiguais: `Estabelecimentos0.zip` tem 2,24 GB e os de domínio têm 1 KB. Com
  paralelismo por arquivo, o fim da carga vira uma única conexão arrastando o
  arquivo gigante sozinha.
* **Failover** — o timeout de socket derruba conexão que passa 45 s sem
  entregar um byte; cada pedaço tem 6 tentativas com espera crescente,
  retomando de onde parou; e o arquivo que ainda assim não fechar é refeito em
  modo sequencial, sem faixas, como último recurso.

No fim, cada arquivo é conferido byte a byte contra o tamanho declarado pelo
WebDAV e tem o diretório central do zip lido — tamanho certo com zip corrompido
seria pior que erro de download, porque só apareceria quatro horas depois, no
meio do ETL.

TLS aqui é verificado normalmente. O `verify=False` que o ETL antigo carregava
não é necessário neste servidor — foi testado.
"""

from __future__ import annotations

import argparse
import base64
import os
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

SHARE = os.getenv("RFB_SHARE", "https://arquivos.receitafederal.gov.br")
TOKEN = os.getenv("RFB_TOKEN", "YggdBLfdninEJX9")
DAV = f"{SHARE}/public.php/dav/files/{TOKEN}"

# 64 MB: grande o bastante para o custo do handshake TLS sumir, pequeno o
# bastante para uma faixa travada não jogar fora muito trabalho.
PEDACO = int(os.getenv("DOWNLOAD_PEDACO_MB", "64")) * 1024 * 1024
CONEXOES = int(os.getenv("DOWNLOAD_CONEXOES", "8"))

BLOCO_LEITURA = 1024 * 1024
TIMEOUT = 45          # segundos sem UM byte: conexão considerada morta
TENTATIVAS = 6
ESPERA = [2, 5, 10, 20, 30, 30]

# Quando a origem inteira cai, tentativa não resolve: as 6 somam menos de dois
# minutos e o servidor da Receita fica fora por muito mais que isso. Nesse caso
# o pedaço para de gastar tentativa e passa a esperar a origem voltar.
ESPERA_ORIGEM_MIN = int(os.getenv("DOWNLOAD_ESPERA_ORIGEM_MIN", "180"))

_AUTH = "Basic " + base64.b64encode(f"{TOKEN}:".encode()).decode()
_lock = threading.Lock()
_baixados = 0         # bytes que passaram pela rede nesta execução


def _requisitar(url: str, metodo: str = "GET", faixa: tuple[int, int] | None = None,
                profundidade: str | None = None):
    req = urllib.request.Request(url, method=metodo)
    req.add_header("Authorization", _AUTH)
    req.add_header("User-Agent", "CNPJ-XRay/1.0")
    if faixa:
        req.add_header("Range", f"bytes={faixa[0]}-{faixa[1]}")
    if profundidade is not None:
        req.add_header("Depth", profundidade)
    return urllib.request.urlopen(req, timeout=TIMEOUT)


# --------------------------------------------------------------------------
# descoberta
# --------------------------------------------------------------------------

def listar_competencias() -> list[str]:
    """Competências disponíveis no compartilhamento, em ordem crescente."""
    with _requisitar(f"{DAV}/", metodo="PROPFIND", profundidade="1") as r:
        xml = r.read()
    nomes = []
    for resp in ET.fromstring(xml):
        href = resp.find("{DAV:}href").text.rstrip("/").split("/")[-1]
        if len(href) == 7 and href[4] == "-" and href[:4].isdigit():
            nomes.append(href)
    return sorted(nomes)


def manifesto(competencia: str) -> dict[str, int]:
    """Mapa {arquivo.zip: tamanho em bytes} declarado pelo WebDAV."""
    with _requisitar(f"{DAV}/{competencia}/", metodo="PROPFIND", profundidade="1") as r:
        xml = r.read()
    itens: dict[str, int] = {}
    for resp in ET.fromstring(xml):
        nome = resp.find("{DAV:}href").text.rstrip("/").split("/")[-1]
        if not nome.lower().endswith(".zip"):
            continue
        tam = resp.find(".//{DAV:}getcontentlength")
        itens[nome] = int(tam.text) if tam is not None else 0
    return dict(sorted(itens.items()))


# --------------------------------------------------------------------------
# transferência
# --------------------------------------------------------------------------

@dataclass
class Pedaco:
    competencia: str
    arquivo: str
    indice: int
    total: int
    inicio: int
    fim: int          # inclusivo, como manda o cabeçalho Range

    @property
    def tamanho(self) -> int:
        return self.fim - self.inicio + 1

    @property
    def url(self) -> str:
        return f"{DAV}/{self.competencia}/{self.arquivo}"


def _fatiar(competencia: str, nome: str, tamanho: int) -> list[Pedaco]:
    if tamanho <= PEDACO:
        return [Pedaco(competencia, nome, 0, 1, 0, max(tamanho - 1, 0))]
    limites = list(range(0, tamanho, PEDACO))
    total = len(limites)
    return [
        Pedaco(competencia, nome, i, total, ini, min(ini + PEDACO, tamanho) - 1)
        for i, ini in enumerate(limites)
    ]


def _escrever(resp, destino: Path, modo: str) -> int:
    global _baixados
    escritos = 0
    with open(destino, modo) as f:
        while True:
            dados = resp.read(BLOCO_LEITURA)
            if not dados:
                break
            f.write(dados)
            escritos += len(dados)
            with _lock:
                _baixados += len(dados)
    return escritos


def _baixar_pedaco(p: Pedaco, partes: Path) -> None:
    """Baixa uma faixa para `partes/<arquivo>.<indice>`, retomando o parcial.

    Levanta a última exceção se esgotar as tentativas — quem chama decide se
    cai para o modo sequencial.
    """
    alvo = partes / f"{p.arquivo}.{p.indice:03d}"
    erro: Exception | None = None
    tentativa = 0

    while tentativa < TENTATIVAS:
        ja = alvo.stat().st_size if alvo.exists() else 0
        if ja == p.tamanho:
            return
        if ja > p.tamanho:        # sobra de execução anterior com outro corte
            alvo.unlink()
            ja = 0
        try:
            with _requisitar(p.url, faixa=(p.inicio + ja, p.fim)) as r:
                if r.status not in (200, 206):
                    raise OSError(f"HTTP {r.status}")
                _escrever(r, alvo, "ab" if ja else "wb")
            if alvo.stat().st_size == p.tamanho:
                return
            erro = OSError(f"recebeu {alvo.stat().st_size} de {p.tamanho} bytes")
        except (urllib.error.URLError, OSError, socket.timeout) as e:
            erro = e

        # Cada tentativa perdida sai no log. Sem isso o processo fica mudo por
        # minutos quando a origem oscila, e silêncio é indistinguível de
        # travamento — foi exatamente o que me fez suspeitar de bug no código
        # quando o problema era o servidor indo e voltando.
        print(f"  {time.strftime('%H:%M:%S')}  tentativa {tentativa + 1}/{TENTATIVAS} "
              f"falhou em {p.arquivo}[{p.indice + 1}/{p.total}]: "
              f"{type(erro).__name__} {erro}", flush=True)

        # Origem fora do ar não é falha deste pedaço: esperar custa menos que
        # queimar as seis tentativas em dois minutos e desistir do arquivo.
        if not servidor_responde(p.url):
            if not esperar_servidor(ESPERA_ORIGEM_MIN, url=p.url):
                raise OSError(f"{p.arquivo} pedaço {p.indice}: origem fora do ar")
            continue

        tentativa += 1
        time.sleep(ESPERA[min(tentativa - 1, len(ESPERA) - 1)])

    raise OSError(f"{p.arquivo} pedaço {p.indice}: {erro}")


def _sequencial(competencia: str, nome: str, tamanho: int, alvo: Path) -> None:
    """Failover: um único fluxo, sem faixas, retomando o que já está em disco."""
    for tentativa in range(TENTATIVAS):
        ja = alvo.stat().st_size if alvo.exists() else 0
        if ja == tamanho:
            return
        try:
            faixa = (ja, tamanho - 1) if ja else None
            with _requisitar(f"{DAV}/{competencia}/{nome}", faixa=faixa) as r:
                _escrever(r, alvo, "ab" if ja else "wb")
            if alvo.stat().st_size == tamanho:
                return
        except (urllib.error.URLError, OSError, socket.timeout):
            pass
        time.sleep(ESPERA[min(tentativa, len(ESPERA) - 1)])
    raise OSError(f"{nome}: modo sequencial também não fechou")


def _juntar(nome: str, pedacos: list[Pedaco], partes: Path, destino: Path) -> None:
    with open(destino / nome, "wb") as saida:
        for p in pedacos:
            with open(partes / f"{p.arquivo}.{p.indice:03d}", "rb") as f:
                while True:
                    dados = f.read(BLOCO_LEITURA * 8)
                    if not dados:
                        break
                    saida.write(dados)
    for p in pedacos:
        (partes / f"{p.arquivo}.{p.indice:03d}").unlink(missing_ok=True)


def conferir(caminho: Path, esperado: int) -> str | None:
    """Devolve a descrição do problema, ou None se o arquivo está íntegro."""
    if not caminho.exists():
        return "não existe"
    real = caminho.stat().st_size
    if real != esperado:
        return f"{real:,} bytes, esperado {esperado:,}"
    try:
        with zipfile.ZipFile(caminho) as z:
            if not z.namelist():
                return "zip sem nenhuma entrada"
    except zipfile.BadZipFile as e:
        return f"zip inválido ({e})"
    return None


# --------------------------------------------------------------------------
# orquestração
# --------------------------------------------------------------------------

def servidor_responde(url: str | None = None) -> bool:
    """Testa a origem — de preferência pelo mesmo caminho que os dados usam.

    O PROPFIND continua respondendo enquanto os GETs de arquivo já estão dando
    timeout: são caminhos diferentes no servidor. Decidir "a origem está no ar"
    olhando o PROPFIND faz o pedaço queimar as seis tentativas à toa. Por isso o
    probe padrão é um GET de 64 KB do próprio arquivo que se quer baixar.
    """
    try:
        if url:
            with _requisitar(url, faixa=(0, 65535)) as r:
                return bool(r.read(1))
        with _requisitar(f"{DAV}/", metodo="PROPFIND", profundidade="0"):
            return True
    except (urllib.error.URLError, OSError, socket.timeout):
        return False


def esperar_servidor(minutos: int, intervalo: int = 60, url: str | None = None) -> bool:
    """Espera o compartilhamento voltar a aceitar conexão.

    O servidor da Receita sai do ar sem aviso — durante este próprio
    desenvolvimento ele passou de 2,6 MB/s a recusar TCP em quatro minutos.
    Abortar a execução por isso obrigaria a vigiar o processo; esperar custa
    nada e deixa o download partir sozinho quando a origem voltar.
    """
    limite = time.time() + minutos * 60
    primeira = True
    while time.time() < limite:
        if servidor_responde(url):
            if not primeira:
                print(f"  {time.strftime('%H:%M:%S')}  servidor respondeu", flush=True)
            return True
        if primeira:
            print(f"  {time.strftime('%H:%M:%S')}  origem fora do ar — "
                  f"tentando a cada {intervalo}s por até {minutos} min", flush=True)
            primeira = False
        time.sleep(intervalo)
    return False


def baixar(competencia: str, destino: Path, conexoes: int = CONEXOES) -> int:
    """Baixa a competência inteira. Devolve a quantidade de arquivos com erro."""
    itens = manifesto(competencia)
    if not itens:
        print(f"nada publicado em {competencia}")
        return 1

    pasta = destino / competencia
    partes = pasta / ".partes"
    partes.mkdir(parents=True, exist_ok=True)

    print(f"{competencia}: {len(itens)} arquivos, {sum(itens.values()) / 1e9:.2f} GB, "
          f"{conexoes} conexões, pedaços de {PEDACO // 1024 // 1024} MB", flush=True)

    # Arquivo já íntegro de execução anterior não volta para a fila.
    pendentes: dict[str, int] = {}
    for nome, tam in itens.items():
        if conferir(pasta / nome, tam) is None:
            print(f"  ja em disco  {nome}", flush=True)
        else:
            (pasta / nome).unlink(missing_ok=True)
            pendentes[nome] = tam
    if not pendentes:
        print("tudo já estava baixado e íntegro")
        return 0

    fatias = {nome: _fatiar(competencia, nome, tam) for nome, tam in pendentes.items()}
    # Maiores primeiro: o arquivo gigante começa cedo e não sobra sozinho no fim.
    fila = [p for nome in sorted(pendentes, key=pendentes.get, reverse=True)
            for p in fatias[nome]]
    restantes = {nome: len(ps) for nome, ps in fatias.items()}
    falhou: dict[str, str] = {}
    inicio = time.time()

    with ThreadPoolExecutor(max_workers=conexoes) as pool:
        futuros = {pool.submit(_baixar_pedaco, p, partes): p for p in fila}
        for fut in as_completed(futuros):
            p = futuros[fut]
            restantes[p.arquivo] -= 1
            try:
                fut.result()
            except Exception as e:                      # noqa: BLE001
                falhou[p.arquivo] = str(e)
                print(f"  {time.strftime('%H:%M:%S')}  FALHA {p.arquivo} "
                      f"[{p.indice + 1}/{p.total}]: {e}", flush=True)
                continue

            decorrido = max(time.time() - inicio, 0.001)
            with _lock:
                mbps = _baixados / decorrido / 1e6
            print(f"  {time.strftime('%H:%M:%S')}  {p.arquivo} "
                  f"[{p.indice + 1}/{p.total}]  {mbps:5.1f} MB/s", flush=True)

            if restantes[p.arquivo] == 0 and p.arquivo not in falhou:
                _juntar(p.arquivo, fatias[p.arquivo], partes, pasta)

    # Failover: quem não fechou por faixas tenta de novo, num fluxo só.
    for nome in list(falhou):
        print(f"  faixas falharam em {nome} ({falhou[nome]}) — modo sequencial", flush=True)
        try:
            _sequencial(competencia, nome, pendentes[nome], pasta / nome)
            for p in fatias[nome]:
                (partes / f"{p.arquivo}.{p.indice:03d}").unlink(missing_ok=True)
            del falhou[nome]
        except OSError as e:
            falhou[nome] = str(e)

    print("\nconferência final", flush=True)
    problemas = 0
    for nome, tam in itens.items():
        erro = conferir(pasta / nome, tam)
        if erro:
            print(f"  FALHA  {nome}: {erro}")
            problemas += 1
    try:
        partes.rmdir()
    except OSError:
        pass

    minutos = (time.time() - inicio) / 60
    print(f"{len(itens) - problemas}/{len(itens)} arquivos íntegros em {minutos:.1f} min")
    return problemas


def main() -> int:
    p = argparse.ArgumentParser(
        prog="download", description="Baixa uma competência do CNPJ da Receita Federal"
    )
    p.add_argument("--competencia", help="AAAA-MM (padrão: a mais recente publicada)")
    p.add_argument("--destino", default=os.getenv("OUTPUT_FILES_PATH", "./Download"),
                   help="raiz onde a pasta da competência é criada")
    p.add_argument("--conexoes", type=int, default=CONEXOES)
    p.add_argument("--aguardar", type=int, default=0, metavar="MIN",
                   help="espera a origem voltar ao ar por até MIN minutos antes de começar")
    p.add_argument("--listar", action="store_true", help="só lista o que há publicado")
    args = p.parse_args()

    if args.aguardar and not esperar_servidor(args.aguardar):
        print(f"origem não respondeu em {args.aguardar} min")
        return 1

    if args.listar:
        for c in listar_competencias():
            print(c)
        return 0

    competencia = args.competencia or listar_competencias()[-1]
    return baixar(competencia, Path(args.destino), args.conexoes)


if __name__ == "__main__":
    sys.exit(main())
