"""
main.py — Ponto de Entrada do Smart Assistant
==============================================
Modos de execução:

    python main.py                # MODO INTERATIVO (conversação)
    python main.py --eval         # MODO AVALIAÇÃO (roda datasets de teste)
    python main.py --eval --offline   # idem, sem precisar de Ollama rodando

Variáveis de ambiente úteis:
    OLLAMA_HOST   (default: http://localhost:11434)
    OLLAMA_MODEL  (default: gpt-oss:120b)
    LLM_OFFLINE   ("1" = usa mock determinístico)

Domínio: TechStore Brasil (Smart Support — atendimento de e-commerce).
Grupo:  Grupo 02 — FIAP 2026 — Checkpoint 03.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# Garante import do pacote src/
sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.chain import AssistantChain                    # noqa: E402
from src.evaluator import Evaluator                     # noqa: E402
from src.guardrails import GuardrailSystem              # noqa: E402
from src.llm_client import OllamaClient                 # noqa: E402


BANNER = r"""
╔══════════════════════════════════════════════════════════╗
║  SMART ASSISTANT — TechStore Brasil                      ║
║  Ana, analista de CX — FIAP CP03 — Grupo 02              ║
╚══════════════════════════════════════════════════════════╝
"""


def build_chain(offline: bool) -> AssistantChain:
    if offline:
        os.environ["LLM_OFFLINE"] = "1"
    llm = OllamaClient()
    guard = GuardrailSystem()
    return AssistantChain(llm=llm, guard=guard)


def modo_interativo(offline: bool) -> None:
    print(BANNER)
    print("Digite sua mensagem (ou 'sair' para encerrar).")
    if offline:
        print("[modo offline ativo — respostas mockadas para testes]")
    print()

    chain = build_chain(offline)

    while True:
        try:
            msg = input("👤 Você: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nEncerrando.")
            return

        if not msg or msg.lower() in {"sair", "exit", "quit"}:
            print("Até logo!")
            return

        out = chain.run(msg)

        if out.bloqueado:
            print(f"🛡  [BLOQUEADO] {out.motivo_bloqueio}\n")
            continue

        if out.resposta is None:
            print(f"⚠️  Erro no pipeline (etapa: {out.etapa_falha}). Erros: {out.erros}\n")
            continue

        print(f"🤖 Ana: {out.resposta.resposta}")
        print(
            f"     ↳ classif={out.classificacao.tipo}/{out.classificacao.urgencia} | "
            f"confiança={out.resposta.confianca} | "
            f"ação={out.resposta.acao_sugerida} | "
            f"latência={out.latencia_total_s:.2f}s | "
            f"tokens={out.tokens_total}\n"
        )


def modo_avaliacao(offline: bool) -> None:
    print(BANNER)
    print("Modo: AVALIAÇÃO AUTOMÁTICA\n")

    chain = build_chain(offline)
    evaluator = Evaluator(chain=chain)
    evaluator.run(verbose=True)

    print()
    print(evaluator.relatorio_texto())
    print()
    print(f"CSV   → output/eval_results.csv")
    print(f"PNGs  → output/graficos/*.png")


def main() -> None:
    parser = argparse.ArgumentParser(description="Smart Assistant — TechStore Brasil")
    parser.add_argument("--eval", action="store_true", help="Roda avaliação automática")
    parser.add_argument("--offline", action="store_true", help="Usa LLM mock (sem Ollama)")
    args = parser.parse_args()

    if args.eval:
        modo_avaliacao(args.offline)
    else:
        modo_interativo(args.offline)


if __name__ == "__main__":
    main()
