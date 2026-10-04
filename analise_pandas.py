"""Versão opcional: leitura e agrupamento com pandas, usados pelo notebook.

A validação de cada linha é recebida do notebook para aplicar as mesmas regras
às duas leituras. A leitura, a deduplicação e os cálculos são feitos com pandas.
"""

from collections.abc import Callable
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

import pandas as pd


def _moeda(valor: Decimal) -> float:
    """Arredonda apenas a saída, preservando a precisão dos cálculos."""
    return float(valor.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def analisar_com_pandas(
    arquivo_csv: str | Path,
    validador: Callable,
    limite_suspeito: float = 10000.00,
    gerado_em: str | None = None,
) -> tuple[dict, pd.DataFrame]:
    """Lê com read_csv, agrupa com groupby e devolve relatório e tabela mensal."""
    # dtype=str e keep_default_na=False preservam vazios e "nan" para o validador.
    dados = pd.read_csv(arquivo_csv, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    validadas = [validador(linha) for linha in dados.to_dict(orient="records")]
    limpos = pd.DataFrame([linha for linha in validadas if linha is not None])
    duplicadas = int(limpos.duplicated(subset="id").sum()) if not limpos.empty else 0
    limpos = limpos.drop_duplicates(subset="id", keep="first") if not limpos.empty else limpos

    resumo = {}
    suspeitas = []
    periodo = None
    if not limpos.empty:
        limpos["data"] = pd.to_datetime(limpos["data"])
        limpos["mes"] = limpos["data"].dt.strftime("%Y-%m")
        limpos["valor_decimal"] = limpos["valor"].map(lambda valor: Decimal(str(valor)))
        zero = Decimal("0")
        limpos["credito"] = limpos["valor_decimal"].where(limpos["tipo"].eq("credito"), zero)
        limpos["debito"] = limpos["valor_decimal"].where(limpos["tipo"].eq("debito"), zero)
        agrupados = limpos.groupby("mes", sort=True).agg(
            quantidade=("id", "size"),
            total_credito=("credito", "sum"),
            total_debito=("debito", "sum"),
            total_valor=("valor_decimal", "sum"),
            maior_valor=("valor_decimal", "max"),
            menor_valor=("valor_decimal", "min"),
        )
        for mes, linha in agrupados.iterrows():
            quantidade = int(linha["quantidade"])
            resumo[mes] = {
                "quantidade": quantidade,
                "total_credito": _moeda(linha["total_credito"]),
                "total_debito": _moeda(linha["total_debito"]),
                "saldo": _moeda(linha["total_credito"] - linha["total_debito"]),
                "media": _moeda(linha["total_valor"] / quantidade),
                "maior_valor": _moeda(linha["maior_valor"]),
                "menor_valor": _moeda(linha["menor_valor"]),
            }
        marcadas = limpos.loc[limpos["valor"].gt(limite_suspeito)]
        suspeitas = [
            {"id": int(linha.id), "cliente_id": linha.cliente_id,
             "data": linha.data.strftime("%Y-%m-%d"), "valor": float(linha.valor)}
            for linha in marcadas.itertuples(index=False)
        ]
        primeira, ultima = limpos["data"].min(), limpos["data"].max()
        periodo = {
            "data_inicio": primeira.strftime("%Y-%m-%d"),
            "data_fim": ultima.strftime("%Y-%m-%d"),
            "dias": int((ultima - primeira).days),
        }

    relatorio = {
        "gerado_em": gerado_em or date.today().isoformat(),
        "total_linhas_lidas": len(dados),
        "total_transacoes_validas": len(limpos),
        "total_transacoes_invalidas": len(dados) - len(limpos),
        "total_duplicadas": duplicadas,
        "periodo_analisado": periodo,
        "resumo_mensal": resumo,
        "transacoes_suspeitas": suspeitas,
    }
    tabela = pd.DataFrame.from_dict(resumo, orient="index")
    tabela.index.name = "mes"
    return relatorio, tabela
