# `/manager` — credenciais, log e estatísticas

Rota administrativa da API: cadastro de contratantes, ciclo de vida das
credenciais e estatísticas de uso, analíticas e sintéticas.

Complementa [api-compativel-serpro.md](api-compativel-serpro.md), que descreve
a parte pública — aquela é compatível com o SERPRO, esta é nossa.

## O que isto muda no desenho do pod

**O pod deixa de ser imutável.** Até aqui os dois volumes podiam ser montados
somente leitura; gravar uma linha de log por requisição acaba com isso.

E derruba um argumento meu: recomendei JWT em parte porque a validação não
precisaria tocar armazenamento. Com log por requisição, o acesso a disco já
está no caminho quente. O JWT segue valendo — é autocontido, dispensa tabela de
sessão e sobrevive a reinício de worker — mas o benefício de "zero I/O" acabou.

Em compensação, **revogação fica barata**. Antes era preciso aceitar que um
token revogado valeria até expirar; agora dá para conferir o estado da
credencial sem custo novo.

## Armazenamento

Três volumes, com ciclos de vida independentes:

| volume | conteúdo | acesso | ciclo |
|---|---|---|---|
| `/data` | base CNPJ (`.fdb`) | **read-only** | trocada todo mês |
| `/creds` | credenciais (SQLite) | leitura e escrita | permanente |
| `/logs` | log de consultas | escrita | janela de retenção |

A base CNPJ continua imutável e read-only — nada aqui a toca.

**SQLite para credenciais**, não Firebird: volume baixo, escrita rara,
`sqlite3` é stdlib e o arquivo é trivial de copiar e versionar. Usar o engine
Firebird que já está na imagem seria reaproveitamento, mas embedded gravável
com vários workers é problema diferente de embedded somente leitura — e não há
motivo para importá-lo aqui.

### O log não vai para o SQLite no caminho quente

Uma linha por requisição, vindas de N workers, num único SQLite, é contenção de
lock exatamente onde não se quer. O desenho:

```
/logs/consultas-{pid}-{AAAA-MM-DD}.jsonl     cada worker escreve o SEU arquivo
/creds/estatisticas.db                       agregado sintético, consolidado
```

Cada worker **acrescenta** ao próprio arquivo, sem lock e sem coordenação. A
consolidação para o sintético roda periodicamente e sob demanda, lendo os
`.jsonl` fechados.

Isso também dá a separação que você pediu de graça:

* **analítico** — os `.jsonl`, uma linha por consulta, dentro da janela de
  retenção
* **sintético** — o agregado, que **sobrevive à poda do analítico**

Assim a estatística de uso de 2024 continua disponível depois de os detalhes de
2024 terem sido apagados.

## Credenciais

| campo | |
|---|---|
| `consumer_key` | identificador público, gerado |
| `secret_hash` | **só o hash** — o segredo em claro nunca é guardado |
| `contratante_nome`, `contratante_documento`, `contratante_email` | dados básicos |
| `criada_em` | |
| `status` | `ativa`, `suspensa`, `revogada` |
| `revogada_em` | preenchido na revogação |
| `quota_mensal` | limite contratado de consultas/mês; nulo = sem limite |
| `observacao` | livre |

### Decisões

**O segredo aparece uma única vez.** Na criação e na rotação, a resposta traz o
segredo em claro; depois disso, só o hash existe. Perdeu, rotaciona — não há
como recuperar, e é assim que deve ser.

**Rotação troca o segredo mantendo a chave.** O cliente atualiza um valor, não
dois, e o histórico de uso continua ligado à mesma credencial.

**O token do MCP é emitido aqui, e só aqui.** Ele vive meses, e um token
de meses é coisa que só o operador cunha — a API pública emite apenas o
token de uma hora do contrato do SERPRO. Ele cai na hora se a credencial
for revogada ou suspensa, e cai também se o segredo for rotacionado.
Detalhes em `docs/mcp.md`.

**Revogar não apaga.** `status` e `revogada_em` em vez de `DELETE`: o uso
histórico daquela credencial continua íntegro nas estatísticas. Credencial
apagada deixaria buraco no relatório do mês.

**`quota_mensal` é o contratado, não o observado.** O consumido vem do log. Os
dois lado a lado é o que permite dizer "usou 82% do contratado".

## Rotas

Todas sob `/manager`, e **nenhuma faz parte do contrato do SERPRO** — esta é a
parte nossa.

| método | rota | |
|---|---|---|
| `GET` | `/manager/credenciais` | lista, com filtro por status |
| `POST` | `/manager/credenciais` | inclui; devolve o segredo **uma vez** |
| `GET` | `/manager/credenciais/{key}` | consulta uma |
| `PATCH` | `/manager/credenciais/{key}` | altera dados do contratante, quota, status |
| `POST` | `/manager/credenciais/{key}/rotacionar` | novo segredo, mesma chave |
| `POST` | `/manager/credenciais/{key}/token-mcp` | token de longa duração para o `/mcp`; `?dias=` de 1 a 365, padrão 90 — ver `docs/mcp.md` |
| `DELETE` | `/manager/credenciais/{key}` | revoga (não apaga) |
| `GET` | `/manager/estatisticas` | sintético geral |
| `GET` | `/manager/estatisticas/{key}` | sintético de uma credencial |
| `GET` | `/manager/consultas` | analítico, com filtros e paginação |

## O que o log registra

Por consulta:

```
instante, consumer_key, rota, ni_consultado,
status_http, faturavel, duracao_ms, worker_pid
```

**`faturavel` imita o SERPRO**, que documenta 400, 401, 403, 500, 502 e 504
como transações não faturáveis. Contar tudo igual inflaria o uso do contratante
com erros que não são dele — e, no caso de 500, são nossos.

**Uma consideração sobre `ni_consultado`:** registrar qual CNPJ cada cliente
consulta é registrar a atividade comercial dele. Não é dado pessoal, mas é
sensível do ponto de vista de negócio — quem consulta o quê revela prospecção,
diligência, concorrência. Daí a janela de retenção do analítico ser curta e o
sintético não guardar o `ni`. Se a retenção do analítico não for necessária
para diagnóstico, o campo pode simplesmente não ser gravado.

## Autenticação do `/manager`

**Não pode ser a mesma credencial que ele gerencia.** Uma credencial capaz de
criar credenciais é escalada de privilégio por desenho.

A recomendação, em ordem de preferência:

1. **Porta separada, não exposta.** O `/manager` escuta noutra porta, acessível
   só de dentro do cluster ou por túnel. É a única opção que não depende de
   segredo nenhum estar certo.
2. **Credencial administrativa própria**, com sinalização explícita, guardada
   fora da tabela de consumidores.

Sem uma das duas, o `/manager` é o ponto mais valioso da superfície de ataque:
quem o alcança emite credenciais para si.

## Em aberto

* **Framework web** e se a API mora neste repositório.
* **Comportamento ao estourar a quota** — recusar com qual código? O SERPRO não
  documenta código de rate limit, então aqui não há contrato a imitar. 429 é o
  óbvio, mas quebra a promessa de que só aparecem os códigos do SERPRO.
* **Janela de retenção do analítico** — 90 dias é um ponto de partida razoável;
  depende de para que os detalhes serão usados.
* **Quem consolida o sintético** — tarefa periódica dentro do pod, ou sob
  demanda na primeira chamada de estatística do dia.
