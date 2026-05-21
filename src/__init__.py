"""
Smart Assistant — TechStore Brasil
Pacote principal do assistente. Exposição centralizada das classes públicas.
"""

from .schemas import (
    ClassificacaoSchema,
    ProcessamentoSchema,
    RespostaSchema,
    PipelineResult,
)
from .llm_client import OllamaClient
from .guardrails import GuardrailSystem, GuardResult
from .chain import AssistantChain
from .evaluator import Evaluator

__all__ = [
    "ClassificacaoSchema",
    "ProcessamentoSchema",
    "RespostaSchema",
    "PipelineResult",
    "OllamaClient",
    "GuardrailSystem",
    "GuardResult",
    "AssistantChain",
    "Evaluator",
]

__version__ = "1.0.0"
