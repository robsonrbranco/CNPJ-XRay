# Publicação da base no pod de consulta

Procedimento mensal para levar a base pronta da estação até o pod que serve
consultas.

## O desenho

| | |
|---|---|
| **ETL** | roda na estação, como hoje — pesado, paralelo, ~6 h |
| **pod** | só serve consultas: Firebird 3 **embedded**, sem servidor |
| **base** | num **volume**, montado pelo pod |
| **troca** | para o pod, apaga a base velha, copia a nova, confere, sobe |

A separação existe porque os dois lados têm perfis opostos. A carga usa 8
processos e 2 GB de cache; o pod lê uma base imutável. Rodar o ETL dentro do
pod obrigaria a imagem a carregar tudo o que a carga precisa, e o embedded a
conviver com concorrência de escrita.

### Por que embedded

Medido em Linux, 4 processos contra o mesmo arquivo:

| `ServerMode` | conexões simultâneas |
|---|---:|
| `Super` (padrão) | 1 de 4 |
| `Classic` | **4 de 4** |

Vale tanto para base read-only quanto gravável — **a variável é o `ServerMode`,
não o read-only**. Em `Super` o engine toma o arquivo no *attach*, antes de
existir transação, e barra os demais.

Com `Classic`, o pod atende vários leitores sem servidor: **sem porta 3050, sem
`security3.fdb`, sem credencial**. A questão da senha de fábrica deixa de ser
mitigada e passa a não existir.

Também medido: o engine lê normalmente de um volume montado **somente
leitura**, com 4 leitores simultâneos. O volume de dados pode ser imutável.

### A imagem

```
libfbclient.so.2
plugins/libEngine12.so
firebird.conf     -> Providers = Engine12
                     ServerMode = Classic
<sua aplicação>
```

Dezenas de MB. Não precisa do executável `firebird`, nem do security database,
nem de `gbak`/`gfix`/`gstat` — a conferência abaixo é toda por SQL.

**O diretório de lock precisa ser gravável** (`/tmp/firebird`, dono `firebird`).
Ele existe mesmo com base read-only, então um root somente leitura exige um
`tmpfs` ali.

**Embedded não autentica, mas o usuário ainda decide privilégio.** Passe
`SYSDBA` explicitamente; sem isso a conexão entra como o usuário do sistema
operacional. Senha não é usada.

## O procedimento

### 1. Na estação, depois do pipeline

O manifesto tem que ser gerado **depois** de a base virar read-only — a última
fase do pipeline. O `gfix` altera o header, e portanto o hash: gerar antes
produz um manifesto que nunca vai conferir.

```bash
python -m src.publicacao.manifesto gerar --competencia 2026-10 --saida manifesto-2026-10.json
```

Leva alguns minutos, dominado pelo SHA-256 de 34 GB.

### 2. Parar o pod

### 3. Apagar a base do volume e copiar a nova

Apagar antes de copiar é deliberado: **não há espaço para duas bases de 34 GB**.

Isso é aceitável porque **a estação continua com o original**. O pior caso não é
perda de dado — é downtime estendido pelo tempo de uma nova cópia. Não é porta
de mão única.

Copie também o `manifesto-AAAA-MM.json` para o volume.

### 4. Conferir ANTES de subir o pod

Este é o passo que paga a economia de espaço. Como a base antiga já não existe,
não há com o que comparar: a conferência vem do manifesto.

```bash
python -m src.publicacao.manifesto conferir \
    --base /data/cnpj_xray.fdb \
    --manifesto /data/manifesto-2026-10.json \
    --anterior /data/manifesto-2026-09.json
```

Confere:

* **SHA-256** — a única garantia de que os 34 GB chegaram inteiros. Uma página
  corrompida no meio do arquivo pode não aparecer por semanas, até alguém
  consultar justo aquele CNPJ.
* **`read only` no header** — sem isso uma base gravável entra em produção e o
  primeiro UPDATE acidental a corrompe.
* **page size, charset, ODS, número de índices**
* **contagem por tabela** — pega o arquivo íntegro que não é o esperado:
  competência errada, ou carga incompleta promovida por engano.
* **régua de crescimento** contra o mês anterior.

Reprovou: **não suba o pod**, recopie.

### 5. Subir o pod

### 6. Consulta de fumaça

```bash
python -m src.consulta.empresa 08314885
```

### 7. Apagar o manifesto antigo do volume

Guarde o do mês corrente — ele vira o `--anterior` do mês que vem.

## A régua de crescimento

A base **cresce em número de linhas** todo mês. Encolher é sinal de carga
incompleta, e o `conferir` reprova.

**A régua é linha, não byte.** Nosso próprio histórico tem o contraexemplo:

| competência | linhas | arquivo |
|---|---:|---:|
| 2026-08 | 220.339.194 | 38,05 GB |
| 2026-09 | 222.197.006 | **34,20 GB** |

As linhas subiram 1,86 milhão e o arquivo **encolheu 10%**, porque `USE_FULL`
entrou entre as duas cargas. Uma regra baseada em tamanho teria reprovado a base
correta.

Crescimento acima de **5%** num mês também é sinalizado — não reprova por si,
mas pede olhar. A série observada cresce menos de 1% ao mês; 5% dá folga sem
deixar passar uma carga duplicada. As tabelas de domínio ficam fora da régua:
elas oscilam para baixo sem que isso seja erro.

## O que ainda não está definido

**Onde o pod roda** — estação, servidor próprio, nuvem. Isso decide como os
34 GB chegam ao volume (cópia local em minutos, ou rede em possivelmente
horas) e, portanto, o tamanho real da janela de indisponibilidade. O passo 3
acima é o único que fica em aberto.
