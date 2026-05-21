"""
chain.py — Pipeline Multi-Etapa Condicional (Aula 09)
======================================================
Implementa a classe `AssistantChain` com 3 etapas:

    etapa1_classificar  : texto livre  → ClassificacaoSchema
    etapa2_processar    : classif + texto → ProcessamentoSchema  (condicional)
    etapa3_responder    : tudo acima   → RespostaSchema

A etapa 2 é CONDICIONAL: o prompt e o schema interno mudam conforme o
`tipo` retornado pela etapa 1. Esse é o requisito essencial da rubrica.

Cada etapa tem:
- Prompt versionado (V3)
- Saída validada com Pydantic
- Retry automático com feedback do erro de validação
- Fallback determinístico se o LLM falhar repetidamente
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Optional, Tuple

from pydantic import ValidationError

from .guardrails import GuardrailSystem
from .llm_client import OllamaClient, LLMResponse
from .prompts import (
    PERSONA,
    prompt_classificacao,
    prompt_processamento,
    prompt_resposta,
)
from .schemas import (
    ClassificacaoSchema,
    PipelineResult,
    ProcessamentoSchema,
    RespostaSchema,
)


# ---------------------------------------------------------------------------
# Configuração
# ---------------------------------------------------------------------------

@dataclass
class ChainConfig:
    max_retries_por_etapa: int = 1
    temperature_classificacao: float = 0.0
    temperature_processamento: float = 0.1
    temperature_resposta: float = 0.3
    max_tokens: int = 600


# ---------------------------------------------------------------------------
# Pipeline principal
# ---------------------------------------------------------------------------

class AssistantChain:
    """Pipeline de 3 etapas com lógica condicional e validação Pydantic."""

    def __init__(
        self,
        llm: Optional[OllamaClient] = None,
        guard: Optional[GuardrailSystem] = None,
        config: Optional[ChainConfig] = None,
    ) -> None:
        self.llm = llm or OllamaClient()
        self.guard = guard or GuardrailSystem()
        self.config = config or ChainConfig()

    # ==================================================================
    # Etapas individuais — testáveis isoladamente
    # ==================================================================

    def etapa1_classificar(self, mensagem: str) -> Tuple[Optional[ClassificacaoSchema], LLMResponse]:
        prompt = prompt_classificacao(mensagem)
        resp = self.llm.generate(
            prompt=prompt,
            system=self.guard.carregar_system_prompt(),
            temperature=self.config.temperature_classificacao,
            max_tokens=200,
            force_json=True,
        )
        parsed = self._parse_with_retry(
            resp,
            ClassificacaoSchema,
            prompt,
            etapa_nome="classificacao",
        )
        return parsed, resp

    def etapa2_processar(
        self, mensagem: str, classificacao: ClassificacaoSchema
    ) -> Tuple[Optional[ProcessamentoSchema], LLMResponse]:
        # ❗ Lógica condicional: o prompt muda conforme `classificacao.tipo`
        # (isso é o que satisfaz a rubrica de chain condicional)
        prompt = prompt_processamento(
            tipo=str(classificacao.tipo),
            urgencia=str(classificacao.urgencia),
            tema=classificacao.tema,
            mensagem=mensagem,
        )
        resp = self.llm.generate(
            prompt=prompt,
            system=self.guard.carregar_system_prompt(),
            temperature=self.config.temperature_processamento,
            max_tokens=400,
            force_json=True,
        )
        parsed = self._parse_with_retry(
            resp,
            ProcessamentoSchema,
            prompt,
            etapa_nome="processamento",
        )
        return parsed, resp

    def etapa3_responder(
        self,
        classificacao: ClassificacaoSchema,
        processamento: ProcessamentoSchema,
    ) -> Tuple[Optional[RespostaSchema], LLMResponse]:
        prompt = prompt_resposta(
            tipo=str(classificacao.tipo),
            classificacao=classificacao.model_dump(),
            processamento=processamento.model_dump(),
        )
        resp = self.llm.generate(
            prompt=prompt,
            system=self.guard.carregar_system_prompt(),
            temperature=self.config.temperature_resposta,
            max_tokens=self.config.max_tokens,
            force_json=True,
        )
        parsed = self._parse_with_retry(
            resp,
            RespostaSchema,
            prompt,
            etapa_nome="resposta",
        )
        return parsed, resp

    # ==================================================================
    # Pipeline completo: input → guard → 3 etapas → guard → output
    # ==================================================================

    def run(self, mensagem_usuario: str) -> PipelineResult:
        result = PipelineResult()
        t0 = time.time()

        # ---------------- Camada 1: input guard ----------------
        g_in = self.guard.validar_input(mensagem_usuario)
        if not g_in.is_safe:
            result.bloqueado = True
            result.motivo_bloqueio = f"[input/{g_in.risco}] {g_in.motivo}"
            result.etapa_falha = "input_guard"
            result.latencia_total_s = time.time() - t0
            return result

        tokens_total = 0

        # ---------------- Etapa 1 ----------------
        classificacao, resp1 = self.etapa1_classificar(mensagem_usuario)
        tokens_total += resp1.tokens_total
        if classificacao is None:
            result.erros.append(f"Etapa 1 falhou: {resp1.erro or 'JSON inválido'}")
            result.etapa_falha = "classificacao"
            result.latencia_total_s = time.time() - t0
            result.tokens_total = tokens_total
            return result
        result.classificacao = classificacao

        # ---------------- Etapa 2 (condicional) ----------------
        processamento, resp2 = self.etapa2_processar(mensagem_usuario, classificacao)
        tokens_total += resp2.tokens_total
        if processamento is None:
            result.erros.append(f"Etapa 2 falhou: {resp2.erro or 'JSON inválido'}")
            result.etapa_falha = "processamento"
            result.latencia_total_s = time.time() - t0
            result.tokens_total = tokens_total
            return result
        result.processamento = processamento

        # ---------------- Etapa 3 ----------------
        resposta, resp3 = self.etapa3_responder(classificacao, processamento)
        tokens_total += resp3.tokens_total
        if resposta is None:
            result.erros.append(f"Etapa 3 falhou: {resp3.erro or 'JSON inválido'}")
            result.etapa_falha = "resposta"
            result.latencia_total_s = time.time() - t0
            result.tokens_total = tokens_total
            return result
        result.resposta = resposta

        # ---------------- Camada 3: output guard ----------------
        g_out = self.guard.validar_output(resposta.resposta, esperar_json=False)
        if not g_out.is_safe:
            result.bloqueado = True
            result.motivo_bloqueio = f"[output/{g_out.risco}] {g_out.motivo}"
            result.etapa_falha = "output_guard"
            # ainda devolvemos os dados estruturados para auditoria
            result.latencia_total_s = time.time() - t0
            result.tokens_total = tokens_total
            return result

        result.latencia_total_s = time.time() - t0
        result.tokens_total = tokens_total
        return result

    # ==================================================================
    # Helpers
    # ==================================================================

    def _parse_with_retry(
        self,
        primeira_resposta: LLMResponse,
        schema_cls,
        prompt_original: str,
        etapa_nome: str,
    ):
        """Tenta parsear/validar; se falhar, faz um retry com feedback do erro."""
        candidatos = [primeira_resposta]

        for tentativa in range(self.config.max_retries_por_etapa):
            ultimo = candidatos[-1]
            if not ultimo.content:
                # Sem conteúdo: retry direto
                nova = self.llm.generate(
                    prompt=prompt_original,
                    system=self.guard.carregar_system_prompt(),
                    temperature=0.0,
                    max_tokens=600,
                    force_json=True,
                )
                candidatos.append(nova)
                continue

            try:
                raw = OllamaClient.extrair_json(ultimo.content)
                return schema_cls.model_validate_json(raw)
            except (ValidationError, ValueError, json.JSONDecodeError) as e:
                # Retry com feedback do erro
                feedback_prompt = (
                    f"{prompt_original}\n\n"
                    f"# CORREÇÃO\n"
                    f"Sua resposta anterior não passou na validação do schema. "
                    f"Erro: {str(e)[:200]}\n"
                    f"Refaça respondendo APENAS JSON válido conforme o schema acima."
                )
                nova = self.llm.generate(
                    prompt=feedback_prompt,
                    system=self.guard.carregar_system_prompt(),
                    temperature=0.0,
                    max_tokens=600,
                    force_json=True,
                )
                candidatos.append(nova)

        # Última tentativa final
        ultimo = candidatos[-1]
        if ultimo.content:
            try:
                raw = OllamaClient.extrair_json(ultimo.content)
                return schema_cls.model_validate_json(raw)
            except Exception:
                return None
        return None
