# 🚀 CNPJ-XRay - Guia de Configuração Inicial

## ✅ Checklist Pré-Execução

Antes de executar o ETL, siga este checklist:

### 1. ✅ Verificar PostgreSQL

O banco PostgreSQL precisa estar rodando. Verifique:

```bash
# Verificar se PostgreSQL está rodando
systemctl status postgresql

# Ou, se estiver usando Docker:
docker ps | grep postgres

# Testar conectividade (ajuste a porta conforme seu ambiente)
nc -zv localhost 5432
# ou
nc -zv localhost 5436
```

**Se PostgreSQL NÃO estiver rodando:**

```bash
# Iniciar PostgreSQL no sistema
sudo systemctl start postgresql

# Ou, se estiver usando Docker:
docker start nome_do_container_postgres
```

---

### 2. ✅ Criar arquivo .env

O arquivo `.env` JÁ FOI CRIADO para você, mas precisa ser configurado:

```bash
# Editar o arquivo .env
nano .env
# ou
vim .env
# ou
code .env
```

**IMPORTANTE:** Altere as seguintes variáveis:

#### a) Senha do PostgreSQL
```bash
DB_PASSWORD=sua_senha_aqui  # ← ALTERE AQUI
```

#### b) Porta do PostgreSQL
Se seu PostgreSQL usa porta diferente de 5436, ajuste:
```bash
DB_PORT=5432  # ou 5436, ou a porta que você usa
```

#### c) Verificar se os caminhos estão corretos
```bash
OUTPUT_FILES_PATH=./dados/downloads      # ← Onde os ZIPs serão baixados
EXTRACTED_FILES_PATH=./dados/extracted   # ← Onde os CSVs serão extraídos
```

---

### 3. ✅ Criar diretórios necessários

Os diretórios são criados automaticamente pelo script, mas você pode criá-los manualmente:

```bash
# Criar diretórios de dados
mkdir -p dados/downloads
mkdir -p dados/extracted

# Verificar
ls -la dados/
```

---

### 4. ✅ Criar o banco de dados

Se o banco `receita_federal` não existir, crie-o:

```bash
# Usando psql
psql -U postgres -c "CREATE DATABASE receita_federal;"

# Ou conectar primeiro e depois criar
psql -U postgres
# Dentro do psql:
postgres=# CREATE DATABASE receita_federal;
postgres=# \q
```

**Verificar se o banco foi criado:**
```bash
psql -U postgres -l | grep receita_federal
```

---

### 5. ✅ Testar conexão com o banco

```bash
# Testar conexão
psql -U postgres -d receita_federal -c "SELECT version();"
```

Se conectar com sucesso, está tudo certo! ✅

---

## 🎯 Executar o ETL

Agora que tudo está configurado, execute o ETL:

### Opção 1: Baixar versão mais recente (RECOMENDADO)
```bash
uv run src/etl/ETL_dados_publicos_empresas.py --last
```

### Opção 2: Modo interativo
```bash
uv run src/etl/ETL_dados_publicos_empresas.py
```

### Opção 3: Versão específica
```bash
uv run src/etl/ETL_dados_publicos_empresas.py 01-2026
```

---

## 🐛 Troubleshooting

### Erro: "Arquivo .env não encontrado"

**Causa:** O arquivo `.env` não está no diretório raiz do projeto.

**Solução:**
```bash
# Verificar se está no diretório correto
pwd
# Deve mostrar o diretório raiz do CNPJ-XRay

# Verificar se .env existe
ls -la .env

# Se não existir, copiar do exemplo
cp .env.example .env
nano .env  # Editar com suas configurações
```

---

### Erro: "expected str, bytes or os.PathLike object, not NoneType"

**Causa:** Variáveis `OUTPUT_FILES_PATH` ou `EXTRACTED_FILES_PATH` não configuradas no `.env`

**Solução:**
```bash
# Editar .env e adicionar:
nano .env

# Adicione estas linhas:
OUTPUT_FILES_PATH=./dados/downloads
EXTRACTED_FILES_PATH=./dados/extracted
```

