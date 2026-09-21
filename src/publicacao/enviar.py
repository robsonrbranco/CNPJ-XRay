#!/usr/bin/env python3
"""Publica a base no host do Olympus: compacta, fatia, envia e troca.

    python -m src.publicacao.enviar preparar          compacta e fatia
    python -m src.publicacao.enviar parar             para o pod e apaga a base
    python -m src.publicacao.enviar enviar            transmite em paralelo
    python -m src.publicacao.enviar trocar            descompacta e sobe o pod
    python -m src.publicacao.enviar publicar          as quatro em sequencia

Por que compactar e fatiar
--------------------------
Medido na base real: gzip -6 dá **2,81x** — 34,2 GB viram ~12,2 GB. São 22 GB
a menos de rede, e numa ligação doméstica isso é a diferença entre uma hora e
três.

Fatiar resolve outra coisa: um `scp` de 12 GB que falha em 90% perde tudo. Com
partes de 512 MB, uma falha custa uma parte, e as demais já verificadas não são
reenviadas.

O pico de disco no host
-----------------------
O host tem **41 GB livres** (medido no host em 20/09/2026) e a base ocupa
34,2 GB. Isso manda em duas decisões deste módulo.

**A base antiga sai antes da transmissão.** Com ela em disco sobram 6,8 GB, e
as partes precisam de 12,2 GB — a transmissão não cabe. Pior: o `scp` encheria
o disco do node, e disco cheio num k3s não derruba só o Themis, gera
DiskPressure e despeja pods dos outros cinco serviços. Por isso `parar` vem
antes de `enviar`, e não depois.

O preço é downtime: deixa de ser só a descompactação (~15 min) e passa a
cobrir a transmissão inteira, 1 a 4 h conforme o upload. Não custa dado — a
estação continua com o original, e o envio é retomável.

**Cada parte é apagada assim que é consumida.** Com os 41 GB livres, o total
em disco durante a descompactação é `12,2·(1-f) + 34,2·f`, que cresce até
34,2 GB. Concatenando tudo primeiro seriam 46,4 GB, que não cabem.

Duas bases nunca caberiam: 34,2 × 2 = 68,4 GB contra 43 GB úteis. Não há
versão deste processo que mantenha a base antiga até validar a nova.

O que NÃO é automatizado
------------------------
A decisão de publicar. A troca para o pod, apaga a base e descompacta — se algo
falhar no meio, o serviço fica fora até alguém agir. Isso é aceitável porque a
estação continua com o original, mas não é coisa para disparar sozinho num
gatilho de CI.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import zlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# 512 MB: grande o bastante para o custo por conexão SSH sumir, pequeno o
# bastante para uma parte perdida custar pouco reenvio.
TAMANHO_PARTE = 512 * 1024 * 1024
BLOCO = 8 * 1024 * 1024

# gzip -6 e não -9: medido, -9 ganha pouco e custa muito mais CPU. E não zstd,
# que comprime melhor e mais rápido, porque `gzip` está em qualquer host Linux
# e o zstd pode não estar — a troca acontece num servidor que não controlamos
# pacote a pacote.
NIVEL_GZIP = 6

PARALELAS = 4
TENTATIVAS = 5


def _env(nome: str, padrao: str = "") -> str:
    v = os.getenv(nome)
    return v if v not in (None, "") else padrao


@dataclass
class Destino:
    host: str
    usuario: str
    porta: int
    pasta_fdb: str
    pasta_db: str
    nome_base: str
    deployment: str
    namespace: str
    url_saude: str

    @classmethod
    def do_ambiente(cls) -> "Destino":
        host = _env("OLYMPUS_HOST")
        if not host:
            raise RuntimeError(
                "OLYMPUS_HOST não definido — sem ele não há para onde enviar"
            )
        return cls(
            host=host,
            usuario=_env("OLYMPUS_USER", "root"),
            porta=int(_env("OLYMPUS_PORT", "22")),
            pasta_fdb=_env("OLYMPUS_PASTA_FDB", "/cnpj-xray-fdb"),
            pasta_db=_env("OLYMPUS_PASTA_DB", "/cnpj-xray-db"),
            nome_base=_env("OLYMPUS_NOME_BASE", "cnpj_xray.fdb"),
            deployment=_env("OLYMPUS_DEPLOYMENT", "themis"),
            namespace=_env("OLYMPUS_NAMESPACE", "olympus"),
            url_saude=_env("OLYMPUS_URL_SAUDE", "https://themis.ecomciencia.com/saude"),
        )

    @property
    def envio(self) -> str:
        """Subpasta onde as partes pousam antes da troca.

        Separada da base em uso: `scp` direto por cima do `.fdb` que o pod está
        servindo corromperia a base em produção no meio de uma consulta.
        """
        return f"{self.pasta_fdb}/envio"

    def ssh(self, *comando: str) -> list[str]:
        return ["ssh", "-p", str(self.porta),
                f"{self.usuario}@{self.host}", *comando]


def sha256(caminho: Path) -> str:
    h = hashlib.sha256()
    with open(caminho, "rb") as f:
        while bloco := f.read(BLOCO):
            h.update(bloco)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# 1. preparar
# ---------------------------------------------------------------------------

def preparar(fdb: Path, trabalho: Path) -> dict:
    """Compacta e fatia, gravando um manifesto com o hash de cada parte."""
    if trabalho.exists():
        shutil.rmtree(trabalho)
    trabalho.mkdir(parents=True)

    bruto = fdb.stat().st_size
    print(f"compactando {bruto / 1e9:.2f} GB (gzip -{NIVEL_GZIP}) e fatiando em "
          f"{TAMANHO_PARTE // 1024 // 1024} MB ...", flush=True)

    t0 = time.time()
    partes: list[dict] = []
    indice = 0
    atual = None
    escrito_na_parte = 0
    comprimido_total = 0

    def abrir_parte(i: int):
        return open(trabalho / f"base.gz.{i:03d}", "wb")

    with open(fdb, "rb") as entrada:
        # zlib com `wbits = 16 + MAX_WBITS` produz fluxo gzip. É preciso o
        # fluxo, e não um arquivo, para fatiar enquanto comprime — `GzipFile`
        # exigiria materializar 12 GB intermediários em disco antes de cortar.
        co = zlib.compressobj(NIVEL_GZIP, zlib.DEFLATED, 16 + zlib.MAX_WBITS)
        atual = abrir_parte(indice)
        lidos = 0

        def emitir(dados: bytes):
            nonlocal atual, indice, escrito_na_parte, comprimido_total
            pos = 0
            while pos < len(dados):
                espaco = TAMANHO_PARTE - escrito_na_parte
                fatia = dados[pos:pos + espaco]
                atual.write(fatia)
                escrito_na_parte += len(fatia)
                comprimido_total += len(fatia)
                pos += len(fatia)
                if escrito_na_parte >= TAMANHO_PARTE:
                    atual.close()
                    indice += 1
                    atual = abrir_parte(indice)
                    escrito_na_parte = 0

        while bloco := entrada.read(BLOCO):
            lidos += len(bloco)
            emitir(co.compress(bloco))
            if lidos % (BLOCO * 64) == 0:
                print(f"\r  {lidos / bruto * 100:5.1f}%  ->  "
                      f"{comprimido_total / 1e9:5.2f} GB", end="", flush=True)
        emitir(co.flush())
        atual.close()
        print(f"\r  100.0%  ->  {comprimido_total / 1e9:5.2f} GB", flush=True)

    # Se o fluxo terminou em múltiplo exato de TAMANHO_PARTE, o laço fechou a
    # parte cheia e abriu a seguinte, que ficou vazia. Concatenar um arquivo de
    # zero byte não estragaria a base, mas ele entraria no manifesto como parte
    # legítima — e o host gastaria uma transmissão e uma conferência com nada.
    ultima = trabalho / f"base.gz.{indice:03d}"
    if ultima.stat().st_size == 0:
        ultima.unlink()

    for arq in sorted(trabalho.glob("base.gz.*")):
        partes.append({"nome": arq.name, "bytes": arq.stat().st_size,
                       "sha256": sha256(arq)})

    manifesto = {
        "fdb_bytes": bruto,
        "fdb_sha256": sha256(fdb),
        "comprimido_bytes": comprimido_total,
        "razao": round(bruto / comprimido_total, 2),
        "partes": partes,
        "preparado_em": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    (trabalho / "manifesto-envio.json").write_text(
        json.dumps(manifesto, indent=2), encoding="utf-8")

    dt = time.time() - t0
    print(f"  {len(partes)} partes, {comprimido_total / 1e9:.2f} GB "
          f"({manifesto['razao']}x) em {dt / 60:.1f} min", flush=True)
    return manifesto


# ---------------------------------------------------------------------------
# 2. enviar
# ---------------------------------------------------------------------------

def _parte_ja_no_host(d: Destino, parte: dict) -> bool:
    """Confere tamanho E hash no host — tamanho sozinho não prova nada."""
    r = subprocess.run(
        d.ssh(f"sha256sum {d.envio}/{parte['nome']} 2>/dev/null | cut -d' ' -f1"),
        capture_output=True, text=True,
    )
    return r.stdout.strip() == parte["sha256"]


def _enviar_parte(d: Destino, trabalho: Path, parte: dict) -> tuple[str, bool, str]:
    origem = trabalho / parte["nome"]
    for tentativa in range(1, TENTATIVAS + 1):
        if _parte_ja_no_host(d, parte):
            return parte["nome"], True, f"ok (já estava, tentativa {tentativa})"
        r = subprocess.run(
            ["scp", "-P", str(d.porta), "-q", str(origem),
             f"{d.usuario}@{d.host}:{d.envio}/"],
            capture_output=True, text=True,
        )
        if r.returncode == 0 and _parte_ja_no_host(d, parte):
            return parte["nome"], True, f"ok (tentativa {tentativa})"
        time.sleep(min(2 ** tentativa, 30))
    return parte["nome"], False, f"falhou após {TENTATIVAS} tentativas"


def enviar(d: Destino, trabalho: Path, paralelas: int = PARALELAS) -> int:
    """Transmite as partes em paralelo, conferindo o hash de cada uma no host.

    Retomável: parte cujo hash já bate no destino não é reenviada, então
    repetir o comando depois de uma queda continua de onde parou.
    """
    manifesto = json.loads((trabalho / "manifesto-envio.json").read_text(encoding="utf-8"))
    partes = manifesto["partes"]

    subprocess.run(d.ssh(f"mkdir -p {d.envio}"), check=True, capture_output=True)

    # Conferir o espaço AQUI, e não no fim. O `trocar-base.sh` também confere,
    # mas àquela altura já se gastou de 1 a 4 h transmitindo — e, pior, o `scp`
    # teria enchido o disco do node no caminho. Disco cheio num k3s não derruba
    # só o Themis: gera DiskPressure e despeja pods dos outros serviços.
    #
    # O limite é o tamanho da BASE, não o das partes: elas somem conforme são
    # consumidas, então o pico da descompactação é 34,2 GB. Quem passa nesse
    # teste passa nos dois.
    livre = espaco_livre_no_host(d)
    preciso = manifesto["fdb_bytes"] + 2_000_000_000
    if livre < 0:
        print("não consegui ler o espaço livre no host — seguindo assim mesmo",
              flush=True)
    elif livre < preciso:
        print(f"ESPAÇO INSUFICIENTE em {d.host}:{d.pasta_fdb}\n"
              f"  livre:   {livre / 1e9:.1f} GB\n"
              f"  preciso: {preciso / 1e9:.1f} GB (base + folga)\n"
              f"A base antiga ainda está lá? Rode 'parar' antes de 'enviar'.",
              flush=True)
        return len(partes)
    else:
        print(f"disco no host: {livre / 1e9:.1f} GB livres, "
              f"preciso de {preciso / 1e9:.1f} GB", flush=True)

    print(f"enviando {len(partes)} partes para {d.host}:{d.envio} "
          f"({paralelas} em paralelo) ...", flush=True)
    t0 = time.time()
    falhas = []
    with ThreadPoolExecutor(max_workers=paralelas) as pool:
        futuros = {pool.submit(_enviar_parte, d, trabalho, p): p for p in partes}
        for i, fut in enumerate(as_completed(futuros), 1):
            nome, ok, msg = fut.result()
            marca = "ok  " if ok else "FALHA"
            print(f"  [{i:>3}/{len(partes)}] {marca} {nome}  {msg}", flush=True)
            if not ok:
                falhas.append(nome)

    # O manifesto viaja junto: é o que o lado do host usa para conferir.
    subprocess.run(["scp", "-P", str(d.porta), "-q",
                    str(trabalho / "manifesto-envio.json"),
                    f"{d.usuario}@{d.host}:{d.envio}/"], check=True)

    dt = time.time() - t0
    mb = manifesto["comprimido_bytes"] / 1e6
    print(f"\n{len(partes) - len(falhas)}/{len(partes)} partes em {dt / 60:.1f} min "
          f"({mb / dt:.1f} MB/s)", flush=True)
    if falhas:
        print(f"FALHARAM: {', '.join(falhas)}", flush=True)
        print("Rode 'enviar' de novo — as partes já conferidas não são reenviadas.")
    return len(falhas)


# ---------------------------------------------------------------------------
# 3. trocar
# ---------------------------------------------------------------------------

def _rodar_no_host(d: Destino, script: Path) -> int:
    """Manda um script para o host e o executa lá, com o ambiente do destino.

    Tudo que acontece com o pod parado roda LÁ, num script só: cada ida e volta
    de SSH a mais é uma janela em que a conexão pode cair com o serviço fora do
    ar e a base pela metade.
    """
    print(f"enviando {script.name} e executando em {d.host} ...", flush=True)
    subprocess.run(["scp", "-P", str(d.porta), "-q", str(script),
                    f"{d.usuario}@{d.host}:/tmp/{script.name}"], check=True)
    r = subprocess.run(
        d.ssh(f"chmod +x /tmp/{script.name} && "
              f"ENVIO={d.envio} PASTA_FDB={d.pasta_fdb} NOME_BASE={d.nome_base} "
              f"DEPLOYMENT={d.deployment} NAMESPACE={d.namespace} "
              f"/tmp/{script.name}"),
        text=True,
    )
    return r.returncode


def parar(d: Destino, script: Path) -> int:
    """Para o pod e apaga a base, ANTES de transmitir.

    Esta é a ordem que o espaço em disco impõe: com a base antiga lá, as partes
    não cabem. Ver o cabeçalho do módulo.
    """
    return _rodar_no_host(d, script)


def trocar(d: Destino, script: Path) -> int:
    """Descompacta as partes já transmitidas e sobe o pod."""
    return _rodar_no_host(d, script)


def espaco_livre_no_host(d: Destino) -> int:
    """Bytes livres na pasta da base, lidos no host."""
    r = subprocess.run(
        d.ssh(f"df -B1 --output=avail {d.pasta_fdb} | tail -1"),
        capture_output=True, text=True,
    )
    try:
        return int(r.stdout.strip())
    except ValueError:
        return -1


#: O `User-Agent` padrão do `urllib` é `Python-urllib/3.x`, e o Cloudflare o
#: recusa com 403 `error code: 1010` — bloqueio por assinatura de cliente. Foi
#: o que aconteceu na publicação de 2026-09: o serviço subiu certo e a
#: confirmação reportou falha dez vezes seguidas.
#:
#: Medido no domínio em 21/09/2026: só o `Python-urllib` é barrado.
#: `python-requests`, `curl`, `Java`, `Go-http-client`, `PostmanRuntime`,
#: `axios` e `okhttp` recebem 200. Ou seja, isto atingia esta função, não os
#: consumidores da API.
AGENTE = "CNPJ-XRay-publicacao/1.0"


def conferir_servico(d: Destino) -> int:
    import urllib.error
    import urllib.request

    print(f"\nconferindo {d.url_saude} ...", flush=True)
    pedido = urllib.request.Request(d.url_saude,
                                    headers={"User-Agent": AGENTE})
    for tentativa in range(1, 11):
        try:
            with urllib.request.urlopen(pedido, timeout=20) as resp:
                corpo = json.load(resp)
            print(f"  {json.dumps(corpo, ensure_ascii=False)}", flush=True)
            if corpo.get("status") == "ok":
                print(f"\nNO AR servindo a competência "
                      f"{corpo.get('competencia') or '?'}", flush=True)
                return 0
            print(f"  tentativa {tentativa}: status "
                  f"{corpo.get('status')!r}", flush=True)
        except urllib.error.HTTPError as e:
            # O código importa: 503 é o pod ainda subindo, e vale esperar; 403
            # é o Cloudflare barrando o cliente, e esperar não resolve. Dizer só
            # "HTTPError" esconde essa diferença — foi o que me custou a
            # investigação da publicação de 2026-09.
            print(f"  tentativa {tentativa}: HTTP {e.code} "
                  f"({e.headers.get('server') or '?'})", flush=True)
        except Exception as e:                               # noqa: BLE001
            print(f"  tentativa {tentativa}: {type(e).__name__}: {e}",
                  flush=True)
        time.sleep(10)
    print("\nO serviço NÃO confirmou. Investigue antes de dar por concluído.")
    return 1


def main() -> int:
    p = argparse.ArgumentParser(prog="enviar",
                               description="Publica a base no host do Olympus")
    p.add_argument("acao",
                   choices=["preparar", "parar", "enviar", "trocar", "publicar"])
    p.add_argument("--fdb", default=os.getenv("DB_NAME", "./bd/cnpj_xray.fdb"))
    p.add_argument("--trabalho", default="./envio")
    p.add_argument("--paralelas", type=int, default=PARALELAS)
    p.add_argument("--script", default="./deploy/trocar-base.sh")
    p.add_argument("--script-parada", default="./deploy/parar-e-limpar.sh")
    args = p.parse_args()

    trabalho = Path(args.trabalho)

    if args.acao == "preparar":
        preparar(Path(args.fdb), trabalho)
        return 0

    d = Destino.do_ambiente()

    if args.acao == "parar":
        return parar(d, Path(args.script_parada))

    if args.acao == "enviar":
        return 1 if enviar(d, trabalho, args.paralelas) else 0

    if args.acao == "trocar":
        return trocar(d, Path(args.script)) or conferir_servico(d)

    # publicar: as quatro em sequência, parando no primeiro erro.
    #
    # A ORDEM É O PONTO. `parar` vem antes de `enviar` porque as partes não
    # cabem em disco junto com a base antiga — e é por isso que `preparar`, que
    # é a etapa longa e roda inteira aqui na estação, vem antes de tudo: não
    # faz sentido derrubar o serviço para só então começar a comprimir 34 GB.
    preparar(Path(args.fdb), trabalho)

    if rc := parar(d, Path(args.script_parada)):
        print("\nNão consegui parar o pod e limpar. Nada foi transmitido.")
        return rc

    # Daqui em diante o serviço está FORA DO AR e a base antiga já foi apagada.
    if enviar(d, trabalho, args.paralelas):
        print("\nNÃO vou trocar com parte faltando.")
        print("O serviço está PARADO e sem base. Rode 'enviar' de novo para "
              "retomar de onde parou, e depois 'trocar'.")
        return 1

    if rc := trocar(d, Path(args.script)):
        print("\nA troca falhou. O pod pode estar parado — verifique.")
        return rc

    return conferir_servico(d)


if __name__ == "__main__":
    sys.exit(main())
