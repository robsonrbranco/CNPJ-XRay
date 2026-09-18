# ✅ Validação - Verificação de Integridade dos Dados

Scripts para validar e verificar a integridade dos dados da Receita Federal.

## 📋 Arquivos

### 🔍 `check_database_status.py`
**Script principal de validação do banco de dados**

**Funcionalidades:**
- Verifica existência de todas as tabelas
- Conta registros por tabela
- Calcula tamanho do banco e tabelas
- Verifica integridade referencial
- Analisa cobertura de dados (CNAEs, etc.)
- Relatório completo de status

**Uso:**
```bash
# Verificar status completo
python src/validation/check_database_status.py

# Com ambiente virtual
python -m src.validation.qualidade
```

## 📊 Validações Executadas

### 1. **Estrutura do Banco**
- ✅ Verificação de tabelas existentes
- ✅ Contagem de registros por tabela
- ✅ Tamanho de cada tabela
- ✅ Índices criados

### 2. **Integridade dos Dados**
- ✅ Registros duplicados
- ✅ Valores nulos críticos
- ✅ Integridade referencial
- ✅ Consistência de CNPJs

### 3. **Cobertura de Dados**
- ✅ CNAEs principais (100% cobertura)
- ✅ CNAEs secundários (46.16% cobertura)
- ✅ Dados de sócios
- ✅ Informações do Simples Nacional

### 4. **Qualidade dos Dados**
- ✅ Formatação de campos
- ✅ Consistência de códigos
- ✅ Completude de informações

## 📈 Relatório de Status

### Exemplo de Saída:
```
===== STATUS DO BANCO DE DADOS =====

📊 Tamanho total do banco: 32 GB

📋 STATUS DAS TABELAS
┌─────────────────┬─────────┬────────────┬─────────┐
│ Tabela          │ Existe  │ Registros  │ Tamanho │
├─────────────────┼─────────┼────────────┼─────────┤
│ empresa         │ ✅ Sim  │ 63,235,730 │ 7.7 GB  │
│ estabelecimento │ ✅ Sim  │ 66,349,375 │ 17 GB   │
│ socios          │ ✅ Sim  │ 25,938,492 │ 3.6 GB  │
│ simples         │ ✅ Sim  │ 43,865,689 │ 3.6 GB  │
│ cnae            │ ✅ Sim  │ 1,359      │ 216 kB  │
│ municipio       │ ✅ Sim  │ 5,572      │ 448 kB  │
│ natureza        │ ✅ Sim  │ 90         │ 32 kB   │
│ qualificacao    │ ✅ Sim  │ 68         │ 32 kB   │
│ motivo          │ ✅ Sim  │ 63         │ 32 kB   │
│ pais            │ ✅ Sim  │ 255        │ 64 kB   │
└─────────────────┴─────────┴────────────┴─────────┘

📈 Total de registros: 199,396,693
📊 Tabelas existentes: 10/10

🔍 STATUS DOS ÍNDICES
┌─────────────────────────────┬─────────┐
│ Índice                      │ Existe  │
├─────────────────────────────┼─────────┤
│ empresa_cnpj               │ ✅ Sim  │
│ estabelecimento_cnpj       │ ✅ Sim  │
│ socios_cnpj               │ ✅ Sim  │
│ simples_cnpj              │ ✅ Sim  │
└─────────────────────────────┴─────────┘

💡 RECOMENDAÇÕES
🔹 Processo ETL foi completado com sucesso!
🔹 Todos os dados foram inseridos no banco
🔹 Índices foram criados com sucesso
```

## 🔧 Validações Detalhadas

### 1. **Consistência de CNPJs**
```sql
-- Verificar CNPJs únicos
SELECT COUNT(DISTINCT cnpj_basico) as empresas_unicas FROM empresa;

-- Verificar estabelecimentos sem empresa
SELECT COUNT(*) FROM estabelecimento e 
LEFT JOIN empresa emp ON e.cnpj_basico = emp.cnpj_basico 
WHERE emp.cnpj_basico IS NULL;
```

### 2. **Integridade Referencial**
```sql
-- Verificar CNAEs inválidos
SELECT COUNT(*) FROM estabelecimento e
LEFT JOIN cnae c ON e.cnae_fiscal_principal = c.codigo
WHERE e.cnae_fiscal_principal IS NOT NULL 
AND c.codigo IS NULL;

-- Verificar municípios inválidos
SELECT COUNT(*) FROM estabelecimento e
LEFT JOIN municipio m ON e.municipio = m.codigo
WHERE e.municipio IS NOT NULL 
AND m.codigo IS NULL;
```

### 3. **Qualidade dos Dados**
```sql
-- Verificar emails inválidos
SELECT COUNT(*) FROM estabelecimento 
WHERE correio_eletronico IS NOT NULL 
AND correio_eletronico NOT LIKE '%@%';

-- Verificar telefones inválidos
SELECT COUNT(*) FROM estabelecimento 
WHERE telefone_1 IS NOT NULL 
AND LENGTH(telefone_1) < 8;
```

## 📋 Checklist de Validação

### ✅ **Pré-ETL**
- [ ] Conexão com banco funcionando
- [ ] Espaço em disco suficiente (>50GB)
- [ ] Memória disponível (>8GB)
- [ ] Firebird rodando e `FB_CLIENT_LIBRARY` apontando para o `fbclient`

### ✅ **Pós-ETL**
- [ ] Todas as tabelas criadas
- [ ] Contagem de registros consistente
- [ ] Índices criados
- [ ] Sem erros críticos nos logs

### ✅ **Validação de Dados**
- [ ] CNPJs únicos e válidos
- [ ] Integridade referencial
- [ ] Cobertura de CNAEs adequada
- [ ] Dados de contato válidos

### ✅ **Performance**
- [ ] Consultas executando rapidamente
- [ ] Índices sendo utilizados
- [ ] Estatísticas atualizadas
- [ ] Sem bloqueios de tabela

## 🚨 Alertas e Problemas

### Problemas Críticos:
- ❌ **Tabelas faltando**: ETL incompleto
- ❌ **Registros zerados**: Falha na importação
- ❌ **Índices ausentes**: Performance comprometida

### Alertas:
- ⚠️ **Cobertura CNAE baixa**: Dados incompletos
- ⚠️ **Registros duplicados**: Possível reprocessamento
- ⚠️ **Tamanho anormal**: Verificar consistência

## 📈 Métricas de Qualidade

### Esperado:
- **Empresas**: ~63M registros
- **Estabelecimentos**: ~66M registros
- **Sócios**: ~26M registros
- **Simples**: ~44M registros
- **Cobertura CNAE Principal**: 100%
- **Cobertura CNAE Secundário**: ~46%

### Thresholds:
- **Variação aceitável**: ±5% dos valores esperados
- **Tempo de consulta**: <1s para CNPJs individuais
- **Disponibilidade**: >99.9%