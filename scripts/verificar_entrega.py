"""Executa, verifica e salva o notebook com saídas reais de um kernel novo.

Uso: python scripts/verificar_entrega.py
Requer: python -m pip install -r requirements-dev.txt
"""

import ast
import copy
import csv
import json
import os
import shutil
import sys
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

import nbformat
from jupyter_client import KernelManager
from nbclient import NotebookClient


RAIZ = Path(__file__).resolve().parents[1]
TEMP = RAIZ / "tmp" / "verificacao"
TEMP.mkdir(parents=True, exist_ok=True)
for variavel, subpasta in (
    ("JUPYTER_CONFIG_DIR", "jupyter-config"), ("JUPYTER_DATA_DIR", "jupyter-data"),
    ("JUPYTER_RUNTIME_DIR", "jupyter-runtime"), ("IPYTHONDIR", "ipython"),
    ("MPLCONFIGDIR", "matplotlib"),
):
    diretorio = TEMP / subpasta
    diretorio.mkdir(exist_ok=True)
    os.environ.setdefault(variavel, str(diretorio))
os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")


def conferir(condicao, mensagem):
    if not condicao:
        raise AssertionError(mensagem)


def executar_notebook(notebook, pasta):
    """Executa uma cópia em kernel novo e verifica todas as saídas."""
    executado = copy.deepcopy(notebook)
    gerenciador = KernelManager(kernel_name="python3")
    gerenciador.kernel_spec.argv = [sys.executable, "-m", "ipykernel_launcher", "-f", "{connection_file}"]
    cliente = NotebookClient(executado, km=gerenciador, timeout=180,
                             allow_errors=False, resources={"metadata": {"path": str(pasta)}})
    try:
        with cliente.setup_kernel():
            cliente.execute()
    finally:
        if gerenciador.has_kernel:
            gerenciador.shutdown_kernel(now=True)
    codigo = [celula for celula in executado.cells if celula.cell_type == "code"]
    conferir([celula.execution_count for celula in codigo] == list(range(1, len(codigo) + 1)),
             "Todas as células devem ter sido executadas em ordem.")
    conferir(all(celula.outputs for celula in codigo), "Nenhuma célula de código deve ter saída vazia.")
    conferir(not any(saida.output_type == "error" for celula in codigo for saida in celula.outputs),
             "Não pode haver erros nas saídas salvas.")
    return executado


def funcoes_nativas(notebook):
    """Extrai a implementação, sem executar dados ou testes do notebook."""
    nativos = [celula for celula in notebook.cells if celula.cell_type == "code"][:9]
    nos = []
    for celula in nativos:
        arvore = ast.parse(celula.source, feature_version=(3, 10))
        for no in arvore.body:
            if isinstance(no, (ast.Import, ast.ImportFrom, ast.FunctionDef)):
                nos.append(no)
            elif isinstance(no, ast.Assign) and any(
                isinstance(alvo, ast.Name) and alvo.id in {"LIMITE_SUSPEITO", "COLUNAS_ESPERADAS"}
                for alvo in no.targets
            ):
                nos.append(no)
    conferir(sum(isinstance(no, ast.FunctionDef) for no in nos) >= 4, "Mínimo de quatro funções.")
    conferir(sum(isinstance(no, ast.Try) for pai in nos for no in ast.walk(pai)) >= 3,
             "Try/except em pelo menos três situações distintas.")
    for no in nos:
        if isinstance(no, ast.Import):
            conferir(all(nome.name.split(".")[0] in sys.stdlib_module_names for nome in no.names),
                     "A solução principal deve usar apenas módulos nativos.")
        if isinstance(no, ast.ImportFrom):
            conferir(no.module.split(".")[0] in sys.stdlib_module_names,
                     "A solução principal deve usar apenas módulos nativos.")
    modulo = ast.fix_missing_locations(ast.Module(body=nos, type_ignores=[]))
    contexto = {"__name__": "validacao_independente"}
    exec(compile(modulo, "implementacao_nativa", "exec"), contexto)
    return contexto