---

### Erro: "Connect call failed" ou "[Errno 111]"

**Causa:** PostgreSQL não está rodando ou porta incorreta.

**Soluções:**

1. **Iniciar PostgreSQL:**
   ```bash
   sudo systemctl start postgresql
   # ou
   docker start postgres_container
   ```

2. **Verificar porta correta:**
   ```bash
   # Ver em qual porta o PostgreSQL está rodando
   sudo netstat -tlnp | grep postgres
   # ou
   sudo ss -tlnp | grep postgres
   ```
   
   Ajuste no `.env`:
   ```bash
   DB_PORT=5432  # ou a porta mostrada no comando acima
   ```

3. **Verificar senha:**
   Certifique-se que a senha no `.env` está correta:
   ```bash
   DB_PASSWORD=sua_senha_real_aqui
   ```

---

### Erro: "database does not exist"

**Causa:** Banco `receita_federal` não foi criado.

**Solução:**
```bash
psql -U postgres -c "CREATE DATABASE receita_federal;"
```

---

## 📊 Verificar Configuração Completa

Execute este script de verificação:

```bash
#!/bin/bash
echo "🔍 Verificando configuração do ambiente..."
echo ""

# 1. Verificar .env
echo "1. Arquivo .env:"
if [ -f .env ]; then
    echo "   ✅ .env existe"
else
    echo "   ❌ .env NÃO encontrado"
    exit 1
fi

# 2. Verificar diretórios
echo "2. Diretórios:"
if [ -d dados/downloads ] && [ -d dados/extracted ]; then
    echo "   ✅ Diretórios criados"
else
    echo "   ⚠️  Criando diretórios..."
    mkdir -p dados/downloads dados/extracted
fi

# 3. Verificar PostgreSQL
echo "3. PostgreSQL:"
if systemctl is-active --quiet postgresql; then
    echo "   ✅ PostgreSQL rodando"
elif docker ps | grep -q postgres; then
    echo "   ✅ PostgreSQL rodando (Docker)"
else
    echo "   ❌ PostgreSQL NÃO está rodando"
    exit 1
fi

# 4. Testar conexão
echo "4. Conexão com banco:"
source .env
if psql -U "$DB_USER" -h "$DB_HOST" -p "$DB_PORT" -d postgres -c "SELECT 1" > /dev/null 2>&1; then
    echo "   ✅ Conexão OK"
else
    echo "   ❌ Não foi possível conectar ao banco"
    exit 1
fi

echo ""
echo "✅ Tudo pronto! Pode executar o ETL."
```

Salve como `check_config.sh`, dê permissão e execute:
```bash
chmod +x check_config.sh
./check_config.sh
```

---

## 📝 Resumo das Variáveis Obrigatórias

No arquivo `.env`, estas variáveis SÃO OBRIGATÓRIAS:

```bash
DB_HOST=localhost                        # Host do PostgreSQL
DB_PORT=5432                            # Porta do PostgreSQL (5432 ou 5436)
DB_NAME=receita_federal                 # Nome do banco
DB_USER=postgres                        # Usuário do banco
DB_PASSWORD=COLOQUE_SUA_SENHA_AQUI     # ← IMPORTANTE: Senha real
DB_SSL_MODE=disable                     # disable para local, require para produção

OUTPUT_FILES_PATH=./dados/downloads     # Onde baixar os ZIPs
EXTRACTED_FILES_PATH=./dados/extracted  # Onde extrair os CSVs
```

---

## 🎉 Pronto!

Após seguir este guia, você estará pronto para executar o ETL com sucesso:

```bash
uv run src/etl/ETL_dados_publicos_empresas.py --last
```

**Tempo estimado de execução:** 4-8 horas (dependendo da conexão e hardware)
**Espaço em disco necessário:** ~100GB (compactado) + ~400GB (descompactado)

---

**Última atualização:** 2026-01-29  
**Versão:** 2.2.0
