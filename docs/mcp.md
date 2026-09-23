# A camada MCP — `POST /mcp`

Consulta de CNPJ para **agentes de IA**, pelo Model Context Protocol. É a
segunda porta do mesmo serviço: a primeira, `/v2/...`, imita a Consulta CNPJ v2
do SERPRO e continua imitando; esta tem um contrato nosso, desenhado para quem
consome ser um modelo de linguagem.

Implementação em `src/api/mcp.py`, testes em `tests/test_api_mcp.py`. É porte
do piloto do CEP-XRay (Hestia), validado em produção em 23/09/2026 — o guia de
lá (`docs/mcp.md` do CEP-XRay) tem o raciocínio completo; aqui fica o que é do
Themis.

## Para que existe

O Themis é serviço de apoio no princípio de *data foundation*: a fonte
canônica de cadastro de empresa para os sistemas da casa. Agentes são os
consumidores em que isso mais falha — sem uma ferramenta, um agente **inventa**
uma razão social, uma situação cadastral ou um endereço plausível.

- **A credencial fica fora do alcance do modelo** — ela mora na configuração do
  cliente MCP.
- **A resposta é enxuta.** O recorte do SERPRO é verboso (domínios aninhados,
  campos sempre nulos); o do MCP entrega o que um agente usa.
- **As ressalvas chegam onde o modelo decide**: retrato mensal, CPF de sócio
  mascarado pela própria Receita, texto de terceiros é dado e não instrução, e
  não preencher de memória se a base estiver fora.

## Como um consumidor passa a usar

**1. O operador emite o token**, pelo `/manager` (não exposto):

```bash
kubectl -n olympus port-forward deploy/themis 8001:8001
```

```bash
curl -X POST "http://localhost:8001/manager/credenciais/<consumerKey>/token-mcp?dias=90"
```

O token não é guardado: perdeu, emite outro. A credencial é a mesma do REST —
mesma cota, mesma cobrança, mesmo contratante. Para agente, prefira uma
credencial **própria** do agente: cobrança e revogação ficam separadas, e o
`consumerSecret` pode ser descartado na criação, porque o token do MCP sai
direto do `/manager`.

**2. O consumidor configura o cliente MCP.** No OpenClaw, de dentro do cluster:

```bash
openclaw mcp add themis --url http://themis.olympus.svc:8000/mcp --header "Authorization=Bearer <token>"
```

De fora, `https://themis.ecomciencia.com/mcp`. A audiência do token é a URI
canônica, não o host da conexão, então o mesmo token vale nos dois caminhos.

## O que o servidor oferece

| ferramenta | para quê | cobrada |
|---|---|---|
| `validar_cnpj` | confere o dígito verificador, sem consultar a base | **não** |
| `situacao_cadastral` | resposta curta: razão social, matriz/filial, situação com data e motivo, abertura, município | sim |
| `consultar_empresa` | cadastro completo; com `incluir_socios: true`, também o quadro societário | sim |

E o recurso `themis://competencia`: competência, data de construção e número
de linhas da base servida. Toda resposta de consulta traz `competencia`.

### Dado pessoal

O quadro societário traz nome de pessoa física e o CPF **mascarado pela
própria Receita** (`***794780**`). `consultar_empresa` só o devolve com
`incluir_socios: true`: o padrão é não mandar dado pessoal para o contexto de
um modelo — e, por ele, para o provedor desse modelo — sem que a tarefa peça.

## O token

| | token do REST | token do MCP |
|---|---|---|
| quem emite | `POST /token` (público) | `/manager` (só o operador) |
| validade | 1 hora | 1 a 365 dias, padrão 90 |
| `aud` | ausente | `https://themis.ecomciencia.com/mcp` |
| vale em | `/v2/...` | `/mcp` |

Cada porta recusa o token da outra, e o token do Hestia não vale aqui — a
audiência é outra. Revogar ou suspender a credencial derruba o token na hora;
rotacionar o segredo também (claim `gen`).

## Cobrança e log

Cada chamada de `situacao_cadastral` e `consultar_empresa` gera **uma linha**
no mesmo log do REST, com `rota: mcp:<ferramenta>`, o status que o REST daria
e **o CNPJ em `ni`** — como no REST, e ao contrário do CEP no Hestia: um CNPJ
identifica uma empresa, e o registro é o que permite investigar uma
reclamação de cobrança.

| situação | status no log | faturável |
|---|---|---|
| encontrado | 200 | sim |
| CNPJ válido que não está na competência (empresa ou estabelecimento) | 404 | sim |
| dígito verificador errado, argumento inválido | 400 | não |
| base fora do ar | 500 | não |
| cota esgotada | — (sem linha, como no REST) | — |
| `validar_cnpj`, `initialize`, `tools/list`, `resources/*` | — (sem linha) | — |

## Disponibilidade

A troca mensal da base deixa o Themis **fora do ar por algumas horas** (ver
THEMIS.md no infra-olympus): com o Deployment em zero réplicas, não há quem
responda ao `/mcp`, e o cliente vê erro de conexão. As instruções do servidor
dizem ao modelo o que fazer com isso — o valor é desconhecido, e não deve ser
preenchido de memória.

## Conformidade conferida

Em 23/09/2026, contra o **cliente oficial** (SDK `mcp` 2.2.0, modo `auto`):
negociação de 2025-11-25, as três ferramentas e o recurso. `validar_cnpj` não
gerou linha de log; `situacao_cadastral` e `consultar_empresa` geraram linha
faturável com o CNPJ em `ni`.

E em produção, no mesmo dia, contra o **cliente do OpenClaw 2026.9.5** do
Cerbero, pelo endereço interno: sonda e `probe` com as três ferramentas, e um
turno real do agente que **cruzou os dois serviços** — consultou o CNPJ aqui,
tirou o CEP do endereço cadastrado e o conferiu no Hestia, concluindo que o
logradouro da Receita bate com o dos Correios. Os logs dos dois pods
registraram uma linha cada, com a credencial do agente.

## Em aberto

- **CNPJ alfanumérico.** A Receita passou a emitir CNPJ com letras a partir de
  julho de 2026, e `api/cnpj.py` só aceita dígitos. Afeta o REST e o MCP
  igualmente; é mudança própria, de algoritmo de dígito verificador e de ETL.
- **A revisão 2026-07-28** do protocolo, `outputSchema` — os mesmos pontos em
  aberto do CEP-XRay.
- **O núcleo de `mcp.py` está duplicado** entre os dois projetos, com os mesmos
  nomes e a mesma estrutura. Mudança no núcleo tem que ir para os dois.
