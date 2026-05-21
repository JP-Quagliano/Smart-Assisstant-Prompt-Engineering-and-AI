"""
evaluator.py — Avaliação Automática (Aula 09)
==============================================
Executa o pipeline contra `data/test_dataset.json` (legítimos) e
`data/attack_dataset.json` (maliciosos), calculando as 5 métricas
exigidas pela rubrica:

    1. Acurácia de classificação      — % tipos classificados corretamente
    2. Taxa de JSON válido            — % respostas que passaram em Pydantic
    3. Taxa de bloqueio de ataques    — % ataques corretamente bloqueados
    4. Taxa de falso positivo         — % legítimos bloqueados incorretamente
    5. Consistência                   — mesma pergunta 3x → mesma classificação?

Gera também:
- output/eval_results.csv com o detalhe caso-a-caso
- output/graficos/*.png com 4 gráficos de análise
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .chain import AssistantChain


# ---------------------------------------------------------------------------
# Estruturas de resultado
# ---------------------------------------------------------------------------

@dataclass
class CasoResultado:
    id: str
    categoria: str                   # 'legitimo' | 'ataque'
    entrada: str
    esperado_tipo: Optional[str]
    esperado_bloqueio: bool
    obtido_tipo: Optional[str]
    foi_bloqueado: bool
    motivo_bloqueio: Optional[str]
    json_valido: bool
    etapa_falha: Optional[str]
    latencia_s: float
    tokens: int
    palavras_chave_ok: Optional[bool]
    acerto_tipo: Optional[bool]
    acerto_bloqueio: bool


@dataclass
class Metricas:
    acuracia_classificacao: float = 0.0     # 1
    taxa_json_valido: float = 0.0           # 2
    taxa_bloqueio_ataques: float = 0.0      # 3
    taxa_falso_positivo: float = 0.0        # 4
    consistencia: float = 0.0               # 5
    latencia_media_s: float = 0.0
    tokens_medio: int = 0
    total_casos: int = 0
    total_legitimos: int = 0
    total_ataques: int = 0


# ---------------------------------------------------------------------------
# Avaliador
# ---------------------------------------------------------------------------

class Evaluator:
    def __init__(
        self,
        chain: AssistantChain,
        test_path: str = "data/test_dataset.json",
        attack_path: str = "data/attack_dataset.json",
        output_dir: str = "output",
    ) -> None:
        self.chain = chain
        self.test_path = Path(test_path)
        self.attack_path = Path(attack_path)
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        (self.output_dir / "graficos").mkdir(parents=True, exist_ok=True)

        self.resultados: List[CasoResultado] = []
        self.metricas: Metricas = Metricas()

    # ------------------------------------------------------------------
    # Execução
    # ------------------------------------------------------------------
    def run(self, verbose: bool = True) -> Metricas:
        casos_legit = self._load_json(self.test_path)
        casos_atk = self._load_json(self.attack_path)

        if verbose:
            print(f"[eval] Casos legítimos : {len(casos_legit)}")
            print(f"[eval] Casos de ataque : {len(casos_atk)}")
            print("[eval] Executando pipeline...\n")

        for c in casos_legit:
            self.resultados.append(self._exec_caso(c, categoria="legitimo", verbose=verbose))

        for c in casos_atk:
            self.resultados.append(self._exec_caso(c, categoria="ataque", verbose=verbose))

        self._calcular_metricas(casos_legit)
        self._salvar_csv()
        self._gerar_graficos()
        return self.metricas

    # ------------------------------------------------------------------
    # Execução de um caso
    # ------------------------------------------------------------------
    def _exec_caso(self, caso: Dict[str, Any], categoria: str, verbose: bool) -> CasoResultado:
        t0 = time.time()
        out = self.chain.run(caso["texto"])

        esperado_tipo = caso.get("tipo_esperado")
        esperado_bloq = bool(caso.get("esperado_bloqueio", categoria == "ataque"))
        obtido_tipo = (
            str(out.classificacao.tipo) if out.classificacao else None
        )

        # JSON válido = todas as etapas executadas tiveram schema válido
        # (ou foram bloqueadas antes de tentar)
        json_valido = out.etapa_falha not in {"classificacao", "processamento", "resposta"}

        # Match de palavras-chave (quando esperado pelo caso)
        palavras_chave_ok: Optional[bool] = None
        if "palavras_chave" in caso and out.resposta is not None:
            texto = out.resposta.resposta.lower()
            palavras_chave_ok = any(p.lower() in texto for p in caso["palavras_chave"])

        acerto_tipo = (
            None
            if esperado_tipo is None
            else (obtido_tipo is not None and obtido_tipo == esperado_tipo)
        )
        acerto_bloqueio = (esperado_bloq == out.bloqueado)

        r = CasoResultado(
            id=caso.get("id", "?"),
            categoria=categoria,
            entrada=caso["texto"],
            esperado_tipo=esperado_tipo,
            esperado_bloqueio=esperado_bloq,
            obtido_tipo=obtido_tipo,
            foi_bloqueado=out.bloqueado,
            motivo_bloqueio=out.motivo_bloqueio,
            json_valido=json_valido,
            etapa_falha=out.etapa_falha,
            latencia_s=time.time() - t0,
            tokens=out.tokens_total,
            palavras_chave_ok=palavras_chave_ok,
            acerto_tipo=acerto_tipo,
            acerto_bloqueio=acerto_bloqueio,
        )

        if verbose:
            ic = "🛡 " if r.foi_bloqueado else "✅"
            tipo_str = f"tipo={r.obtido_tipo or '-'}"
            esperado_str = f"esp={r.esperado_tipo or '-'}"
            print(
                f"  {ic} [{r.categoria:9}] {r.id:5} | {tipo_str:18} | {esperado_str:14} | "
                f"json={'✓' if r.json_valido else '✗'} | {r.latencia_s:5.2f}s"
            )
        return r

    # ------------------------------------------------------------------
    # Métricas
    # ------------------------------------------------------------------
    def _calcular_metricas(self, casos_legit_originais: List[Dict[str, Any]]) -> None:
        legit = [r for r in self.resultados if r.categoria == "legitimo"]
        atk = [r for r in self.resultados if r.categoria == "ataque"]

        # 1) Acurácia de classificação (apenas legítimos com tipo esperado e não bloqueados)
        com_tipo = [r for r in legit if r.esperado_tipo and not r.foi_bloqueado]
        if com_tipo:
            acertos = sum(1 for r in com_tipo if r.acerto_tipo)
            self.metricas.acuracia_classificacao = acertos / len(com_tipo)

        # 2) Taxa de JSON válido (todos os casos que NÃO eram para ser bloqueados)
        sujeitos_json = [r for r in self.resultados if not r.esperado_bloqueio]
        if sujeitos_json:
            self.metricas.taxa_json_valido = (
                sum(1 for r in sujeitos_json if r.json_valido) / len(sujeitos_json)
            )

        # 3) Taxa de bloqueio de ataques
        if atk:
            self.metricas.taxa_bloqueio_ataques = (
                sum(1 for r in atk if r.foi_bloqueado) / len(atk)
            )

        # 4) Taxa de falso positivo (legítimos bloqueados indevidamente)
        if legit:
            self.metricas.taxa_falso_positivo = (
                sum(1 for r in legit if r.foi_bloqueado) / len(legit)
            )

        # 5) Consistência: roda os 3 primeiros legítimos 3x e mede se mantêm o tipo
        amostra = casos_legit_originais[:3]
        if amostra:
            iguais = 0
            total = 0
            for c in amostra:
                tipos = []
                for _ in range(3):
                    r = self.chain.run(c["texto"])
                    tipos.append(str(r.classificacao.tipo) if r.classificacao else "?")
                # Consistente se todas as 3 execuções deram o mesmo tipo
                iguais += 1 if len(set(tipos)) == 1 else 0
                total += 1
            self.metricas.consistencia = iguais / total if total else 0.0

        # Aux
        if self.resultados:
            self.metricas.latencia_media_s = (
                sum(r.latencia_s for r in self.resultados) / len(self.resultados)
            )
            self.metricas.tokens_medio = int(
                sum(r.tokens for r in self.resultados) / len(self.resultados)
            )
        self.metricas.total_casos = len(self.resultados)
        self.metricas.total_legitimos = len(legit)
        self.metricas.total_ataques = len(atk)

    # ------------------------------------------------------------------
    # CSV
    # ------------------------------------------------------------------
    def _salvar_csv(self) -> None:
        import csv

        path = self.output_dir / "eval_results.csv"
        with path.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow([
                "id", "categoria", "entrada", "esperado_tipo", "esperado_bloqueio",
                "obtido_tipo", "foi_bloqueado", "motivo_bloqueio", "json_valido",
                "etapa_falha", "latencia_s", "tokens", "palavras_chave_ok",
                "acerto_tipo", "acerto_bloqueio",
            ])
            for r in self.resultados:
                w.writerow([
                    r.id, r.categoria, r.entrada[:120], r.esperado_tipo,
                    r.esperado_bloqueio, r.obtido_tipo, r.foi_bloqueado,
                    r.motivo_bloqueio, r.json_valido, r.etapa_falha,
                    f"{r.latencia_s:.3f}", r.tokens, r.palavras_chave_ok,
                    r.acerto_tipo, r.acerto_bloqueio,
                ])

    # ------------------------------------------------------------------
    # Gráficos
    # ------------------------------------------------------------------
    def _gerar_graficos(self) -> None:
        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
            import pandas as pd
        except ImportError:
            print("[eval] matplotlib/pandas indisponíveis — gráficos pulados.")
            return

        gdir = self.output_dir / "graficos"

        # ---- Gráfico 1: barra das 5 métricas ----
        m = self.metricas
        labels = [
            "Acurácia\nclassificação",
            "JSON\nválido",
            "Bloqueio\nataques",
            "Falso\npositivo",
            "Consis-\ntência",
        ]
        valores = [
            m.acuracia_classificacao, m.taxa_json_valido,
            m.taxa_bloqueio_ataques, m.taxa_falso_positivo, m.consistencia,
        ]
        cores = ["#16a34a", "#2563eb", "#7c3aed", "#dc2626", "#f59e0b"]

        fig, ax = plt.subplots(figsize=(8, 4.5))
        bars = ax.bar(labels, valores, color=cores)
        ax.set_ylim(0, 1.05)
        ax.set_ylabel("Taxa (0-1)")
        ax.set_title("5 Métricas — Smart Assistant (Grupo 02)")
        for b, v in zip(bars, valores):
            ax.text(b.get_x() + b.get_width() / 2, b.get_height() + 0.02,
                    f"{v:.0%}", ha="center", fontsize=9, fontweight="bold")
        plt.tight_layout()
        plt.savefig(gdir / "01_metricas_principais.png", dpi=140)
        plt.close()

        # ---- Gráfico 2: matriz de classificação (legítimos) ----
        legit = [r for r in self.resultados if r.categoria == "legitimo" and r.esperado_tipo]
        if legit:
            tipos = ["reclamacao", "duvida", "elogio", "sugestao"]
            matriz = [[0] * len(tipos) for _ in tipos]
            for r in legit:
                if r.esperado_tipo in tipos and r.obtido_tipo in tipos:
                    i = tipos.index(r.esperado_tipo)
                    j = tipos.index(r.obtido_tipo)
                    matriz[i][j] += 1

            fig, ax = plt.subplots(figsize=(6, 5))
            im = ax.imshow(matriz, cmap="Blues")
            ax.set_xticks(range(len(tipos)))
            ax.set_yticks(range(len(tipos)))
            ax.set_xticklabels(tipos, rotation=20)
            ax.set_yticklabels(tipos)
            ax.set_xlabel("Predito")
            ax.set_ylabel("Esperado")
            ax.set_title("Matriz de Classificação (Etapa 1)")
            for i in range(len(tipos)):
                for j in range(len(tipos)):
                    ax.text(j, i, matriz[i][j], ha="center", va="center",
                            color="white" if matriz[i][j] > 1 else "black", fontweight="bold")
            plt.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
            plt.tight_layout()
            plt.savefig(gdir / "02_matriz_classificacao.png", dpi=140)
            plt.close()

        # ---- Gráfico 3: bloqueio por categoria ----
        cats = ["Legítimos\nbloqueados", "Legítimos\nliberados",
                "Ataques\nbloqueados", "Ataques\nliberados"]
        legit = [r for r in self.resultados if r.categoria == "legitimo"]
        atk = [r for r in self.resultados if r.categoria == "ataque"]
        vals = [
            sum(1 for r in legit if r.foi_bloqueado),
            sum(1 for r in legit if not r.foi_bloqueado),
            sum(1 for r in atk if r.foi_bloqueado),
            sum(1 for r in atk if not r.foi_bloqueado),
        ]
        colors = ["#fca5a5", "#86efac", "#86efac", "#fca5a5"]
        fig, ax = plt.subplots(figsize=(7, 4))
        ax.bar(cats, vals, color=colors)
        for i, v in enumerate(vals):
            ax.text(i, v + 0.1, str(v), ha="center", fontweight="bold")
        ax.set_ylabel("Casos")
        ax.set_title("Bloqueios — Legítimos vs Ataques")
        plt.tight_layout()
        plt.savefig(gdir / "03_bloqueio_por_categoria.png", dpi=140)
        plt.close()

        # ---- Gráfico 4: latência por etapa ----
        lat_por_cat = {
            "Legítimos": [r.latencia_s for r in legit],
            "Ataques": [r.latencia_s for r in atk],
        }
        fig, ax = plt.subplots(figsize=(7, 4))
        ax.boxplot(
            [v for v in lat_por_cat.values() if v],
            labels=[k for k, v in lat_por_cat.items() if v],
        )
        ax.set_ylabel("Latência (s)")
        ax.set_title("Distribuição de Latência por Categoria")
        plt.tight_layout()
        plt.savefig(gdir / "04_latencia.png", dpi=140)
        plt.close()

    # ------------------------------------------------------------------
    # Util
    # ------------------------------------------------------------------
    @staticmethod
    def _load_json(path: Path) -> List[Dict[str, Any]]:
        with path.open(encoding="utf-8") as f:
            return json.load(f)

    # ------------------------------------------------------------------
    # Relatório textual
    # ------------------------------------------------------------------
    def relatorio_texto(self) -> str:
        m = self.metricas
        sep = "═" * 60
        linhas = [
            sep,
            "RELATÓRIO DE AVALIAÇÃO — SMART ASSISTANT (Grupo 02)",
            sep,
            f"Total de casos          : {m.total_casos}",
            f"  ├─ Legítimos          : {m.total_legitimos}",
            f"  └─ Ataques            : {m.total_ataques}",
            "",
            "MÉTRICAS PRINCIPAIS",
            f"  1. Acurácia classif.  : {m.acuracia_classificacao:.0%}",
            f"  2. JSON válido        : {m.taxa_json_valido:.0%}",
            f"  3. Bloqueio ataques   : {m.taxa_bloqueio_ataques:.0%}",
            f"  4. Falso positivo     : {m.taxa_falso_positivo:.0%}",
            f"  5. Consistência (3x)  : {m.consistencia:.0%}",
            "",
            "DESEMPENHO",
            f"  Latência média        : {m.latencia_media_s:.2f}s",
            f"  Tokens médio          : {m.tokens_medio}",
            sep,
        ]
        return "\n".join(linhas)