def conferir_nova_base(funcoes, pasta):
    """Verifica datas, valores e meses que não estão na base de demonstração."""
    nova_base = pasta / "outras_transacoes.csv"
    linhas = [
        [201, "2025-12-31", "CLIX", "credito", "10.10", "Entrada", "teste"],
        [202, "2026-01-01", "CLIX", "debito", "0.20", "Saída", "teste"],
        [203, "2026-01-02", "CLIY", "credito", "10000.00", "No limite", "teste"],
        [204, "2026-01-03", "CLIY", "debito", "10000.01", "Acima do limite", "teste"],
        [205, "2026-02-30", "CLIY", "credito", "1.00", "Data inválida", "teste"],
        [206, "2026-01-02", "CLIY", "pix", "1.00", "Tipo inválido", "teste"],
        [207, "2026-01-02", "CLIY", "credito", "nan", "Valor inválido", "teste"],
        [201, "2025-12-31", "CLIX", "credito", "10.10", "Duplicata", "teste"],
    ]
    with nova_base.open("w", encoding="utf-8", newline="") as arquivo:
        escritor = csv.writer(arquivo)
        escritor.writerow(funcoes["COLUNAS_ESPERADAS"])
        escritor.writerows(linhas)
    saida = StringIO()
    with redirect_stdout(saida):
        resultado = funcoes["executar_analise"](nova_base, pasta / "outra_analise.json")
    conferir(resultado["total_transacoes_validas"] == 4, "Nova base: quatro válidas.")
    conferir(resultado["total_transacoes_invalidas"] == 4, "Nova base: quatro inválidas.")
    conferir(resultado["total_duplicadas"] == 1, "Nova base: uma duplicata.")
    conferir(resultado["periodo_analisado"] ==
             {"data_inicio": "2025-12-31", "data_fim": "2026-01-03", "dias": 3}, "Novo período.")
    conferir(resultado["resumo_mensal"]["2026-01"] == {
        "quantidade": 3, "total_credito": 10000.0, "total_debito": 10000.21,
        "saldo": -0.21, "media": 6666.74, "maior_valor": 10000.01, "menor_valor": 0.20,
    }, "Métricas de uma nova base devem ser calculadas, sem valores fixos.")
    conferir([transacao["id"] for transacao in resultado["transacoes_suspeitas"]] == [204],
             "Regra estrita de suspeita em uma nova base.")

    sys.path.insert(0, str(RAIZ))
    from analise_pandas import analisar_com_pandas
    resultado_pandas, _ = analisar_com_pandas(
        nova_base, funcoes["validar_transacao"], gerado_em=resultado["gerado_em"]
    )
    conferir(resultado_pandas == resultado, "Pandas e nativo devem coincidir também na nova base.")
    print("[OK] Nova base independente: outro ano, quatro válidas, quatro inválidas e limite estrito.", flush=True)
    return nova_base.read_text(encoding="utf-8")


