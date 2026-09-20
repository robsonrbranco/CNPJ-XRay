#!/usr/bin/env bash
#
# Troca a base do Themis no host do Olympus.
#
# Roda NO HOST, com o pod parado. Tudo que acontece com o serviço fora do ar
# está aqui, num script só: cada ida e volta de SSH a mais seria uma janela em
# que a conexão cai com o serviço parado e a base pela metade.
#
# O detalhe que decide se isto cabe no disco
# ------------------------------------------
# A base tem 34,2 GB e as partes somam ~12,2 GB. Concatenar tudo e descompactar
# exigiria 46,4 GB de pico, contra ~46 GB livres no node -- sem margem.
#
# Alimentando o `gunzip` parte a parte e **apagando cada uma assim que é
# consumida**, o total em disco em qualquer instante é
#
#     12,2·(1-f) + 34,2·f
#
# que cresce até 34,2 GB no fim. O pico vira o tamanho da própria base. São
# 12 GB de diferença, e é a razão de o laço abaixo ter um `rm` dentro do pipe.
#
# Ordem das operações
# -------------------
# Conferir TODAS as partes antes de encostar no pod. Parar o serviço para
# descobrir que falta uma parte seria downtime gratuito.
#
# Apagar a base antiga antes de descompactar é aceitável porque a estação
# continua com o original: o pior caso é downtime estendido, não perda de dado.

set -euo pipefail

ENVIO="${ENVIO:?ENVIO nao definido}"
PASTA_FDB="${PASTA_FDB:?PASTA_FDB nao definido}"
NOME_BASE="${NOME_BASE:-cnpj_xray.fdb}"
DEPLOYMENT="${DEPLOYMENT:-themis}"
NAMESPACE="${NAMESPACE:-olympus}"

ALVO="$PASTA_FDB/$NOME_BASE"
MANIFESTO="$ENVIO/manifesto-envio.json"

msg() { printf '%s  %s\n' "$(date +%H:%M:%S)" "$*"; }
erro() { printf '%s  ERRO: %s\n' "$(date +%H:%M:%S)" "$*" >&2; exit 1; }

json() { python3 -c "import json,sys; print(json.load(open('$MANIFESTO'))$1)"; }

[ -f "$MANIFESTO" ] || erro "manifesto nao encontrado em $MANIFESTO"

FDB_BYTES=$(json "['fdb_bytes']")
FDB_SHA=$(json "['fdb_sha256']")
N_PARTES=$(json "['partes'].__len__()")

msg "base esperada: $((FDB_BYTES / 1000000000)) GB em $N_PARTES partes"

# --- 1. conferir TODAS as partes, antes de tocar no pod ---------------------

msg "conferindo o hash das $N_PARTES partes"
FALTA=0
for i in $(seq 0 $((N_PARTES - 1))); do
    NOME=$(json "['partes'][$i]['nome']")
    SHA=$(json "['partes'][$i]['sha256']")
    ARQ="$ENVIO/$NOME"
    if [ ! -f "$ARQ" ]; then
        msg "  FALTA    $NOME"; FALTA=$((FALTA + 1)); continue
    fi
    REAL=$(sha256sum "$ARQ" | cut -d' ' -f1)
    if [ "$REAL" != "$SHA" ]; then
        msg "  CORROMPIDA $NOME"; FALTA=$((FALTA + 1))
    fi
done
[ "$FALTA" -eq 0 ] || erro "$FALTA parte(s) faltando ou corrompida(s) — nada foi alterado"
msg "  todas conferidas"

# --- 2. espaco em disco -----------------------------------------------------
# O pico e o tamanho da base, porque as partes somem conforme sao consumidas.
# Mas a base ANTIGA ainda esta la neste momento, e so sai no passo 4.

LIVRE=$(df -B1 --output=avail "$PASTA_FDB" | tail -1)
ANTIGA=0
[ -f "$ALVO" ] && ANTIGA=$(stat -c%s "$ALVO")
DISPONIVEL=$((LIVRE + ANTIGA))
msg "disco: $((LIVRE / 1000000000)) GB livres + $((ANTIGA / 1000000000)) GB da base antiga"
if [ "$DISPONIVEL" -lt "$((FDB_BYTES + 2000000000))" ]; then
    erro "espaco insuficiente: precisa de $((FDB_BYTES / 1000000000)) GB + folga, ha $((DISPONIVEL / 1000000000)) GB"
fi

# --- 3. parar o pod ---------------------------------------------------------
# scale 0 e nao delete pod: o Deployment recriaria o pod imediatamente, e ele
# subiria em cima de uma base sendo descompactada.

msg "parando $DEPLOYMENT (scale 0)"
kubectl -n "$NAMESPACE" scale deployment "$DEPLOYMENT" --replicas=0
kubectl -n "$NAMESPACE" wait --for=delete pod -l "app=$DEPLOYMENT" --timeout=180s 2>/dev/null || true
msg "  parado"

# A partir daqui o servico esta FORA DO AR. Qualquer saida sem religar deixa o
# Themis parado, entao o trap cuida disso.
religar() {
    msg "religando $DEPLOYMENT"
    kubectl -n "$NAMESPACE" scale deployment "$DEPLOYMENT" --replicas=1 || true
}
trap religar EXIT

# --- 4. apagar a antiga e descompactar a nova -------------------------------

if [ -f "$ALVO" ]; then
    msg "apagando a base antiga ($((ANTIGA / 1000000000)) GB)"
    rm -f "$ALVO"
fi

msg "descompactando (apagando cada parte apos consumi-la)"
T0=$(date +%s)
(
    for i in $(seq 0 $((N_PARTES - 1))); do
        NOME=$(printf 'base.gz.%03d' "$i")
        cat "$ENVIO/$NOME"
        rm -f "$ENVIO/$NOME"
    done
) | gzip -dc > "$ALVO"
msg "  $(( ($(date +%s) - T0) / 60 )) min"

# --- 5. conferir o que ficou -----------------------------------------------

REAL_BYTES=$(stat -c%s "$ALVO")
if [ "$REAL_BYTES" -ne "$FDB_BYTES" ]; then
    erro "tamanho nao confere: $REAL_BYTES != $FDB_BYTES (a base esta INVALIDA)"
fi
msg "tamanho confere: $((REAL_BYTES / 1000000000)) GB"

# O hash e caro (~34 GB de leitura, alguns minutos) mas e a unica prova real.
# O gzip ja valida CRC na descompactacao, entao isto e cinto e suspensorio --
# e barato perto de servir uma base corrompida por um mes.
msg "conferindo sha256 da base descompactada"
REAL_SHA=$(sha256sum "$ALVO" | cut -d' ' -f1)
[ "$REAL_SHA" = "$FDB_SHA" ] || erro "sha256 nao confere — a base esta INVALIDA"
msg "  sha256 confere"

rm -f "$MANIFESTO"
rmdir "$ENVIO" 2>/dev/null || true

# --- 6. religar -------------------------------------------------------------
# O trap ja religa na saida; aqui so esperamos ficar pronto para reportar.

trap - EXIT
religar
msg "aguardando o pod ficar pronto"
kubectl -n "$NAMESPACE" rollout status deployment/"$DEPLOYMENT" --timeout=300s
msg "troca concluida"
