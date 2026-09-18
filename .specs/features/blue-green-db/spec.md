# Blue-Green Database — Specification

> **Concluído e superado.** Esta especificação foi escrita para o projeto sobre
> **PostgreSQL**, e descreve a troca blue-green como `ALTER DATABASE ... RENAME`
> entre os bancos `receita_federal` e `receita_federal_staging`. A feature foi
> entregue, mas o projeto migrou para Firebird 3.0 na versão 3.0.0 e a troca
> passou a ser **renomeação de arquivo** — ver `src/blue_green/switch.py`.
>
> Os comandos citados aqui (`uv run`, `ETL_dados_publicos_empresas.py`) não
> funcionam mais. O documento fica como registro do desenho original, não como
> instrução. Para o comportamento atual, ver o código e seus testes em
> `tests/test_switch.py`.

## Problem Statement

O ETL sobrescreve o banco `receita_federal` em produção enquanto carrega dados novos, deixando
o banco indisponível ou inconsistente durante o processo (que pode durar horas). Além disso,
não há como validar os dados novos antes de substituir os dados ativos. O objetivo é permitir
carregar, validar e promover uma nova carga sem interromper o banco ativo.

## Goals

- [ ] ETL sempre carrega em `receita_federal_staging`, nunca no banco ativo
- [ ] Estado (qual banco está ativo, mês da fonte, datas de download e processamento) persiste em arquivo JSON
- [ ] Validação da staging é executada antes de qualquer switch
- [ ] Switch (rename + update estado) só ocorre com validação aprovada
- [ ] Fluxo funciona igual em postgres nativo (prod) e docker (teste) — mesma base de código

## Out of Scope

| Feature | Reason |
|---------|--------|
| Gerenciamento de containers docker | Fora do escopo conforme decisão do usuário |
| Rollback automático após switch | Banco renomeado fica disponível como `receita_federal_old`; rollback é manual |
| Multi-tenant / schemas por versão | Abordagem rename de banco é suficiente |
| Health check contínuo | ETL é batch, não serviço contínuo |

---

## User Stories

### P1: ETL carrega em staging ⭐ MVP

**User Story**: Como operador de dados, quero que o ETL carregue sempre em
`receita_federal_staging` para que `receita_federal` (ativo) nunca seja tocado durante a carga.

**Why P1**: Sem isso, prod fica inconsistente durante horas de processamento.

**Acceptance Criteria**:

1. WHEN o ETL inicia THEN system SHALL criar `receita_federal_staging` (drop + create se já existir)
2. WHEN o ETL conclui THEN system SHALL gravar no state file: `source_month`, `downloaded_at`, `processed_at` para o slot staging
3. WHEN `receita_federal` existe THEN system SHALL nunca executar DDL/DML nele durante a carga
4. WHEN o ETL é executado com `--target staging` ou sem flag THEN system SHALL usar `receita_federal_staging` como destino

**Independent Test**: Executar o ETL e confirmar que `receita_federal` não foi alterado; `receita_federal_staging` tem os dados carregados; state file tem as datas corretas.

---

### P1: State file com metadados completos ⭐ MVP

**User Story**: Como operador, quero um arquivo `blue_green_state.json` que registre qual banco
está ativo e os metadados de cada slot (mês dos dados, data de download, data de processamento)
para rastrear o histórico de cargas.

**Why P1**: Sem estado persistido não há como saber qual banco está ativo nem qual versão dos dados ele contém.

**Acceptance Criteria**:

1. WHEN o ETL inicia o download THEN system SHALL gravar `staging.downloaded_at` no state file
2. WHEN o ETL conclui a carga THEN system SHALL gravar `staging.processed_at` e `staging.source_month` no state file
3. WHEN o switch é executado THEN system SHALL mover staging → active no state file e limpar o slot staging
4. WHEN o state file não existe THEN system SHALL criá-lo com estado inicial vazio

**State file structure**:
```json
{
  "active": {
    "database": "receita_federal",
    "source_month": "2025-01",
    "downloaded_at": "2025-01-15T10:30:00",
    "processed_at": "2025-01-15T14:45:00",
    "switched_at": "2025-01-15T15:00:00"
  },
  "staging": {
    "database": "receita_federal_staging",
    "source_month": "2025-02",
    "downloaded_at": "2025-02-10T09:00:00",
    "processed_at": null
  },
  "last_switch": "2025-01-15T15:00:00"
}
```

**Independent Test**: Ler `blue_green_state.json` após ETL e switch e verificar que todos os campos estão preenchidos corretamente.

---

### P1: Validação da staging antes do switch ⭐ MVP

**User Story**: Como operador, quero validar que `receita_federal_staging` está íntegro (tabelas
corretas, registros dentro do esperado, índices existentes) antes de promovê-lo para ativo.

**Why P1**: Switch sem validação derruba prod se a carga falhou silenciosamente.

**Acceptance Criteria**:

1. WHEN a validação é executada THEN system SHALL verificar que todas as 10 tabelas esperadas existem
2. WHEN a validação é executada THEN system SHALL verificar que cada tabela tem pelo menos 1 registro
3. WHEN a validação é executada THEN system SHALL verificar que os 7 índices esperados existem
4. WHEN todas as verificações passam THEN system SHALL retornar status `VALID` e imprimir resumo
5. WHEN qualquer verificação falha THEN system SHALL retornar status `INVALID`, listar falhas, e bloquear o switch
6. WHEN `receita_federal_staging` não existe THEN system SHALL retornar status `INVALID` com mensagem clara

