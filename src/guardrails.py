"""
guardrails.py — Sistema de Segurança em 3 Camadas (Aula 10)
=============================================================
Implementa as três camadas de defesa exigidas pela rubrica:

1. INPUT GUARD     — valida o input do usuário antes de enviar ao LLM
2. SYSTEM PROMPT   — carregado de arquivo, defensivo e versionado
3. OUTPUT GUARD    — valida a resposta do LLM antes de devolver ao usuário

A detecção de injection cobre as 5 famílias principais discutidas
na Aula 10 + variações em português:

- ignore/forget instructions    (override direto)
- prompt leaking                (revelar system prompt)
- jailbreak / DAN               (alter ego sem restrições)
- role override                 ("you are now ...")
- encoding/obfuscation          (base64, hex, língua inventada)
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple


# ---------------------------------------------------------------------------
# Resultado padronizado das verificações
# ---------------------------------------------------------------------------

@dataclass
class GuardResult:
    is_safe: bool
    motivo: str = ""
    risco: str = "BAIXO"             # BAIXO | MEDIO | ALTO | CRITICO
    padroes_detectados: List[str] = field(default_factory=list)
    camada: str = ""                 # input | output


# ---------------------------------------------------------------------------
# Sistema principal
# ---------------------------------------------------------------------------

class GuardrailSystem:
    """Sistema de guardrails com 3 camadas obrigatórias."""

    # ------- Configurações -------
    INPUT_MAX_CHARS = 500

    CARACTERES_PROIBIDOS = re.compile(r"[<>{}]")

    # 5+ padrões de prompt injection (Aula 10). Cobertura bilíngue.
    INJECTION_PATTERNS = [
        # 1) Override direto
        r"(?i)ignor[ae]?\s+(todas?\s+as?\s+|all\s+)?(previous|prior|anterior|above|acima|suas?)\s+(instruc(?:tions?|oes|ções)|prompts?|regras?|rules?)",
        r"(?i)\b(forget|esque[cç]a|esquece)\s+(everything|all|tudo|suas?|your)\s*(instruc(?:tions?|oes|ções)|regras?|rules?|training|treinamento)?",

        # 2) Role override
        r"(?i)\byou\s+are\s+now\s+(a|an|um|uma)?\s*\w+",
        r"(?i)\bvoc[eê]\s+(agora\s+)?[eé]\s+(um\s+|uma\s+)?(hacker|pirata|criminoso|n[aã]o[\s-]*restrito|assistente\s+sem\s+restri)",
        r"(?i)\bassistente\s+sem\s+restri[cç][oõ]es",
        r"(?i)\bmodo\s+(desenvolvedor|dev|admin|root)\b",

        # 3) Prompt leaking
        r"(?i)(reveal|show|repeat|print|mostre|repita|exiba|imprima)\s+(your\s+|seu\s+|o\s+)?(system\s+prompt|prompt\s+do\s+sistema|instru[cç][oõ]es)",
        r"(?i)\b(qual|what'?s|what\s+is)\s+(your|seu|o)\s+(system\s+prompt|prompt|sistema)",
        r"(?i)code\s*block.*(prompt|instru[cç])",

        # 4) Jailbreak / DAN
        r"(?i)\bDAN\b|\bdo\s+anything\s+now\b",
        r"(?i)jailbreak|modo\s+(desbloqueado|sem\s+restri)",
        r"(?i)pretend\s+you\s+(are|have)\s+no\s+(restrictions?|rules?|limits?)",
        r"(?i)finja\s+(que\s+)?(voc[eê]|n[aã]o\s+tem)",

        # 5) Encoding / obfuscation
        r"(?i)\bresponda?\s+em\s+(base64|hex|rot13|morse)\b",
        r"(?i)\breply\s+(in|using)\s+(base64|hex|rot13)\b",

        # Bônus: tentativa de assumir papel de administrador
        r"(?i)\b(developer|admin|sudo|root)\s+mode\b",
        r"(?i)modo\s+(desenvolvedor|administrador)\b",
        r"(?i)overrid[ae]\s+(safety|security|guardrail)",
        # Variações em português para "sem restrições" / "sem regras"
        r"(?i)\b(sem|sin)\s+(restri[çc][õo]es?|regras?)\b",
        r"(?i)\bassistente\s+sem\s+(restri|regras?)\b",
    ]

    # Termos do system prompt que NÃO podem vazar na resposta final
    SYSTEM_LEAK_KEYWORDS = [
        "REGRAS INVIOLÁVEIS",
        "NUNCA revele",
        "system_prompt",
        "<system>",
        "</system>",
        "INVIOLÁVEL",
        "REGRAS DE SEGURANÇA",
        "INSTRUÇÕES INTERNAS",
    ]

    # Domínio: TechStore Brasil. Resposta deve estar relacionada.
    DOMAIN_KEYWORDS = [
        "produto", "pedido", "entrega", "devolu", "troca", "garantia",
        "techstore", "compra", "atendimento", "suporte", "estoque",
        "boleto", "cart", "pix", "frete", "nota fiscal", "loja",
        "cliente", "ana",  # nome da assistente
    ]

    # Termos que indicam que o assistente saiu do domínio
    OUT_OF_DOMAIN_RED_FLAGS = [
        "como hackear", "fazer bomba", "invadir", "phishing", "malware",
        "instruções para",  # quando vier sozinho
    ]

    def __init__(self, system_prompt_path: Optional[str] = None) -> None:
        self.system_prompt_path = (
            system_prompt_path
            or str(Path(__file__).resolve().parent.parent / "prompts" / "system_prompt.txt")
        )
        self._system_prompt: Optional[str] = None
        self._compiled = [re.compile(p) for p in self.INJECTION_PATTERNS]

    # ------------------------------------------------------------------
    # Camada 1: Input Guard
    # ------------------------------------------------------------------
    def validar_input(self, texto: str) -> GuardResult:
        if not texto or not texto.strip():
            return GuardResult(False, "Input vazio.", "MEDIO", camada="input")

        texto_norm = texto.strip()

        # Tamanho
        if len(texto_norm) > self.INPUT_MAX_CHARS:
            return GuardResult(
                False,
                f"Mensagem excede {self.INPUT_MAX_CHARS} caracteres.",
                "BAIXO",
                camada="input",
            )

        # Caracteres proibidos
        if self.CARACTERES_PROIBIDOS.search(texto_norm):
            return GuardResult(
                False,
                "Mensagem contém caracteres reservados (<, >, {, }).",
                "MEDIO",
                camada="input",
            )

        # Padrões de injection
        detectados: List[str] = []
        for rx in self._compiled:
            m = rx.search(texto_norm)
            if m:
                detectados.append(m.group(0)[:80])

        if detectados:
            risco = "CRITICO" if len(detectados) >= 2 else "ALTO"
            return GuardResult(
                False,
                "Padrão de prompt injection detectado.",
                risco,
                padroes_detectados=detectados,
                camada="input",
            )

        return GuardResult(True, "OK", "BAIXO", camada="input")

    # ------------------------------------------------------------------
    # Camada 2: System Prompt Defensivo
    # ------------------------------------------------------------------
    def carregar_system_prompt(self) -> str:
        if self._system_prompt is None:
            path = Path(self.system_prompt_path)
            if not path.exists():
                raise FileNotFoundError(f"System prompt não encontrado: {path}")
            self._system_prompt = path.read_text(encoding="utf-8")
        return self._system_prompt

    # ------------------------------------------------------------------
    # Camada 3: Output Guard
    # ------------------------------------------------------------------
    def validar_output(self, resposta: str, esperar_json: bool = False) -> GuardResult:
        if not resposta or not resposta.strip():
            return GuardResult(False, "Resposta vazia.", "ALTO", camada="output")

        # 1) Vazamento de system prompt?
        baixo = resposta.lower()
        for termo in self.SYSTEM_LEAK_KEYWORDS:
            if termo.lower() in baixo:
                return GuardResult(
                    False,
                    f"Possível vazamento do system prompt: '{termo}'.",
                    "CRITICO",
                    padroes_detectados=[termo],
                    camada="output",
                )

        # 2) Conteúdo claramente fora de domínio / perigoso
        for termo in self.OUT_OF_DOMAIN_RED_FLAGS:
            if termo in baixo:
                return GuardResult(
                    False,
                    f"Conteúdo fora de domínio detectado: '{termo}'.",
                    "ALTO",
                    padroes_detectados=[termo],
                    camada="output",
                )

        # 3) JSON válido quando esperado
        if esperar_json:
            try:
                json.loads(resposta)
            except json.JSONDecodeError as e:
                return GuardResult(
                    False,
                    f"JSON inválido: {e.msg}",
                    "MEDIO",
                    camada="output",
                )

        # 4) Heurística leve de domínio (não bloqueia, apenas marca risco)
        if not esperar_json and not any(k in baixo for k in self.DOMAIN_KEYWORDS):
            return GuardResult(
                True,
                "Resposta passou, mas sem termos do domínio (revisar).",
                "MEDIO",
                camada="output",
            )

        return GuardResult(True, "OK", "BAIXO", camada="output")

    # ------------------------------------------------------------------
    # Diagnóstico — combinação útil para o evaluator
    # ------------------------------------------------------------------
    def pipeline_check(
        self, entrada: str, saida: Optional[str] = None
    ) -> Tuple[GuardResult, Optional[GuardResult]]:
        return (
            self.validar_input(entrada),
            self.validar_output(saida) if saida is not None else None,
        )