def conferir_fluxos_completos(notebook, pasta, nova_base):
    """Verifica o notebook inteiro, incluindo testes e opcionais, em seis casos."""
    cabecalho = "id,data,cliente_id,tipo,valor,descricao,categoria\n"
    casos = [
        ("outro-csv", nova_base, (8, 4, 4)),
        ("somente-invalidas", cabecalho + "1,2026-02-30,CLI001,credito,100.00,Teste,teste\n", (1, 0, 1)),
        ("somente-cabecalho", cabecalho, (0, 0, 0)),
        ("arquivo-ausente", None, None),
        ("cabecalho-incorreto", "id,data\n1,2026-01-01\n", None),
        ("sem-suspeitas", cabecalho + "1,2026-01-01,CLI001,credito,0.10,Teste,teste\n", (1, 1, 0)),
    ]
    for nome, conteudo, contagens in casos:
        destino = pasta / nome
        destino.mkdir()
        shutil.copy2(RAIZ / "analise_pandas.py", destino / "analise_pandas.py")
        if conteudo is not None:
            (destino / "transacoes.csv").write_text(conteudo, encoding="utf-8")
        executado = executar_notebook(notebook, destino)
        if contagens is None:
            conferir(not (destino / "relatorio.json").exists(), "Entrada indisponível não gera JSON.")
            conferir(not (destino / "grafico.png").exists(), "Entrada indisponível não gera gráfico.")
        else:
            resultado = json.loads((destino / "relatorio.json").read_text(encoding="utf-8"))
            conferir((resultado["total_linhas_lidas"], resultado["total_transacoes_validas"],
                      resultado["total_transacoes_invalidas"]) == contagens, "Contagens do cenário " + nome)
            conferir((destino / "grafico.png").exists() == bool(contagens[1]), "Gráfico somente com dados válidos.")
            if nome == "sem-suspeitas":
                conferir(resultado["transacoes_suspeitas"] == [], "Nenhuma suspeita na base abaixo do limite.")
                saidas = "".join(saida.get("text", "") for celula in executado.cells
                                if celula.cell_type == "code" for saida in celula.outputs)
                conferir("Nenhuma transação suspeita encontrada." in saidas, "Mensagem obrigatória sem suspeitas.")
        print(f"[OK] Notebook completo: {nome}, todas as 12 células sem erro.", flush=True)


def main():
    notebook_path = RAIZ / "desafio-final.ipynb"
    notebook = nbformat.read(notebook_path, as_version=4)
    nbformat.validate(notebook)
    # Verifica sintaxe compatível com 3.10 em todo o código entregue.
    for celula in notebook.cells:
        if celula.cell_type == "code":
            ast.parse(celula.source, feature_version=(3, 10))
    ast.parse((RAIZ / "analise_pandas.py").read_text(encoding="utf-8"), feature_version=(3, 10))
    funcoes = funcoes_nativas(notebook)

    with TemporaryDirectory(dir=TEMP) as diretorio:
        pasta = Path(diretorio)
        for nome in ("transacoes.csv", "analise_pandas.py"):
            shutil.copy2(RAIZ / nome, pasta / nome)
        print("Executando todas as células em um kernel novo...", flush=True)
        notebook = executar_notebook(notebook, pasta)
        codigo = [celula for celula in notebook.cells if celula.cell_type == "code"]
        relatorio = json.loads((pasta / "relatorio.json").read_text(encoding="utf-8"))
        conferir((relatorio["total_linhas_lidas"], relatorio["total_transacoes_validas"],
                  relatorio["total_transacoes_invalidas"], relatorio["total_duplicadas"]) == (33, 18, 15, 1),
                 "Contagens da base fornecida.")
        conferir(list(relatorio["resumo_mensal"]) == ["2026-01", "2026-02", "2026-03"], "Três meses em ordem.")
        conferir([mes["saldo"] for mes in relatorio["resumo_mensal"].values()] ==
                 [18769.60, 19430.10, -2650.10], "Saldos mensais de referência.")
        conferir([item["id"] for item in relatorio["transacoes_suspeitas"]] == [5, 10], "Duas suspeitas.")
        conferir((pasta / "grafico.png").read_bytes()[:8] == b"\x89PNG\r\n\x1a\n", "PNG válido.")
        nova_base = conferir_nova_base(funcoes, pasta)
        conferir_fluxos_completos(notebook, pasta, nova_base)

        nbformat.write(notebook, notebook_path)
        for nome in ("relatorio.json", "grafico.png"):
            shutil.copy2(pasta / nome, RAIZ / nome)
        nbformat.validate(nbformat.read(notebook_path, as_version=4))
        print(f"[OK] {len(codigo)} células executadas em ordem, com saídas e sem erros.", flush=True)
        print("[OK] Leitura nativa, validação, funções, datas, métricas, suspeitas, terminal e JSON.", flush=True)
        print("[OK] Os dois opcionais passaram: comparação com pandas e gráfico com matplotlib.", flush=True)
        print("[OK] Notebook salvo com saídas reais; relatorio.json e grafico.png atualizados.", flush=True)


if __name__ == "__main__":
    main()
