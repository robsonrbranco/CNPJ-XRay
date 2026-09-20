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

## Quem consulta: a API

### O que o projeto de origem tinha

O fork de origem **não trazia código de API** — não há `fastapi`, `flask`,
`uvicorn`, router ou endpoint em nenhum commit dele. O que havia de consulta
era um script de linha de comando (`consultar_empresa.py`, hoje portado para
`src/consulta/empresa.py`) e arquivos `.sql`.

Mas existia uma API, **noutro repositório**, e o documento de operação do
upstream (`docs/atualizacao-base-receita.md`, removido daqui na 3.0.0 por
descrever tablespaces de PostgreSQL) a registrava como consumidora:

> Conexões da API caem no switch (`pg_terminate_backend`): a API fica alguns
> segundos em `receita_db: degraded` e reconecta sozinha. Para zero erro, pare
> a API no switch.

Disso se extrai o desenho antigo: API separada, conectando ao PostgreSQL **pela
rede**, com endpoint de saúde reportando o estado do banco, num servidor com
volume dedicado.

Este trecho fica registrado aqui porque é a única memória de que a base tem um
consumidor — e ela quase se perdeu junto com a operação obsoleta que a cercava.

### O que muda com o embedded

| | upstream (servidor + API) | pod embedded |
|---|---|---|
| ligação API ↔ banco | TCP na 5432 | **mesmo processo** |
| durante a troca | `pg_terminate_backend`, `degraded`, reconecta | o pod para; não há conexão a cair |
| pool de conexões | necessário | cada worker tem seu próprio attachment |
| credencial | usuário e senha | nenhuma |

O ciclo de "cai, fica degradada, reconecta" **desaparece**: não existe conexão
de rede entre a API e o banco. A API para junto com o pod, que é exatamente o
downtime controlado do procedimento acima.

### Como a API se encaixa

É uma casca fina sobre o que já existe. `src/consulta/empresa.py` expõe
`consultar(cnpj_basico)`, que devolve a ficha inteira como dicionário —
empresa, estabelecimentos, sócios, Simples e CNAEs secundários resolvidos. Um
endpoint é pouco mais que serializar isso.

Três pontos que o embedded impõe:

* **Vários workers exigem `ServerMode = Classic`.** Medido: com `Super`, um
  worker sobe e os demais morrem no attach. Com `Classic`, 4 de 4 conectam.
* **Não há pool entre processos.** Cada worker abre seu attachment ao subir e o
  mantém. A base é read-only e imutável, então não há invalidação de cache nem
  reconexão a gerenciar.
* **O endpoint de saúde deve dizer qual competência está em disco**, lendo do
  manifesto de publicação. É o equivalente honesto ao `receita_db: degraded` do
  upstream: em vez de informar se a conexão está de pé — o que no embedded é
  redundante com o pod estar de pé — informa *o que* está sendo servido.

## O que ainda não está definido

**Onde o pod roda** — estação, servidor próprio, nuvem. Isso decide como os
34 GB chegam ao volume (cópia local em minutos, ou rede em possivelmente
horas) e, portanto, o tamanho real da janela de indisponibilidade. O passo 3
acima é o único do procedimento que fica em aberto.

**A API é deste repositório ou de outro?** No upstream era de outro. Trazê-la
para cá acrescenta uma dependência web e o projeto deixa de ser só ETL.

**Quais endpoints.** Só a ficha por CNPJ, ou também busca por razão social, UF
ou CNAE? A resposta decide quais índices fazem falta — hoje há 15, escolhidos
para consulta por chave e para os recortes de `uf`, `municipio`,
`situacao_cadastral` e `capital_social`. Busca textual por trecho de razão
social **não tem como ser atendida**: o Firebird 3.0 não tem equivalente a
trigrama, e a collation `WIN_PTBR` resolve caixa e acento, mas não busca por
pedaço no meio do nome.

**Exposição.** Se a API for acessível de fora do pod, autenticação volta ao
desenho — não no Firebird, que em embedded não autentica, mas na camada HTTP.
