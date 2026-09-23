# 📝 Changelog - CNPJ-XRay

> **Aviso de datação.** As entradas **2.2.0 e anteriores** descrevem o projeto
> quando ele rodava sobre **PostgreSQL** e era invocado com **uv**. Os comandos
> citados nelas — `uv run src/etl/ETL_dados_publicos_empresas.py` e afins — não
> funcionam mais: o script foi removido na 3.0.0 e o uv foi abandonado. Elas
> ficam como estão de propósito, porque registram o que era verdade naquela
> data; corrigi-las para a sintaxe atual produziria comandos igualmente
> quebrados e um registro falso. Para instruções válidas, ver o
> [README](README.md) e o [CONFIGURACAO_INICIAL.md](CONFIGURACAO_INICIAL.md).

## [3.2.0] - 2026-09-23

### ✨ Novo

- **Camada MCP para agentes, em `POST /mcp`.** Porte do piloto validado no
  CEP-XRay (Hestia 0.3.0). Três ferramentas — `validar_cnpj`,
  `situacao_cadastral` e `consultar_empresa` — e o recurso
  `themis://competencia`, com resposta enxuta: capital social em reais exato
  (texto, sem float), domínios reduzidos à descrição onde ela basta, e sem os
  campos que o contrato do SERPRO manda sempre nulos. Detalhes em `docs/mcp.md`.

- **Token próprio do MCP, emitido pelo `/manager`.** Novo
  `POST /manager/credenciais/{key}/token-mcp`, de 1 a 365 dias, com `aud` e
  `gen`. Cada porta recusa o token da outra, e um token emitido para o Hestia
  não vale no Themis (audiência diferente). Revogar ou rotacionar derruba o
  token de longa duração na hora.

### 🔧 Decisões registradas

- **Sócio é dado pessoal, e não vai por padrão.** `consultar_empresa` só
  devolve o quadro societário com `incluir_socios: true`.
- **`validar_cnpj` não é cobrado nem consome cota**: confere o dígito
  verificador sem encostar na base, então não é consulta.
- **O CNPJ vai para o log, como no REST** — ao contrário do CEP no Hestia: um
  CNPJ identifica uma empresa, e o registro é o que permite investigar uma
  reclamação de cobrança.
- **`token.py`, `credenciais.py` e `manager.py` voltam a ser idênticos aos do
  CEP-XRay** (o manager, exceto o título). O núcleo de `mcp.py` também é o
  mesmo; mudança nele tem que ir para os dois projetos.

### 📊 Resultado

- 246 testes (31 novos). Quatro proteções sabotadas uma a uma — audiência no
  MCP, audiência no REST, geração do segredo e o padrão `incluir_socios=false`
  — e cada sabotagem reprovou o teste que a protege.
- Conformidade conferida contra o cliente oficial (SDK `mcp` 2.2.0):
  negociação de 2025-11-25, as três ferramentas e o recurso; `validar_cnpj` sem
  linha de log, as consultas com linha faturável e o `ni`.

## [3.1.0] - 2026-09-21

A 3.0.0 entregou a base. Esta entrega o **serviço**: o Themis no ar em
`themis.ecomciencia.com`, servindo 222.197.006 linhas da competência 2026-09.

