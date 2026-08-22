# 🔎 CNPJ-XRay

Raio-X dos dados públicos de CNPJ: ETL completo para baixar, processar e consultar o Cadastro Nacional da Pessoa Jurídica (CNPJ) da Receita Federal do Brasil em PostgreSQL.

## 📋 Sobre o Projeto

A Receita Federal do Brasil disponibiliza bases com os dados públicos do cadastro nacional de pessoas jurídicas (CNPJ). Nelas constam as mesmas informações que conseguimos ver no cartão do CNPJ, quando fazemos uma consulta individual, acrescidas de outros dados de Simples Nacional, sócios e etc.

Este repositório contém um processo de ETL completo para:
- **🔽 Baixar** os arquivos da fonte oficial
- **📦 Descompactar** os arquivos ZIP
- **🔧 Processar** e tratar os dados
- **💾 Inserir** em banco PostgreSQL otimizado
- **🔍 Consultar** dados de forma eficiente

## 🗂️ Estrutura do Projeto

```
📁 src/
├── 📁 etl/                    # 🔄 Processo ETL Principal
│   ├── ETL_dados_publicos_empresas.py    # Script principal do ETL
│   ├── resume_etl.py                     # Retomar ETL interrompido
│   └── README.md
├── 📁 validation/             # ✅ Validação de Dados
│   ├── check_database_status.py         # Verificar integridade
│   └── README.md
├── 📁 indexes/                # 📊 Otimização de Performance
│   ├── create_indexes.py                # Criar índices otimizados
│   └── README.md
├── 📁 sql/                    # 📄 Scripts SQL Indispensáveis
│   ├── banco_de_dados.sql               # Estrutura do banco
│   ├── database_setup.sql               # Configurações avançadas
│   ├── consulta_empresa_completa.sql    # Consultas principais
│   └── README.md
├── 📁 auxiliary/              # 🛠️ Scripts Auxiliares
│   ├── 📁 python/
│   │   ├── consultar_empresa.py         # Interface de consulta
│   │   ├── dump_and_restore.py          # Backup/restauração
│   │   └── sql_dump_generator.py        # Gerador de dumps SQL
│   ├── 📁 sql/                          # Scripts SQL auxiliares
│   ├── README.md
│   └── DUMP_RESTORE_README.md
└── README.md
```

## 🚀 Início Rápido

### 1. **Pré-requisitos**
- PostgreSQL 12+ instalado
- Python 3.8+
- UV (recomendado) ou pip

### 2. **Instalação**
```bash
# Clonar repositório
git clone https://github.com/robsonrbranco/CNPJ-XRay.git
cd CNPJ-XRay

# Instalar dependências
uv install
# ou: pip install -r requirements.txt

# Configurar ambiente
cp env.example .env
# Editar .env com suas configurações
```

### 3. **Configuração do Banco**
```bash
# Criar banco de dados
createdb -U postgres receita_federal

# Criar estrutura
psql -U postgres -d receita_federal -f src/sql/banco_de_dados.sql
```

### 4. **Execução**

#### 🎯 Modos de Execução do ETL

O ETL suporta **3 modos de operação**:

**a) Modo Interativo (padrão):**
```bash
# Solicita ano/mês interativamente
uv run src/etl/ETL_dados_publicos_empresas.py
```

**b) Modo Automático (versão mais recente):**
```bash
# Detecta e baixa automaticamente a versão mais recente da Receita Federal
uv run src/etl/ETL_dados_publicos_empresas.py --last
```

**c) Modo Específico (data customizada):**
```bash
# Baixa uma versão específica (formato: MM-AAAA)
uv run src/etl/ETL_dados_publicos_empresas.py 01-2025
uv run src/etl/ETL_dados_publicos_empresas.py 12-2024
```

**Ver ajuda:**
```bash
uv run src/etl/ETL_dados_publicos_empresas.py --help
```

#### 📋 Processos Complementares

