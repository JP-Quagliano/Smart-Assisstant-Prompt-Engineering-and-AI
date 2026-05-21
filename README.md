# Smart Assistant — TechStore Brasil

> **FIAP — Prompt Engineering & Artificial Intelligence**
> **Checkpoint 03 — Módulo 3 (Aulas 09 a 11)**
> **Grupo 02**

Assistente inteligente de Customer Experience da **TechStore Brasil** construído em Python puro com pipeline multi-etapa (prompt chaining condicional), structured output validado com Pydantic, três camadas de guardrails contra prompt injection e aplicação do **Persona Pattern** + **Template Pattern** + **Recipe Pattern**.

A persona oficial do assistente é **Ana**, analista sênior de CX com 12 anos de experiência.

---

## 1. Arquitetura

```
Input usuário
     │
     ▼
🛡  Camada 1: Input Guard       (guardrails.py)
     │
     ▼
🔗 Etapa 1: Classificar          → ClassificacaoSchema (Pydantic)
     │
     ▼
🔗 Etapa 2: Processar (CONDICIONAL conforme tipo)  → ProcessamentoSchema
     │
     ▼
🔗 Etapa 3: Responder            → RespostaSchema
     │
     ▼
🛡  Camada 3: Output Guard
     │
     ▼
Resposta JSON final
```

A **Camada 2 (System Prompt Defensivo)** atua durante as etapas 1, 2 e 3 — é injetada em toda chamada ao LLM.

---

## 2. Estrutura do Repositório

```
smart-assistant/
├── README.md
├── requirements.txt
├── .env.example
├── main.py                        # Ponto de entrada (interativo + eval)
├── src/
│   ├── __init__.py
│   ├── llm_client.py              # Cliente Ollama (gpt-oss:120b)
│   ├── guardrails.py              # 3 camadas de segurança (Aula 10)
│   ├── chain.py                   # Pipeline multi-etapa (Aula 09)
│   ├── schemas.py                 # Modelos Pydantic (Aula 09)
│   ├── prompts.py                 # Frameworks/patterns (Aula 11)
│   └── evaluator.py               # Avaliação automática (Aula 09)
├── prompts/
│   ├── system_prompt.txt          # System prompt ativo (cópia da V3)
│   └── versions/                  # Histórico V1 → V2 → V3
│       ├── v1.txt
│       ├── v2.txt
│       └── v3.txt
├── data/
│   ├── test_dataset.json          # 15 perguntas legítimas
│   └── attack_dataset.json        # 6 ataques de injection/jailbreak
├── output/
│   ├── eval_results.csv           # Gerado por main.py --eval
│   └── graficos/                  # Gerados por main.py --eval
│       ├── 01_metricas_principais.png
│       ├── 02_matriz_classificacao.png
│       ├── 03_bloqueio_por_categoria.png
│       └── 04_latencia.png
└── docs/
    └── CP03_Grupo02.pdf           # Documento de entrega
```

---

## 3. Stack Técnica

| Componente | Tecnologia |
|---|---|
| Linguagem | Python 3.10+ |
| LLM | Ollama API local — modelo `gpt-oss:120b` |
| Validação | `pydantic` >= 2.5 |
| Tokenização | `tiktoken` (proxy cl100k_base) |
| Análise | `pandas` + `matplotlib` |
| HTTP | `requests` |

> Nenhuma API paga é utilizada — conforme exigência do CP03.

---

## 4. Instalação

```bash
# 1. Clonar o repositório
git clone <URL_DO_REPOSITORIO>
cd smart-assistant

# 2. Criar e ativar venv
python -m venv .venv
source .venv/bin/activate      # Linux/macOS
# .venv\Scripts\activate       # Windows

# 3. Instalar dependências
pip install -r requirements.txt

# 4. Copiar arquivo de configuração
cp .env.example .env
```

### 4.1 Ollama (obrigatório para uso em produção)

```bash
# Instalar Ollama: https://ollama.com/download
ollama pull gpt-oss:120b
ollama serve     # roda em http://localhost:11434
```

Para desenvolvimento sem Ollama, basta usar `--offline` (descrito abaixo) — o cliente retorna respostas mockadas determinísticas.

---

## 5. Execução

### 5.1 Modo interativo

```bash
python main.py
```

