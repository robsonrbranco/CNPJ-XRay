"""A imagem do pod não carrega o que o pod não usa.

O `Dockerfile` instala menos pacotes do que o `pyproject.toml` declara: o pod
serve consulta e **nunca roda o ETL**, então `polars` e `rich` ficam de fora.
São 181 MB de `polars` (o runtime compilado sozinho tem 172 MB) e 12 MB de
`rich` + `pygments` que não precisam existir num container de API.

Duas coisas tiveram de mudar no código para isso ser possível, e as duas são
do tipo que volta sozinha se ninguém vigiar:

* `src/db/__init__.py` reexportava `carregar`/`normalizar` de `.loader`, e o
  `__init__.py` roda no primeiro import de QUALQUER submódulo — então um
  `from ..db import connection` arrastava `polars`;
* `src/consulta/empresa.py` importava `rich` no topo para a saída da CLI, e a
  API importa esse módulo por causa de `consultar()`.

A economia só se sustenta enquanto o grafo de import da API não encostar
neles. Um import acrescentado sem pensar derrubaria o pod com
`ModuleNotFoundError` — e só na hora de subir em produção, porque em
desenvolvimento os dois estão instalados e o import passa.

É isto que este arquivo trava: o teste roda com `polars` e `rich`
INSTALADOS e mesmo assim reprova se forem alcançados.
"""

import subprocess
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent

# O que o pod NÃO tem instalado. Ver o `pip install` do Dockerfile.
AUSENTES_NA_IMAGEM = ("polars", "rich", "pygments")

# Tudo o que os DOIS containers do pod importam: a API pública, o /manager e a
# camada de consulta que elas usam.
SUPERFICIE_DO_POD = """
import sys
from src.api.publica import criar_app
from src.api.manager import criar_app as criar_manager
from src.api import cnpj, serpro, esquemas, token, uso, credenciais, conexao, config
from src.consulta import empresa
from src.db import manage, schema, connection
print(",".join(sorted({m.split(".")[0] for m in sys.modules})))
"""


def _modulos_de_topo() -> set[str]:
    """Importa a superfície do pod num processo LIMPO e devolve o que entrou.

    Processo separado de propósito: o pytest já importou meio mundo, e medir
    `sys.modules` aqui dentro daria sempre positivo.
    """
    r = subprocess.run(
        [sys.executable, "-c", SUPERFICIE_DO_POD],
        cwd=RAIZ, capture_output=True, text=True,
    )
    assert r.returncode == 0, f"a superfície do pod não importa:\n{r.stderr}"
    return set(r.stdout.strip().split(","))


@pytest.mark.parametrize("pacote", AUSENTES_NA_IMAGEM)
def test_a_api_nao_alcanca_o_que_a_imagem_nao_tem(pacote):
    """Se este teste falhar, o pod quebra ao subir — não aqui.

    O conserto é um dos dois: mover o import para dentro da função que o usa
    (como `consulta/empresa.console()` faz com o rich), ou reconhecer que o
    pacote virou dependência de runtime e acrescentá-lo ao `pip install` do
    Dockerfile, pagando os MB.
    """
    assert pacote not in _modulos_de_topo(), (
        f"importar a superfície do pod agora puxa `{pacote}`, que o Dockerfile "
        f"NÃO instala na imagem. O pod subiria com ModuleNotFoundError."
    )


def test_o_init_do_db_continua_sem_reexportar_o_loader():
    """A regressão mais provável, porque parece uma melhoria.

    Reexportar do `__init__.py` é um reflexo comum, e aqui custa 181 MB: o
    `__init__` roda no primeiro import de qualquer submódulo.
    """
    import ast

    # Pela ARVORE, nao por texto: o docstring deste arquivo fala sobre imports,
    # e uma busca por substring acusaria a propria explicacao.
    arvore = ast.parse((RAIZ / "src" / "db" / "__init__.py").read_text(encoding="utf-8"))
    imports = [n for n in ast.walk(arvore)
               if isinstance(n, (ast.Import, ast.ImportFrom))]
    assert not imports, (
        "src/db/__init__.py voltou a importar submódulo "
        f"(linha {imports[0].lineno}). Se for mesmo necessário, confira que "
        "não alcança `loader` — ele importa `polars`."
    )


def test_o_dockerfile_e_o_teste_concordam():
    """Trava a lista deste arquivo contra o `pip install` real.

    Sem isto, alguém acrescenta `polars` de volta ao Dockerfile, o teste
    continua verde defendendo uma restrição que não existe mais, e ninguém
    percebe que os 181 MB voltaram.
    """
    dockerfile = (RAIZ / "Dockerfile").read_text(encoding="utf-8")
    instalados = dockerfile.split("pip install")[1].split("&&")[0]
    for pacote in AUSENTES_NA_IMAGEM:
        assert f'"{pacote}' not in instalados, (
            f"`{pacote}` voltou ao Dockerfile — atualize AUSENTES_NA_IMAGEM "
            f"aqui, ou remova-o de lá."
        )


def test_o_etl_continua_precisando_de_polars():
    """O contrapeso: `polars` não é dispensável, é só de OUTRO processo."""
    r = subprocess.run(
        [sys.executable, "-c",
         "import sys; from src.etl import leitura; print('polars' in sys.modules)"],
        cwd=RAIZ, capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "True"


def test_a_cli_de_consulta_continua_exibindo():
    """O rich saiu do topo, mas a CLI tem que seguir funcionando.

    Se o `console()` preguiçoso quebrar, `python -m src.consulta.empresa`
    morre — e isso não aparece em nenhum teste de rota.
    """
    r = subprocess.run(
        [sys.executable, "-c",
         "from src.consulta import empresa; "
         "empresa.console().print('[bold]ok[/bold]')"],
        cwd=RAIZ, capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stderr
    assert "ok" in r.stdout