```bash
# Validar dados
uv run src/validation/check_database_status.py

# Criar índices
uv run src/indexes/create_indexes.py

# Aplicar configurações avançadas
psql -U postgres -d receita_federal -f src/sql/database_setup.sql
```

## 📊 Dados Processados

### Tabelas Principais (~200M registros):
- **`empresa`**: Dados básicos das empresas (~63M registros)
- **`estabelecimento`**: Estabelecimentos/filiais (~66M registros)
- **`socios`**: Sócios e representantes (~26M registros)
- **`simples`**: Regime tributário Simples Nacional (~44M registros)

### Tabelas de Referência:
- **`cnae`**: Códigos de atividade econômica (1.359 registros)
- **`natureza`**: Naturezas jurídicas (90 registros)
- **`municipio`**: Códigos dos municípios (5.572 registros)
- **`pais`**: Códigos dos países (255 registros)
- **`qualificacao`**: Qualificações de sócios (68 registros)
- **`motivo`**: Motivos de situação cadastral (63 registros)

## 🔍 Consultas e Uso

### Consultar Empresa por CNPJ:
```bash
# CNPJ completo
uv run src/auxiliary/python/consultar_empresa.py 11222333000181

# CNPJ básico
uv run src/auxiliary/python/consultar_empresa.py 11222333

# Com formatação
uv run src/auxiliary/python/consultar_empresa.py 11.222.333/0001-81
```

### Consultas SQL Diretas:
```sql
-- Buscar empresa completa
SELECT 
    e.razao_social,
    est.nome_fantasia,
    est.situacao_cadastral,
    cnae.descricao as atividade_principal,
    mun.descricao as municipio,
    est.uf
FROM empresa e
JOIN estabelecimento est ON e.cnpj_basico = est.cnpj_basico
LEFT JOIN cnae ON est.cnae_fiscal_principal = cnae.codigo
LEFT JOIN municipio mun ON est.municipio = mun.codigo
WHERE e.cnpj_basico = '11222333'
AND est.cnpj_ordem = '0001';
```

## 💾 Backup e Restauração

### Fazer Backup:
```bash
# Backup completo
uv run src/auxiliary/python/dump_and_restore.py dump

# Apenas estrutura
uv run src/auxiliary/python/dump_and_restore.py model

# Informações do banco
uv run src/auxiliary/python/dump_and_restore.py info
```

### Restaurar Banco:
```bash
# Restaurar em outro ambiente
uv run src/auxiliary/python/dump_and_restore.py restore arquivo_backup.dump
```

## ⚡ Performance e Otimizações

### Configurações Recomendadas PostgreSQL:
```sql
-- Configurações para melhor performance
SET work_mem = '1GB';
SET maintenance_work_mem = '2GB';
SET shared_buffers = '4GB';
SET max_wal_size = '4GB';
SET checkpoint_completion_target = 0.9;
```

### Índices Otimizados:
- **CNPJs**: Busca por empresa/estabelecimento (~1-5ms)
- **Situação**: Filtros por situação cadastral (~1-3s)
- **Localização**: Consultas por município/UF (~500ms-2s)
- **Atividade**: Filtros por CNAE (~1-5s)

### Estatísticas:
- **Tamanho do banco**: ~32GB
- **Tempo de ETL**: 3-5 horas
- **Tempo de índices**: 30-60 minutos
- **Consultas otimizadas**: <100ms

## 🛠️ Configuração Avançada

### Arquivo `.env`:
```env
# Configurações do banco
DB_HOST=localhost
DB_PORT=5432
DB_NAME=receita_federal
DB_USER=postgres
DB_PASSWORD=sua_senha

# Configurações de performance
CHUNK_SIZE=10000
MAX_WORKERS=4
TIMEOUT=3600

# Configurações de rede
DOWNLOAD_TIMEOUT=600
MAX_RETRIES=3
```

### Recursos do Sistema:
- **RAM mínima**: 8GB
- **Espaço em disco**: 50GB livres
- **CPU**: Multi-core recomendado
- **Rede**: Conexão estável para downloads