```
👤 Você: Meu pedido 12345 está atrasado há 10 dias!
🤖 Ana: Olá! Lamento muito pelo atraso do seu pedido 12345...
        ↳ classif=reclamacao/alta | confiança=alta | ação=abrir ticket de logística | latência=4.21s | tokens=512
```

### 5.2 Modo avaliação automática

```bash
python main.py --eval
```

Roda os 15 casos legítimos + 6 ataques, calcula as 5 métricas, salva CSV em `output/eval_results.csv` e gera 4 gráficos em `output/graficos/`.

### 5.3 Modo offline (sem Ollama)

```bash
python main.py --eval --offline
# ou:
LLM_OFFLINE=1 python main.py --eval
```

---

## 6. Componentes Principais

### 6.1 Pipeline (`src/chain.py`)

Classe `AssistantChain` com 3 etapas, todas validadas via Pydantic:

| Etapa | Entrada | Saída | Lógica |
|---|---|---|---|
| `etapa1_classificar` | texto livre | `ClassificacaoSchema` | tipo + urgência + tema |
| `etapa2_processar` | classif + texto | `ProcessamentoSchema` | **condicional ao tipo** |
| `etapa3_responder` | tudo acima | `RespostaSchema` | resposta + confiança + ação |

A etapa 2 é **condicional**: o prompt e o sub-schema interno mudam conforme `classificacao.tipo`. Esse é o critério essencial da rubrica.

### 6.2 Guardrails (`src/guardrails.py`)

Classe `GuardrailSystem` com 3 camadas:

1. **Input Guard** — tamanho (≤ 500 chars), caracteres proibidos (`<>{}`), **15+ padrões regex** de prompt injection cobrindo override, leaking, jailbreak, role override e encoding.
2. **System Prompt Defensivo** — `prompts/system_prompt.txt` com persona, 6 regras invioláveis, separação dados/instruções por tags XML, *sandwich defense* (regras no início e no fim) e fallback explícito.
3. **Output Guard** — verifica vazamento do system prompt, conteúdo fora de domínio e validade JSON quando esperado.

### 6.3 Frameworks de Prompt (`src/prompts.py`)

Patterns aplicados (Aula 11):
- **Persona Pattern** — "Ana, analista sênior de CX com 12 anos na TechStore"
- **Template Pattern** — placeholders dinâmicos `{tipo}`, `{urgencia}`, `{tema}`, `{mensagem}`
- **Recipe Pattern** — instruções em formato de receita: "1. Leia... 2. Determine... 3. Resuma..."

---

## 7. Datasets

- `data/test_dataset.json` — **15** mensagens legítimas com `tipo_esperado`, `urgencia_esperada` e `palavras_chave` por caso.
- `data/attack_dataset.json` — **6** ataques cobrindo: prompt injection direta, prompt leaking, jailbreak/DAN, role override, encoding (base64) e tentativa de exfiltração de dados.

---

## 8. Métricas Reportadas

| # | Métrica | Cálculo |
|---|---|---|
| 1 | Acurácia de classificação | acertos de `tipo` / casos legítimos não bloqueados |
| 2 | Taxa de JSON válido | casos que passaram em todas as etapas Pydantic / casos não-bloqueio |
| 3 | Taxa de bloqueio de ataques | ataques bloqueados / total de ataques |
| 4 | Taxa de falso positivo | legítimos bloqueados / total de legítimos |
| 5 | Consistência | mesma classificação em 3 execuções repetidas (top-3 casos) |

---

## 9. Exemplos de Uso Programático

```python
from src import AssistantChain, OllamaClient, GuardrailSystem

chain = AssistantChain(
    llm=OllamaClient(),
    guard=GuardrailSystem(),
)

result = chain.run("Meu monitor chegou com pixel queimado!")
print(result.classificacao.tipo)     # 'reclamacao'
print(result.classificacao.urgencia) # 'alta'
print(result.resposta.resposta)      # texto final ao cliente
```

---

## 10. Grupo

 RM | Nome 

Joao Pedro do Vale Quagliano - RM 570233
Leticia Aiko Okano           - RM 571988
Thiago Calazans Luz Nakano   - RM 569151
Enzo Scattolin Furtado       - RM 570824
Guilherme De Lucena Fontes   - RM 569658
Matheus Levi Dagel           - RM 571961

---

## 11. Licença

Projeto acadêmico — FIAP 2026. Uso restrito à disciplina Prompt Engineering & AI.