**Independent Test**: Criar uma staging incompleta (sem alguns índices) e executar validação — deve retornar `INVALID`; criar staging completa — deve retornar `VALID`.

---

### P1: Switch (rename + update state) ⭐ MVP

**User Story**: Como operador, quero executar um comando de switch que renomeia os bancos e
atualiza o state file, promovendo staging para ativo após validação aprovada.

**Why P1**: É o objetivo central da feature — trocar banco sem downtime para o banco ativo.

**Acceptance Criteria**:

1. WHEN o switch é invocado THEN system SHALL executar validação primeiro; se `INVALID`, abortar com erro
2. WHEN validação passa THEN system SHALL terminar todas as conexões ativas em `receita_federal`
3. WHEN conexões encerradas THEN system SHALL executar `ALTER DATABASE receita_federal RENAME TO receita_federal_old`
4. WHEN renomeado THEN system SHALL executar `ALTER DATABASE receita_federal_staging RENAME TO receita_federal`
5. WHEN ambos renames concluídos THEN system SHALL atualizar state file: staging → active, `switched_at` = agora
6. WHEN switch completo THEN system SHALL imprimir resumo: banco ativo, source_month, switched_at
7. WHEN `--force` não passado e validação falha THEN system SHALL nunca executar rename
8. WHEN `receita_federal_old` já existe THEN system SHALL perguntar ou usar `--drop-old` para dropar antes

**Independent Test**: Executar switch após carga válida; verificar que `receita_federal` contém os dados novos e state file foi atualizado.

---

### P2: CLI unificado com subcomandos

**User Story**: Como operador, quero um script único `blue_green.py` com subcomandos para
gerenciar todo o ciclo blue-green de forma explícita.

**Why P2**: Facilita automação (cron/CI) e documentação do fluxo.

**Acceptance Criteria**:

1. WHEN `python blue_green.py status` é executado THEN system SHALL exibir estado atual (ativo, staging, datas)
2. WHEN `python blue_green.py validate` é executado THEN system SHALL executar e exibir resultado da validação da staging
3. WHEN `python blue_green.py switch` é executado THEN system SHALL executar validação + switch
4. WHEN `python blue_green.py switch --force` é executado THEN system SHALL pular validação e forçar switch
5. WHEN `python blue_green.py cleanup` é executado THEN system SHALL dropar `receita_federal_old` se existir

**Independent Test**: Executar cada subcomando e verificar output e efeito esperado.

---

### P3: ETL com flag `--db-target` explícito

**User Story**: Como operador avançado, quero poder apontar o ETL para qualquer banco específico
via `--db-target <nome>` para casos de manutenção ou carga manual.

**Why P3**: Flexibilidade para cargas fora do fluxo normal; não é necessário para o MVP.

**Acceptance Criteria**:

1. WHEN `--db-target receita_federal_staging` é passado THEN system SHALL usar esse banco como destino
2. WHEN `--db-target` não é passado THEN system SHALL usar `receita_federal_staging` como padrão

---

## Edge Cases

- WHEN switch é executado mas `receita_federal_staging` não existe THEN system SHALL abortar com mensagem clara
- WHEN rename falha no meio (e.g., permissão) THEN system SHALL reportar estado inconsistente e não atualizar state file
- WHEN state file é inválido/corrompido THEN system SHALL recriar com estado vazio e avisar
- WHEN `source_month` do staging já é igual ao active THEN system SHALL avisar que é a mesma versão dos dados
- WHEN postgres não tem permissão para ALTER DATABASE RENAME THEN system SHALL informar superuser necessário

---

## Requirement Traceability

| Requirement ID | Story | Phase | Status |
|----------------|-------|-------|--------|
| BG-01 | P1: ETL carrega em staging | Design | Pending |
| BG-02 | P1: ETL não toca banco ativo | Design | Pending |
| BG-03 | P1: State file — downloaded_at | Design | Pending |
| BG-04 | P1: State file — processed_at + source_month | Design | Pending |
| BG-05 | P1: State file — update no switch | Design | Pending |
| BG-06 | P1: Validação — tabelas | Design | Pending |
| BG-07 | P1: Validação — registros > 0 | Design | Pending |
| BG-08 | P1: Validação — índices | Design | Pending |
| BG-09 | P1: Validação — bloqueia switch se INVALID | Design | Pending |
| BG-10 | P1: Switch — encerra conexões | Design | Pending |
| BG-11 | P1: Switch — rename receita_federal → old | Design | Pending |
| BG-12 | P1: Switch — rename staging → receita_federal | Design | Pending |
| BG-13 | P1: Switch — update state file | Design | Pending |
| BG-14 | P2: CLI status | - | Pending |
| BG-15 | P2: CLI validate | - | Pending |
| BG-16 | P2: CLI switch | - | Pending |
| BG-17 | P3: --db-target flag | - | Pending |

**Coverage:** 17 total, 0 mapped to tasks yet

---

## Success Criteria

- [ ] ETL pode ser executado sem afetar o banco `receita_federal` ativo
- [ ] Operador consegue validar staging e ver resultado detalhado antes de qualquer switch
- [ ] Switch completo (rename + state update) executa em < 30s para bancos com dados completos
- [ ] State file sempre reflete a realidade atual com datas corretas
- [ ] Zero downtime para consultas em `receita_federal` durante a carga da staging
