"""Camada de acesso a dados do CNPJ-XRay (Firebird 3.0).

Este arquivo está VAZIO de propósito, e vale saber por quê antes de
"melhorá-lo" com os re-exports que estavam aqui.

Ele reexportava `carregar` e `normalizar` de `.loader`, que importa `polars`.
Como o `__init__.py` roda no primeiro import de QUALQUER submódulo, um
inocente `from ..db import connection` — que é o que `api/conexao.py` e
`consulta/empresa.py` fazem — arrastava 181 MB de `polars` para dentro da
imagem do pod, que nunca roda o ETL.

Medido: com os re-exports, importar a superfície da API trazia `polars` E
`rich`; sem eles, nenhum dos dois.

Os re-exports também não serviam a ninguém: uma varredura em `src/` e
`tests/` não achou **um só** uso de `from src.db import carregar` ou dos
outros 16 nomes. Todo consumidor já importava o submódulo direto
(`from src.db import manage, schema`), que é a forma que não paga por isso.

`tests/test_imagem_enxuta.py` trava a propriedade: roda com `polars`
INSTALADO e reprova se a superfície do pod alcançar ele. Sem esse teste, a
regressão só apareceria ao subir o pod em produção, com
`ModuleNotFoundError` — em desenvolvimento o import passa.
"""