## 📋 Comandos Úteis

### Verificar Estrutura:
```bash
# Verificar organização do projeto
uv run check_structure.py
```

### Monitoramento:
```bash
# Acompanhar logs
tail -f etl_log.log

# Verificar status do banco
uv run src/validation/check_database_status.py

# Estatísticas detalhadas
uv run src/auxiliary/python/dump_and_restore.py info
```

### Solução de Problemas:
```bash
# Retomar ETL interrompido
uv run src/etl/resume_etl.py

# Recriar índices
uv run src/indexes/create_indexes.py

# Verificar integridade
psql -d receita_federal -c "SELECT COUNT(*) FROM empresa;"
```

## 🔧 Troubleshooting

### Problemas Comuns:

1. **Timeout na criação de índices**:
   ```bash
   # Use o script de retomada
   uv run src/etl/resume_etl.py
   ```

2. **Memória insuficiente**:
   ```sql
   -- Ajustar configurações PostgreSQL
   SET work_mem = '512MB';
   SET maintenance_work_mem = '1GB';
   ```

3. **Erro de conexão**:
   ```bash
   # Verificar .env e PostgreSQL
   psql -h localhost -p 5432 -U postgres -d receita_federal
   ```

4. **Espaço em disco**:
   ```bash
   # Verificar espaço disponível
   df -h
   
   # Limpar arquivos temporários
   rm -rf downloads/ temp/
   ```

## 📚 Documentação Completa

- **[Processo ETL](src/etl/README.md)**: Detalhes do processo de extração, transformação e carga
- **[Validação](src/validation/README.md)**: Verificação de integridade e qualidade dos dados
- **[Índices](src/indexes/README.md)**: Otimização de performance e consultas
- **[SQL](src/sql/README.md)**: Scripts SQL essenciais e configurações
- **[Auxiliares](src/auxiliary/README.md)**: Scripts complementares e utilitários

## 🌐 Fontes Oficiais

- **[Dados Oficiais](https://dados.gov.br/dados/conjuntos-dados/cadastro-nacional-da-pessoa-juridica---cnpj)**: Fonte da Receita Federal
- **[Layout dos Arquivos](https://www.gov.br/receitafederal/dados/cnpj-metadados.pdf)**: Documentação técnica oficial
- **[Consulta Individual](https://solucoes.receita.fazenda.gov.br/servicos/cnpjreva/cnpjreva_solicitacao.asp)**: Consulta no site da Receita Federal

## 🤝 Contribuição

Contribuições são bem-vindas! Por favor:

1. Faça fork do repositório
2. Crie branch para sua feature (`git checkout -b feature/nova-funcionalidade`)
3. Commit suas mudanças (`git commit -am 'Adicionar nova funcionalidade'`)
4. Push para branch (`git push origin feature/nova-funcionalidade`)
5. Abra Pull Request

## 🙏 Créditos

O CNPJ-XRay deriva do trabalho de:

- [aphonsoar/Receita_Federal_do_Brasil_-_Dados_Publicos_CNPJ](https://github.com/aphonsoar/Receita_Federal_do_Brasil_-_Dados_Publicos_CNPJ) — projeto original
- [fonsecach/dados-publicos-cnpj](https://github.com/fonsecach/dados-publicos-cnpj) — base direta deste fork

## 📄 Licença

Este projeto está sob a licença MIT, herdada dos projetos de origem. Veja o arquivo `LICENSE` para detalhes.

## 🚨 Aviso Legal

Este projeto processa dados públicos disponibilizados pela Receita Federal do Brasil. O uso dos dados deve respeitar os termos de uso estabelecidos pelo órgão oficial. Os desenvolvedores não se responsabilizam pelo uso inadequado das informações processadas.

## 📞 Suporte

Para problemas, sugestões ou dúvidas:
- **Issues**: Abra uma issue no GitHub
- **Documentação**: Consulte os READMEs específicos de cada módulo
