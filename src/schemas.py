"""
schemas.py — Structured Output (Aula 09)
=========================================
Modelos Pydantic que validam o JSON retornado pelo LLM em cada etapa do chain.

Cada etapa do pipeline produz JSON; estes schemas garantem:
- Tipos corretos
- Campos obrigatórios
- Valores dentro do domínio (enums)
- Mensagens de erro detalhadas para retry/fallback
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ---------------------------------------------------------------------------
# Enums — Domínios fechados (impedem alucinações de categoria)
# ---------------------------------------------------------------------------

class TipoSolicitacao(str, Enum):
    RECLAMACAO = "reclamacao"
    DUVIDA = "duvida"
    ELOGIO = "elogio"
    SUGESTAO = "sugestao"


class Urgencia(str, Enum):
    ALTA = "alta"
    MEDIA = "media"
    BAIXA = "baixa"


class Sentimento(str, Enum):
    POSITIVO = "positivo"
    NEGATIVO = "negativo"
    NEUTRO = "neutro"
    MISTO = "misto"


class Confianca(str, Enum):
    ALTA = "alta"
    MEDIA = "media"
    BAIXA = "baixa"


# ---------------------------------------------------------------------------
# Etapa 1 — Classificação
# ---------------------------------------------------------------------------

class ClassificacaoSchema(BaseModel):
    """
    Saída da Etapa 1 do chain: classificação inicial da mensagem.
    Determina o roteamento condicional da Etapa 2.
    """
    model_config = ConfigDict(use_enum_values=True, extra="forbid")

    tipo: TipoSolicitacao = Field(
        ...,
        description="Tipo da solicitação: reclamacao | duvida | elogio | sugestao",
    )
    urgencia: Urgencia = Field(
        ...,
        description="Nível de urgência: alta | media | baixa",
    )
    tema: str = Field(
        ...,
        min_length=2,
        max_length=120,
        description="Tema curto resumindo o assunto (ex.: 'entrega atrasada')",
    )

    @field_validator("tema")
    @classmethod
    def _strip_tema(cls, v: str) -> str:
        return v.strip()


# ---------------------------------------------------------------------------
# Etapa 2 — Processamento (saída varia conforme o tipo da Etapa 1)
# ---------------------------------------------------------------------------

class ProcessamentoSchema(BaseModel):
    """
    Saída da Etapa 2. A estrutura interna de `dados_extraidos` varia conforme
    o tipo classificado na Etapa 1 — isso satisfaz o critério essencial de
    chain condicional (não apenas sequencial).

    Convenções por tipo:
    - reclamacao: { 'produto': str, 'problema': str, 'severidade': str }
    - duvida:     { 'topico': str, 'precisa_humano': bool }
    - elogio:     { 'aspecto_elogiado': str, 'intensidade': str }
    - sugestao:   { 'sugestao': str, 'area_impactada': str }
    """
    model_config = ConfigDict(use_enum_values=True, extra="forbid")

    dados_extraidos: Dict[str, Any] = Field(
        ...,
        description="Dicionário com campos extraídos específicos do tipo",
    )
    analise: str = Field(
        ...,
        min_length=5,
        max_length=600,
        description="Análise curta da mensagem feita pelo assistente",
    )
    sentimento: Optional[Sentimento] = Field(
        default=None,
        description="Sentimento geral detectado (opcional)",
    )

    @field_validator("dados_extraidos")
    @classmethod
    def _no_empty(cls, v: Dict[str, Any]) -> Dict[str, Any]:
        if not v:
            raise ValueError("dados_extraidos não pode ser vazio")
        return v


# ---------------------------------------------------------------------------
# Etapa 3 — Resposta final ao usuário
# ---------------------------------------------------------------------------

class RespostaSchema(BaseModel):
    """
    Saída da Etapa 3: resposta final formatada para o usuário.
    """
    model_config = ConfigDict(use_enum_values=True, extra="forbid")

    resposta: str = Field(
        ...,
        min_length=10,
        max_length=1200,
        description="Mensagem final para o usuário",
    )
    confianca: Confianca = Field(
        ...,
        description="Confiança da resposta: alta | media | baixa",
    )
    acao_sugerida: str = Field(
        ...,
        min_length=3,
        max_length=200,
        description="Próximo passo recomendado (ex.: 'abrir ticket #devolucao')",
    )


# ---------------------------------------------------------------------------
# Resultado consolidado do pipeline
# ---------------------------------------------------------------------------

class PipelineResult(BaseModel):
    """
    Envelopa o resultado completo de uma execução do pipeline,
    incluindo metadados úteis para o evaluator.
    """
    model_config = ConfigDict(extra="forbid")

    classificacao: Optional[ClassificacaoSchema] = None
    processamento: Optional[ProcessamentoSchema] = None
    resposta: Optional[RespostaSchema] = None

    bloqueado: bool = False
    motivo_bloqueio: Optional[str] = None
    etapa_falha: Optional[str] = None
    erros: List[str] = Field(default_factory=list)

    latencia_total_s: float = 0.0
    tokens_total: int = 0
