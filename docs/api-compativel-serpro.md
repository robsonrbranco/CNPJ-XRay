# API compatível com a Consulta CNPJ do SERPRO

Desenho de uma API que reproduz o contrato da [Consulta CNPJ do
SERPRO](https://apicenter.estaleiro.serpro.gov.br/documentacao/consulta-cnpj/pt/),
servida a partir da base local do CNPJ-XRay.

O objetivo é que um cliente escrito para a API do SERPRO funcione apontando
para esta, sem alteração de código.

## O que é compatível, e o que não pode ser

**Compatível:** o contrato — autenticação, caminhos, nomes de campo, estrutura
de aninhamento, códigos de retorno.

**Não compatível, e nenhuma implementação resolve:**

* **O CPF dos sócios vem mascarado.** A Receita publica `***794780**` nos dados
  abertos; o SERPRO, como canal autorizado, devolve o CPF completo. O campo
  existe e tem o mesmo nome, mas o conteúdo é parcial.
* **A base é um retrato mensal.** O SERPRO consulta a Receita no momento da
  chamada. Uma empresa aberta ontem não está aqui, e uma baixa da semana
  passada ainda aparece ativa.

As duas são propriedades da fonte, não do código. Precisam estar documentadas
para o consumidor: é o tipo de diferença que vira chamado de suporte meses
depois, quando alguém compara os dois resultados.

## Autenticação

OAuth2 `client_credentials`, idêntico ao do SERPRO:

```
POST /token
Authorization: Basic base64(ConsumerKey:ConsumerSecret)
Content-Type: application/x-www-form-urlencoded

grant_type=client_credentials
```

Resposta:

```json
{
  "access_token": "...",
  "token_type": "Bearer",
  "expires_in": 3600,
  "scope": "default"
}
```

E então `Authorization: Bearer <access_token>` nas consultas.

### O token é um JWT assinado

O cliente trata o token como opaco — devolve o que recebeu. Isso nos deixa
livres para emitir um JWT, e a vantagem é concreta: **validação sem estado**.
Qualquer worker do pod valida qualquer token sem consultar armazenamento
nenhum, o que importa porque no embedded cada worker é um processo isolado, sem
cache compartilhado.

Claims:

| claim | valor |
|---|---|
| `sub` | Consumer Key |
| `iat` | emissão |
| `exp` | `iat + 3600`, para casar com `expires_in` |
| `scope` | `default` |

Assinatura **HS256** com segredo lido do volume de credenciais. Chave
assimétrica só faria sentido se outro serviço precisasse validar sem poder
emitir, o que não é o caso.

**Limite conhecido:** JWT sem estado não se revoga antes de expirar. Desativar
um consumidor impede a emissão de token novo, mas o token em curso vale até uma
hora. Para esta aplicação — consulta a dado público, sem escrita — é aceitável;
se um dia não for, a saída é uma lista de revogação, que reintroduz estado.

## Endpoints

Três, como no SERPRO:

| caminho | devolve |
|---|---|
| `GET /v2/basica/{ni}` | dados cadastrais, **sem** sócios |
| `GET /v2/qsa/{ni}` | apenas o quadro de sócios e administradores |
| `GET /v2/empresa/{ni}` | o conjunto completo |

`{ni}` é o CNPJ com 14 dígitos, sem pontuação.

## Códigos de retorno

Os mesmos, com os mesmos significados:

| código | quando |
|---|---|
| 200 | consulta bem-sucedida |
| 206 | dado devolvido de forma incompleta |
| 400 | CNPJ inválido |
| 401 | falha de autenticação |
| 403 | acesso negado |
| 404 | nenhum registro para o CNPJ informado |
| 500 | erro interno |

**400 e 404 são casos diferentes e precisam ser distinguidos antes da consulta.**
CNPJ com dígito verificador errado é 400; CNPJ bem formado que não está na base
é 404. Isso exige validar o DV em código — não dá para deduzir do banco, porque
lá os dois casos são igualmente "não achei".

## De onde vem cada campo

Quase tudo tem origem direta no schema:

| campo SERPRO | origem |
|---|---|
| `ni` | `cnpj_basico ‖ cnpj_ordem ‖ cnpj_dv` |
| `tipoEstabelecimento` | `estabelecimento.identificador_matriz_filial` |
| `nomeEmpresarial` | `empresa.razao_social` |
| `nomeFantasia` | `estabelecimento.nome_fantasia` |
| `dataAbertura` | `estabelecimento.data_inicio_atividade` |
| `capitalSocial` | `empresa.capital_social` |
| `porte` | `empresa.porte_empresa` |
| `situacaoEspecial`, `dataSituacaoEspecial` | colunas homônimas |
| `correioEletronico` | `estabelecimento.correio_eletronico` |
| `situacaoCadastral{codigo,data,motivo}` | `situacao_cadastral`, `data_situacao_cadastral`, `motivo` → `motivo.descricao` |
| `naturezaJuridica{codigo,descricao}` | `empresa.natureza_juridica` → `natureza` |
| `cnaePrincipal{codigo,descricao}` | `cnae_fiscal_principal` → `cnae` |
| `cnaeSecundarias[]` | `cnae_fiscal_secundaria` → `cnae` |
| `endereco.*` | `tipo_logradouro`, `logradouro`, `numero`, `complemento`, `cep`, `bairro`, `uf` |
| `endereco.municipio`, `endereco.pais` | `municipio` → `municipio`, `pais` → `pais` |
| `telefone[]{ddd,numero}` | `ddd_1`/`telefone_1`, `ddd_2`/`telefone_2` |
| `informacoesAdicionais.optanteSimples` | `simples.opcao_pelo_simples` |
| `informacoesAdicionais.optanteMei` | `simples.opcao_mei` |
| `socios[]` | tabela `socios`, incluindo `representanteLegal` |

Toda junção com tabela de domínio é **`LEFT JOIN`**: a base não tem FOREIGN KEY
de propósito e há código órfão real — 18.253 em `motivo_situacao_cadastral`,
por exemplo. `INNER JOIN` faria o estabelecimento sumir da resposta por causa
de um código que a Receita publicou errado.

### Três ajustes de forma

* **`capitalSocial` é inteiro em centavos** no SERPRO; aqui é `NUMERIC(18,2)`.
  Multiplicar por 100, sem passar por float.
* **`listaPeriodosSimples[]`** — a Receita publica **um** par opção/exclusão; o
  SERPRO devolve o histórico. Emitimos lista de um elemento, e isso vai
  documentado.
* **`endereco.municipioJurisdicao`** — não existe nos dados abertos. É o
  município de jurisdição fiscal, diferente do município do endereço.

## Credenciais em volume separado

**Não podem morar na base CNPJ**: ela é `read-only` no header e é apagada e
substituída todo mês. Qualquer consumidor cadastrado sumiria na virada.

Ficam num volume próprio, com o ciclo de vida desacoplado da competência:

```
/creds/credenciais.fdb     consumer key, hash do secret, ativo
/creds/jwt.key             segredo de assinatura HS256
```

Usar um `.fdb` pequeno reaproveita o engine que a imagem já carrega — nenhuma
dependência nova.

**O secret nunca é guardado em claro**, só o hash. A validação compara o hash
do que chegou; um vazamento do volume não entrega as credenciais dos
consumidores.

### O volume de credenciais também pode ser read-only

Se o cadastro de consumidores for provisionado fora de banda — você edita e
reinicia o pod — **o pod inteiro continua imutável**, sem nenhuma superfície
gravável. Só passa a precisar de escrita se houver autoatendimento (consumidor
se cadastra sozinho, rotaciona o próprio secret).

Começar read-only e abrir depois, se preciso, é mais fácil que o contrário.

## Como isso muda o pod

| | |
|---|---|
| volume de dados | `/data`, read-only, trocado todo mês |
| volume de credenciais | `/creds`, read-only, ciclo próprio |
| `ServerMode` | `Classic` — obrigatório com vários workers |
| porta exposta | só a da API; o Firebird continua sem listener |

A API é casca fina sobre `src/consulta/empresa.py`, que já devolve a ficha
inteira como dicionário. O trabalho novo é a tradução para os nomes do SERPRO,
a validação de DV e a camada de token.

## Em aberto

* **Framework web** e se a API mora neste repositório ou noutro. Trazê-la para
  cá acrescenta dependência web a um projeto que hoje é só ETL e consulta.
* **Quota por consumidor.** O SERPRO cobra por transação e não documenta código
  de rate limit. Se houver limite aqui, ele precisa de contador — e contador é
  estado gravável, o que reabre a questão do volume read-only.
* **Gateway de referência.** O SERPRO tem dois caminhos com prefixos
  diferentes: `gateway.apiserpro.serpro.gov.br/consulta-cnpj-df-trial/v2/` e
  `apigateway.conectagov.estaleiro.serpro.gov.br/api-cnpj-{basica,qsa,empresa}/v2/`.
  Os recursos são os mesmos; o prefixo a imitar depende de qual cliente se quer
  atender sem alteração.
