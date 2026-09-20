#!/usr/bin/env bash
#
# Para o Themis e apaga a base ANTES da transmissão.
#
# Roda NO HOST. É o primeiro passo da publicação, não o terceiro.
#
# Por que antes
# -------------
# O node tem 41 GB livres e a base ocupa 34,2 GB. Com a base de setembro em
# disco sobram 6,8 GB, e as partes comprimidas precisam de 12,2 GB: a
# transmissão não cabe. O `scp` encheria o disco do node -- e disco cheio num
# k3s não derruba só o Themis, gera DiskPressure e despeja pods dos outros
# cinco serviços.
#
# Apagando a base primeiro, os 41 GB ficam livres: as partes entram com folga e
# a descompactação, que consome e apaga parte a parte, tem pico de 34,2 GB.
#
# O que isso custa
# ----------------
# Downtime. Deixa de ser só a descompactação (~15 min) e passa a cobrir a
# transmissão inteira -- 1 a 4 h conforme o upload. É o preço de não ter disco
# separado, e foi escolha consciente.
#
# O que isso NÃO custa: dado. A estação continua com o .fdb original. O pior
# caso é o serviço ficar fora do ar até alguém retomar o envio, que é
# retomável: parte cujo hash já confere no host não é reenviada.

set -euo pipefail

PASTA_FDB="${PASTA_FDB:?PASTA_FDB nao definido}"
NOME_BASE="${NOME_BASE:-cnpj_xray.fdb}"
DEPLOYMENT="${DEPLOYMENT:-themis}"
NAMESPACE="${NAMESPACE:-olympus}"
ENVIO="${ENVIO:-$PASTA_FDB/envio}"

ALVO="$PASTA_FDB/$NOME_BASE"

msg() { printf '%s  %s\n' "$(date +%H:%M:%S)" "$*"; }

msg "parando $DEPLOYMENT (scale 0)"
kubectl -n "$NAMESPACE" scale deployment "$DEPLOYMENT" --replicas=0
kubectl -n "$NAMESPACE" wait --for=delete pod -l "app=$DEPLOYMENT" --timeout=180s 2>/dev/null || true
msg "  parado -- O SERVICO ESTA FORA DO AR a partir de agora"

# Sobra de um envio interrompido antes. Sai junto: sao partes de outra base, e
# ocupam justamente o espaco de que precisamos.
if [ -d "$ENVIO" ]; then
    SOBRA=$(du -sb "$ENVIO" 2>/dev/null | cut -f1)
    if [ "${SOBRA:-0}" -gt 0 ]; then
        msg "apagando sobra de envio anterior ($((SOBRA / 1000000)) MB)"
        rm -rf "${ENVIO:?}"/*
    fi
fi

if [ -f "$ALVO" ]; then
    ANTIGA=$(stat -c%s "$ALVO")
    msg "apagando a base antiga ($((ANTIGA / 1000000000)) GB)"
    rm -f "$ALVO"
else
    msg "nao havia base em $ALVO (primeira publicacao)"
fi

mkdir -p "$ENVIO"

LIVRE=$(df -B1 --output=avail "$PASTA_FDB" | tail -1)
msg "disco livre agora: $((LIVRE / 1000000000)) GB"
msg "pronto para receber as partes"
