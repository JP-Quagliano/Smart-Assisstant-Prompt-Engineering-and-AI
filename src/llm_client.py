"""
llm_client.py — Conexão com Ollama (gpt-oss:120b)
==================================================
Cliente HTTP simples para a API local do Ollama. Sem dependência de SDK
externo (apenas `requests`). Inclui:

- Retry com backoff exponencial
- Timeout configurável
- Contagem de tokens com tiktoken (aproximada, já que o tokenizer do
  gpt-oss não é exposto publicamente; usamos cl100k_base como proxy)
- Modo offline para testes (Ollama desligado) — devolve resposta
  determinística para que a pipeline rode sem rede
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import requests

try:
    import tiktoken
    _ENCODING = tiktoken.get_encoding("cl100k_base")
except Exception:  # pragma: no cover
    _ENCODING = None


@dataclass
class LLMResponse:
    """Resposta padronizada do LLM com metadados."""
    content: str
    latencia_s: float
    tokens_prompt: int
    tokens_response: int
    tokens_total: int = field(init=False)
    model: str = ""
    erro: Optional[str] = None

    def __post_init__(self) -> None:
        self.tokens_total = self.tokens_prompt + self.tokens_response


def _count_tokens(text: str) -> int:
    """Contagem de tokens (aproximação cl100k_base)."""
    if not text:
        return 0
    if _ENCODING is None:
        # Fallback grosseiro: ~4 chars por token
        return max(1, len(text) // 4)
    return len(_ENCODING.encode(text))


class OllamaClient:
    """
    Wrapper para chamadas à API Ollama (/api/generate).

    Variáveis de ambiente reconhecidas:
        OLLAMA_HOST   — endpoint base (default: http://localhost:11434)
        OLLAMA_MODEL  — modelo (default: gpt-oss:120b)
        LLM_OFFLINE   — se "1", devolve resposta mock para CI/avaliação local
    """

    def __init__(
        self,
        host: Optional[str] = None,
        model: Optional[str] = None,
        timeout_s: int = 120,
        max_retries: int = 2,
        offline: Optional[bool] = None,
    ) -> None:
        self.host = (host or os.getenv("OLLAMA_HOST", "http://localhost:11434")).rstrip("/")
        self.model = model or os.getenv("OLLAMA_MODEL", "gpt-oss:120b")
        self.timeout_s = timeout_s
        self.max_retries = max_retries
        self.offline = bool(int(os.getenv("LLM_OFFLINE", "0"))) if offline is None else offline

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------
    def generate(
        self,
        prompt: str,
        system: Optional[str] = None,
        temperature: float = 0.2,
        max_tokens: int = 600,
        force_json: bool = False,
    ) -> LLMResponse:
        """Chamada principal de geração. Em modo offline devolve mock."""
        if self.offline:
            return self._mock_response(prompt, system, force_json)

        full_prompt = self._build_prompt(prompt, system)
        payload: Dict[str, Any] = {
            "model": self.model,
            "prompt": full_prompt,
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
        }
        if force_json:
            payload["format"] = "json"

        return self._post_with_retry(payload, prompt, system)

    # ------------------------------------------------------------------
    # Internos
    # ------------------------------------------------------------------
    def _build_prompt(self, prompt: str, system: Optional[str]) -> str:
        if system:
            return f"<system>\n{system}\n</system>\n\n<user>\n{prompt}\n</user>"
        return prompt

    def _post_with_retry(
        self,
        payload: Dict[str, Any],
        raw_prompt: str,
        system: Optional[str],
    ) -> LLMResponse:
        url = f"{self.host}/api/generate"
        last_error: Optional[str] = None
        prompt_tokens = _count_tokens(raw_prompt) + _count_tokens(system or "")

        for attempt in range(self.max_retries + 1):
            t0 = time.time()
            try:
                r = requests.post(url, json=payload, timeout=self.timeout_s)
                r.raise_for_status()
                data = r.json()
                content = data.get("response", "").strip()
                return LLMResponse(
                    content=content,
                    latencia_s=time.time() - t0,
                    tokens_prompt=prompt_tokens,
                    tokens_response=_count_tokens(content),
                    model=self.model,
                )
            except requests.exceptions.RequestException as e:
                last_error = f"{type(e).__name__}: {e}"
                if attempt < self.max_retries:
                    time.sleep(1.5 ** attempt)

        return LLMResponse(
            content="",
            latencia_s=0.0,
            tokens_prompt=prompt_tokens,
            tokens_response=0,
            model=self.model,
            erro=last_error,
        )

    # ------------------------------------------------------------------
    # Mock determinístico (modo offline) — útil para testes em CI
    # ------------------------------------------------------------------
    def _mock_response(
        self, prompt: str, system: Optional[str], force_json: bool
    ) -> LLMResponse:
        """
        Resposta heurística baseada em marcadores do prompt.

        NÃO É O COMPORTAMENTO DE PRODUÇÃO — serve apenas para que a pipeline
        rode sem Ollama. O evaluator deve ser executado com Ollama ativo
        (gpt-oss:120b) para gerar métricas reais.

        Detecção das etapas:
          - Etapa 1 (classificacao): prompt contém '"tipo": "reclamacao|duvida'
          - Etapa 2 (processamento): prompt contém '"dados_extraidos"'
          - Etapa 3 (resposta):      prompt contém '"acao_sugerida"'
        """
        import time as _t
        import json as _j
        t0 = _t.time()
        p_low = prompt.lower()

        # Extrai a mensagem do cliente (vem entre <mensagem>...</mensagem>).
        # Usamos rfind porque o prompt também MENCIONA essas tags na receita.
        msg_cliente = ""
        if "<mensagem>" in p_low and "</mensagem>" in p_low:
            i = p_low.rfind("<mensagem>") + len("<mensagem>")
            j = p_low.rfind("</mensagem>")
            if j > i:
                msg_cliente = prompt[i:j].lower()
        m = msg_cliente

        # ----------- ETAPA 3: RESPOSTA (mais específico — testar primeiro) -----------
        # O marker '"acao_sugerida"' aparece APENAS no schema da etapa 3.
        if '"acao_sugerida"' in prompt:
            tipo_atual = self._extrair_tipo_do_prompt(p_low)
            payload = self._heuristica_responder(tipo_atual)
            return LLMResponse(
                content=_j.dumps(payload),
                latencia_s=_t.time() - t0 + 0.05,
                tokens_prompt=_count_tokens(prompt) + _count_tokens(system or ""),
                tokens_response=60,
                model=f"{self.model} [MOCK]",
            )

        # ----------- ETAPA 2: PROCESSAMENTO -----------
        # '"dados_extraidos"' aparece tanto na etapa 2 quanto no contexto da
        # etapa 3, por isso testamos a etapa 3 antes.
        if '"dados_extraidos"' in prompt:
            tipo_atual = self._extrair_tipo_do_prompt(p_low)
            payload = self._heuristica_processar(tipo_atual, m)
            return LLMResponse(
                content=_j.dumps(payload),
                latencia_s=_t.time() - t0 + 0.04,
                tokens_prompt=_count_tokens(prompt) + _count_tokens(system or ""),
                tokens_response=40,
                model=f"{self.model} [MOCK]",
            )

        # ----------- ETAPA 1: CLASSIFICAÇÃO -----------
        if '"tipo":' in prompt and '"urgencia":' in prompt and '"tema":' in prompt:
            tipo, urg, tema = self._heuristica_classificar(m)
            return LLMResponse(
                content=_j.dumps({"tipo": tipo, "urgencia": urg, "tema": tema}),
                latencia_s=_t.time() - t0 + 0.03,
                tokens_prompt=_count_tokens(prompt) + _count_tokens(system or ""),
                tokens_response=20,
                model=f"{self.model} [MOCK]",
            )

        # Fallback genérico
        return LLMResponse(
            content=_j.dumps({"resposta": "Olá, sou a Ana da TechStore. Como posso te ajudar?",
                              "confianca": "alta", "acao_sugerida": "saudar cliente"}),
            latencia_s=_t.time() - t0 + 0.02,
            tokens_prompt=_count_tokens(prompt) + _count_tokens(system or ""),
            tokens_response=30,
            model=f"{self.model} [MOCK]",
        )

    # ------------------------------------------------------------------
    # Heurísticas auxiliares do mock
    # ------------------------------------------------------------------
    @staticmethod
    def _heuristica_classificar(msg: str):
        """Retorna (tipo, urgencia, tema) por palavras-chave."""
        # Ordem importa: reclamação > elogio > sugestão > dúvida
        recl = ["atrasad", "atrasou", "defeito", "trincad", "quebrad", "errado",
                "estragad", "péssim", "pessim", "irritad", "reclama", "pixel",
                "não chegou", "nao chegou", "veio errad", "produto errad"]
        elog = ["adorei", "excelente", "parabéns", "parabens", "superou",
                "recomendo", "extremamente educad", "atencios", "ótim", "otim"]
        sug = ["sugiro", "sugest", "poderiam", "deveria", "acho que o", "acho que vocês",
               "vocês poderiam", "voces poderiam", "considere adicionar", "ampliar"]

        if any(k in msg for k in recl):
            tipo, urg = "reclamacao", "alta"
        elif any(k in msg for k in elog):
            tipo, urg = "elogio", "baixa"
        elif any(k in msg for k in sug):
            tipo, urg = "sugestao", "baixa"
        else:
            tipo, urg = "duvida", "media"

        # Tema curto
        if "entrega" in msg or "atrasad" in msg:
            tema = "entrega/logistica"
        elif "devolu" in msg or "troca" in msg:
            tema = "troca/devolucao"
        elif "garantia" in msg:
            tema = "garantia"
        elif "atendimento" in msg:
            tema = "atendimento"
        elif "produto" in msg or "notebook" in msg or "monitor" in msg:
            tema = "produto"
        elif "frete" in msg:
            tema = "frete"
        elif "pix" in msg or "pagamento" in msg:
            tema = "pagamento"
        else:
            tema = "geral"

        return tipo, urg, tema

    @staticmethod
    def _extrair_tipo_do_prompt(p_low: str) -> str:
        """Lê o tipo classificado a partir do prompt da Etapa 2 ou 3."""
        for t in ("reclamacao", "duvida", "elogio", "sugestao"):
            marker = f"classificada como **{t}**"
            if marker in p_low:
                return t
            marker2 = f'"tipo": "{t}"'
            if marker2 in p_low:
                return t
        return "duvida"

    @staticmethod
    def _heuristica_processar(tipo: str, msg: str):
        if tipo == "reclamacao":
            return {
                "dados_extraidos": {
                    "produto": "produto mencionado",
                    "problema": "problema descrito pelo cliente",
                    "severidade": "alta",
                },
                "analise": "Cliente apresenta reclamação clara que demanda solução rápida.",
                "sentimento": "negativo",
            }
        if tipo == "elogio":
            return {
                "dados_extraidos": {
                    "aspecto_elogiado": "atendimento e produto",
                    "intensidade": "alta",
                },
                "analise": "Cliente satisfeito, feedback positivo registrado.",
                "sentimento": "positivo",
            }
        if tipo == "sugestao":
            return {
                "dados_extraidos": {
                    "sugestao": "melhoria proposta pelo cliente",
                    "area_impactada": "atendimento e site",
                },
                "analise": "Cliente contribui com sugestão construtiva.",
                "sentimento": "neutro",
            }
        # duvida (default)
        return {
            "dados_extraidos": {
                "topico": "informação operacional solicitada",
                "precisa_humano": False,
            },
            "analise": "Cliente busca informação operacional sobre produtos ou serviços.",
            "sentimento": "neutro",
        }

    @staticmethod
    def _heuristica_responder(tipo: str):
        if tipo == "reclamacao":
            resp = (
                "Olá! Aqui é a Ana, da TechStore Brasil. Lamento muito pelo "
                "ocorrido — entendemos o quanto isso é frustrante. Vamos "
                "tratar a troca/devolução com prioridade. Pode me confirmar "
                "o número do pedido para abrirmos o atendimento agora mesmo?"
            )
            acao = "abrir ticket de troca/devolucao com prioridade alta"
        elif tipo == "elogio":
            resp = (
                "Olá! Aqui é a Ana, da TechStore Brasil. Muito obrigada pelo "
                "seu feedback — vou compartilhar com toda a equipe de atendimento. "
                "Comentários como o seu são o que nos motiva a melhorar sempre."
            )
            acao = "registrar feedback positivo e agradecer cliente"
        elif tipo == "sugestao":
            resp = (
                "Olá! Aqui é a Ana, da TechStore Brasil. Agradeço muito a sua "
                "sugestão — vou encaminhar para o time responsável do site/atendimento. "
                "Vamos considerar com carinho na próxima rodada de melhorias."
            )
            acao = "encaminhar sugestao para time de produto"
        else:  # duvida
            resp = (
                "Olá! Aqui é a Ana, da TechStore Brasil. Posso te ajudar com "
                "essa dúvida sobre produto/entrega/frete/garantia. Vou verificar "
                "as informações e te respondo na sequência. Pode me confirmar mais "
                "detalhes (CEP, número do pedido, modelo)?"
            )
            acao = "responder duvida operacional ao cliente"
        return {"resposta": resp, "confianca": "alta", "acao_sugerida": acao}

    # ------------------------------------------------------------------
    # Utilidades estáticas
    # ------------------------------------------------------------------
    @staticmethod
    def extrair_json(raw: str) -> str:
        """Extrai o primeiro objeto JSON balanceado de uma string."""
        if not raw:
            return ""
        # Caso comum: LLM colocou cercas markdown
        raw = raw.replace("```json", "```").strip()
        if "```" in raw:
            partes = [p for p in raw.split("```") if p.strip()]
            for p in partes:
                if p.strip().startswith("{"):
                    raw = p.strip()
                    break
        # Recortar do primeiro { até o último }
        if "{" in raw and "}" in raw:
            return raw[raw.index("{"): raw.rindex("}") + 1]
        return raw