> **Nota de origem.** As entradas abaixo cobrem 16 entregas (#11 a #26) que
> ficaram sem registro entre 18 e 21 de setembro, mais duas de 21/09. As
> primeiras foram reconstruídas a partir do histórico de commits e dos
> documentos do `infra-olympus`; os números foram conferidos contra o serviço
> em execução onde era possível. Se alguma atribuição estiver torta, o commit
> correspondente manda.

### ✨ Novo

- **API pública compatível com a Consulta CNPJ v2 do SERPRO** (#13): `POST
  /token` e os três recortes — `/v2/basica/{ni}`, `/v2/qsa/{ni}`,
  `/v2/empresa/{ni}`. Mesmos caminhos, mesmos nomes de campo, mesmos códigos.
- **`/manager` em processo e porta separados** (#12, #17), não expostos. Uma
  credencial capaz de criar credenciais seria escalada de privilégio, e não
  publicar a porta é a única barreira que não depende de nenhum segredo estar
  certo. Credenciais em SQLite com PBKDF2, log de uso em duas camadas e
  estatísticas.
- **Imagem do pod: Firebird 3.0 embedded** (#14). Sem servidor, sem porta 3050,
  sem `security3.fdb`, sem credencial de banco. `ServerMode = Classic`, porque
  em `Super` o primeiro worker toma o arquivo e os demais morrem no attach.
- **A base carrega a própria proveniência** (#18), numa tabela `metadados`
  dentro do `.fdb`, e a API a expõe em `GET /saude`. Sem isso, quem recebe uma
  resposta não tem como saber de que competência é o dado.
- **OpenAPI que descreve o contrato** (#16, #17), e não apenas lista as rotas.
- **Publicação da base em partes comprimidas, paralelas e retomáveis** (#20).
- **CI/CD no cluster Olympus** (#19, #26): testes, build, push para o ghcr.io,
  `set image` nos dois containers do pod e conferência do `/saude`.
- **Página de apresentação em `/`** (#25), compilada com Hugo e servida pela
  própria aplicação — versionar as duas juntas é o que impede a página de
  descrever uma API que já mudou.

### ⚡ Decisões medidas

- **Conexão viva por worker** (#15): a consulta caiu de ~1000 ms para **12 ms**
  de mediana. Abrir attachment em embedded custa ~530 ms e a consulta indexada
  custa fração de milissegundo — pagar o attachment por requisição era o
  gargalo inteiro.
- **A imagem caiu 42%: 140 MB → 81 MB** (21/09). O pod carregava o ETL e nunca
  o roda. Saíram `polars` (181 MB), `rich` + `pygments` (12 MB) e `pip`
  (12 MB); `site-packages` foi de 252 MB para 46 MB, e o pull de 17,3 s para
  4,9 s.

  Exigiu mudar **código antes do Dockerfile**, e é isso que a torna
  interessante: `src/db/__init__.py` reexportava `carregar`/`normalizar` de
  `.loader`, que importa `polars`. O `__init__` roda no primeiro import de
  QUALQUER submódulo, então um inocente `from ..db import connection` — que é
  o que `api/conexao.py` e `consulta/empresa.py` fazem — arrastava os 181 MB.
  Os re-exports não serviam a ninguém: varredura em `src/` e `tests/` não achou
  **um só** uso dos 17 nomes. O `rich` entrava por outro caminho, o import no
  topo de `consulta/empresa.py`, e virou preguiçoso.

- **A credencial do CI deixou de ser cluster-admin** (21/09). O
  `KUBECONFIG_OLYMPUS` era o kubeconfig do k3s — `system:admin` em
  `system:masters`, poder total sobre os sete serviços, guardado no secret de
  um repositório **público**, e baseado em certificado X509 que o k3s não
  revoga individualmente. No lugar, uma ServiceAccount que escreve **apenas no
  `themis`**, por `resourceName`. Não houve mudança neste repositório: é o
  valor do secret, e o manifest está em `k8s/themis-deployer-rbac.yaml` no
  `infra-olympus`.

### 🐛 Correções que custaram caro

- **As partes da publicação não cabiam junto com a base antiga** (#23): a
  ordem passou a apagar a base **antes** de transmitir. Com 34,2 GB contra a
  folga do node, transmitir primeiro pode encher o disco — e disco cheio num
  k3s não derruba só o Themis, gera `DiskPressure` e despeja os vizinhos.
- **A confirmação do `/saude` batia num 403 do Cloudflare e culpava o
  serviço** (#24). O `Python-urllib` é bloqueado; qualquer User-Agent nomeado
  passa.
- **`set image` tocava só um container** (#26). O `manager` ficava preso na tag
  do manifest enquanto a API avançava, e os dois passariam a ler o mesmo
  SQLite com códigos de versões diferentes — divergência que só apareceria na
  gestão de credencial, meses depois.
- **A consulta de fumaça do pod morria com `ModuleNotFoundError`** (21/09),
  consequência do enxugamento da imagem. O passo 6 de
  `docs/publicacao-pod.md` manda rodar `python -m src.consulta.empresa` dentro
  do pod, e sem o `rich` isso quebra — no meio da publicação mensal, que é o
  pior momento para um traceback que manda procurar no lugar errado. O doc
  passou a usar `--json`, e a CLI explica em uma linha quando alguém esquece.

  Nenhum teste de rota pegaria: a API nunca chama `exibir()`. Apareceu ao
  rodar o procedimento documentado no pod de verdade.

### 🔧 Arrumação

- **A versão do pacote passou a acompanhar o CHANGELOG.** O `pyproject.toml`
  ficou em `0.1.0` desde o primeiro commit enquanto este arquivo já registrava
  quatro versões: duas fontes discordando sobre a mesma coisa, e a que um
  `pip install` lê era a errada. Não é cosmético — `0.x` afirma "pré-1.0, sem
  compromisso de compatibilidade" sobre um projeto com API pública em produção
  e uma quebra já documentada (a 3.0.0). Quem pinasse por versão estaria
  pinando uma afirmação falsa.

  Entrou **nesta** entrada e não numa 3.1.1 porque uma 3.1.1 recriaria na hora
  a divergência que ela corrige: não há tag `v3.1.0` publicada, então o topo do
  CHANGELOG e o `version=` do pacote descrevem o mesmo estado.

  Os `version=` de `src/api/publica.py` (2.0) e `src/api/manager.py` (1.0)
  **não** mudaram. Aqueles versionam o contrato HTTP no OpenAPI, que tem ciclo
  próprio; alinhá-los ao pacote faria a especificação anunciar uma versão de
  API que nunca existiu.

### 📊 Resultado

**215 testes**, sem Firebird e sem rede. Serviço no ar, 2/2, com pipeline
verde de ponta a ponta e imagem de 81 MB.

---

## [3.0.0] - 2026-09-18

Reescrita do projeto sobre **Firebird 3.0**. É uma mudança incompatível em
tudo: banco, forma de carregar, forma de consultar e forma de instalar.

### 💥 Incompatível

- **PostgreSQL saiu inteiro.** Removidas 7.561 linhas: o ETL
  (`ETL_dados_publicos_empresas.py`), `run_prod.py`, `resume_etl.py`,
  `create_indexes.py`, `check_database_status.py`,
  `check_database_structure.py`, `dump_and_restore.py`,
  `sql_dump_generator.py` e os `.sql` de DDL e tuning. Cada um tem substituto
  no caminho Firebird.
- **uv abandonado.** O `uv.lock` estava congelado em agosto e travava o
  backport `pathlib` do PyPI, que sombreia o módulo da stdlib. Instalação
  passa a ser venv + pip.
- **`requires-python`** de `==3.13.*` para `>=3.13` — a restrição antiga
  excluía o 3.14, que é o interpretador em uso.

### ✨ Novo

- **Camada Firebird** (`src/db/`): schema como fonte da verdade, carga em
  massa com statement reaproveitado, remoção de chave repetida via
  `RDB$DB_KEY` e contorno para a Services API do driver, que falha neste
  ambiente.
- **Download multipart** (`src/etl/download.py`): faixas `Range` de 64 MB com
  fila de pedaços global, retomável, e espera pela origem quando o servidor da
  Receita cai — o que ele fez cerca de 595 vezes em 15 horas na competência
  2026-09. Substitui o download que travava para sempre num socket abandonado.
- **Pipeline de 11 fases** (`src/etl/pipeline.py`) com troca blue-green por
  renomeação de arquivo, validação antes de promover e produção em read-only
  no header.
- **Consulta de empresa** (`src/consulta/empresa.py`), com `--json`.
- **Relatório de qualidade** (`src/validation/qualidade.py`) e a
  [linha de base da 2026-09](docs/qualidade-linha-de-base.md).
- **Primeiros testes do projeto**: 23, sem banco e sem rede, cobrindo o
  caminho de erro da troca blue-green e as sutilezas da leitura dos CSV.

### ⚡ Decisões medidas

- **WIN1252 + WIN_PTBR** em vez de UTF8: base 27% menor, e comparação sem
  acento e sem caixa.
- **Sem reserva de espaço nas páginas** (`USE_FULL`), aplicado na criação:
  −12,4% de tamanho, com 1,86 milhão de linhas a mais.
- **Paralelismo por processo, não thread** — as threads disputam o GIL. 4,7x.
- **Carga sem nenhuma trava.** Sem PRIMARY KEY, NOT NULL ou FOREIGN KEY: a
  fonte traz chave repetida e código órfão reais, e uma constraint abortaria a
  carga. A conferência virou relatório, medindo o estrago em vez de parar por
  causa dele.
- **`page_size` não afeta o tamanho final** — 1,4 MB de diferença em 190 MB
  entre 4K, 8K e 16K. Mantido 16384 pelo outro critério.
- **Índices de `uf` e `capital_social`**: ~80x na maioria das UFs por 439,9 MB.

### 🐛 Correções que custaram caro

- **Encoding é cp1252, não latin-1.** Os dois divergem em 0x80–0x9F; ler como
  latin-1 e gravar em WIN1252 matou uma carga de 40 minutos com
  `UnicodeEncodeError`. Os 5 bytes que cp1252 não define viram `?`.
- **`complemento` tem `;` dentro de aspas** — o corte de bloco passou a
  respeitar paridade de aspas.
- **A configuração criava a base errada**: o `docker-compose.yml` criava o
  banco em UTF8 e sem collation, e o `.env.example` dizia `DB_CHARSET=UTF8`.
  Quem seguisse o exemplo construía uma base 27% maior com ordenação errada.

### 📊 Resultado

Base 2026-09 em produção: **222.197.006 linhas em 34,2 GB**, read-only,
construída em 5,9 h. Inconsistência de 0,0103%, toda vinda da fonte.

---

## [2.2.0] - 2026-01-29

### ✨ Novas Funcionalidades

#### Suporte a Argumentos de Linha de Comando
- **Modo Automático (`--last`)**: Detecta e baixa automaticamente a versão mais recente disponível na Receita Federal
- **Modo Específico (`MM-AAAA`)**: Permite especificar uma versão exata via linha de comando
- **Modo Interativo (padrão)**: Mantém compatibilidade com o comportamento original

#### Detecção Automática de Versão
- Implementada função `get_latest_available_date()` que faz scraping da página da Receita Federal
- Identifica automaticamente a versão mais recente disponível
- Fallback inteligente para data atual em caso de erro

#### Validação Robusta de Entrada
- Parser `parse_date_string()` com validação de formato `MM-AAAA`
- Validação de intervalos de ano (2019 até ano atual + 1)
- Validação de mês (01-12)
- Mensagens de erro claras e informativas

### 📚 Documentação

#### Novos Arquivos
- **EXEMPLOS_USO.md**: Guia completo com exemplos práticos de uso
  - Demonstrações de todos os modos de execução
  - Casos de uso: Cron, CI/CD, análise comparativa
  - Scripts de exemplo em Shell e Python
  - Troubleshooting de erros comuns

#### Atualizações
- **README.md**: Adicionada seção "Modos de Execução do ETL"
- **CLAUDE.md**: Documentação técnica atualizada com:
  - Novos modos de operação
  - Comandos de execução atualizados
  - Casos de uso práticos
  - Versão atualizada para v2.2

### 🔧 Melhorias Técnicas

#### Arquitetura
- Adicionado módulo `argparse` para parsing de argumentos CLI
- Função `get_year_month()` refatorada para suportar múltiplos modos
- Separação clara de responsabilidades entre funções

#### Automação
- Suporte completo a execução não-interativa
- Compatível com scripts, cron jobs e pipelines CI/CD
- Código testável e reutilizável

### 🎯 Casos de Uso Suportados

1. **Automação**: Scripts e cron jobs podem usar `--last` para sempre obter dados atualizados
2. **CI/CD**: Integração fácil em pipelines de GitHub Actions, GitLab CI, etc.
3. **Análise Histórica**: Download de versões específicas para comparações temporais
4. **Desenvolvimento**: Modo interativo mantido para uso manual e testes

### 💡 Exemplos de Uso

```bash
# Modo interativo (padrão)
uv run src/etl/ETL_dados_publicos_empresas.py

# Baixar versão mais recente automaticamente
uv run src/etl/ETL_dados_publicos_empresas.py --last

# Baixar versão específica
uv run src/etl/ETL_dados_publicos_empresas.py 01-2025
uv run src/etl/ETL_dados_publicos_empresas.py 12-2024

# Ver ajuda
uv run src/etl/ETL_dados_publicos_empresas.py --help
```

### ⚙️ Alterações no Código

#### Arquivo: `src/etl/ETL_dados_publicos_empresas.py`

**Novas Funções:**
- `parse_arguments()`: Parseia argumentos de linha de comando
- `get_latest_available_date()`: Detecta versão mais recente via scraping
- `parse_date_string(date_str)`: Valida e parseia formato MM-AAAA

**Modificações:**
- `get_year_month(args=None)`: Refatorada para suportar argumentos CLI
- Adicionada lógica para escolher modo de operação baseado em argumentos

**Imports:**
- `import argparse`: Adicionado para parsing de argumentos

### 🔄 Compatibilidade

- ✅ **Retrocompatível**: Modo interativo funciona exatamente como antes
- ✅ **Python 3.8+**: Compatível com versões modernas do Python
- ✅ **Dependências**: Nenhuma dependência adicional necessária

### 📦 Migração

Não é necessária nenhuma ação para migração. O comportamento padrão (sem argumentos) permanece inalterado.

Usuários que desejam usar os novos recursos podem simplesmente adicionar os argumentos CLI:
- Adicionar `--last` para automação
- Passar `MM-AAAA` para versões específicas

---

## [2.1.0] - 2025-08-15

### ✨ Funcionalidades Anteriores

- Sistema de seleção dinâmica de data (interativo)
- Tratamento robusto de conexões SSL/TLS
- Sistema de recuperação de falhas
- Configurações de ambiente via .env
- Função `check_diff()` com SSL robusto

---

**Versionamento**: Seguimos [Semantic Versioning](https://semver.org/)
- **MAJOR**: Mudanças incompatíveis na API
- **MINOR**: Novas funcionalidades compatíveis
- **PATCH**: Correções de bugs compatíveis
