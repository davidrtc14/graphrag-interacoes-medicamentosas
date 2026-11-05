# Assistente GraphRAG de interações medicamentosas

Projeto final de Tendências em Ciência da Computação. O sistema responde em português a perguntas sobre interações entre medicamentos, usando um grafo de conhecimento extraído de bulas públicas da openFDA. Cada resposta mostra o subgrafo, os trechos e as consultas que a sustentam.

> Projeto acadêmico. Não substitui a orientação de um profissional de saúde. O sistema não usa dados de pacientes.

## Como funciona

```text
openFDA -> trechos -> extração NER/RE (LLM) -> resolução de entidades -> Neo4j
                                                                            |
Pergunta -> busca vetorial e nomes -> entidades -> travessia Cypher multi-hop
                                                                            |
           Interface <- resposta com citações <- LLM restrito ao subgrafo <-+
```

- **Extração:** uma relação só é aceita se a evidência citada pelo LLM estiver literalmente no trecho.
- **Resposta:** o LLM usa somente a evidência recuperada, e o código recusa citações inexistentes.
- **Abstenção:** sem evidência suficiente, o sistema diz que não encontrou resposta.
- **Declarado e inferido:** um salto é o que a bula afirma. Dois ou mais saltos indicam só um possível mecanismo.
- **Cypher gerado:** roda em transação somente leitura, com validação, limite de linhas e tempo máximo.
- **Confiança:** é uma regra calculada pela evidência citada, não uma probabilidade calibrada.

O modelo do grafo e as decisões estão em [docs/ESPECIFICACAO_E_PLANO.md](docs/ESPECIFICACAO_E_PLANO.md).

## Requisitos

- Python 3.11 ou superior
- Docker Desktop
- Chave gratuita do [Google AI Studio](https://aistudio.google.com) e, como reserva, do [Groq](https://console.groq.com)

## Instalação

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt
.venv/Scripts/python.exe -m pip install -e .
cp .env.example .env
```

No Linux ou macOS, troque `.venv/Scripts/` por `.venv/bin/`. Depois, edite o `.env`: defina `NEO4J_PASSWORD` e preencha as chaves.

```bash
docker compose up -d --wait
```

Os comandos abaixo usam o ambiente virtual ativado, ou `.venv/Scripts/graphrag.exe` no lugar de `graphrag`.

## Construir o grafo

### Com o grafo pronto, sem gastar cota

```bash
graphrag importar
```

Este comando exige o arquivo `data/grafo/grafo.json.gz`, gerado por `graphrag exportar`.

### Do zero

```bash
graphrag baixar
graphrag schema
graphrag extrair --limite 5
graphrag extrair
graphrag carregar
graphrag algoritmos
graphrag exportar
```

O comando `extrair` salva cada bula em `data/extracoes`. Se a cota acabar, basta executá-lo de novo: ele continua de onde parou. Comece com `--limite 5` e revise o resultado antes de processar o restante.

## Usar

```bash
streamlit run app/streamlit_app.py
```

Ou no terminal:

```bash
graphrag perguntar "A claritromicina pode alterar o efeito da sinvastatina?"
```

## Avaliar

```bash
graphrag avaliar
```

O comando compara LLM puro, RAG vetorial e GraphRAG com as 40 perguntas de `eval/perguntas.json` e grava em `eval/resultados`:

- `respostas.jsonl`: todas as respostas; a execução continua de onde parou
- `metricas.json`: resumo por modo
- `revisao.csv`: planilha para o grupo conferir as notas automáticas

## Testar

```bash
docker compose --profile test up -d --wait
pytest
```

Os testes usam um grafo de exemplo, um LLM falso e um Neo4j separado na porta 7688, então não gastam cota nem tocam nos dados reais. Sem esse Neo4j, os testes de integração são ignorados.

Para ver a interface sem chaves, depois de rodar os testes:

```bash
streamlit run tests/app_exemplo.py
```

## Hospedar

1. Crie uma instância no Neo4j Aura Free.
2. Aponte `NEO4J_URI`, `NEO4J_USER` e `NEO4J_PASSWORD` para ela no `.env`.
3. Rode `graphrag importar`.
4. Publique `app/streamlit_app.py` no Streamlit Community Cloud, a partir de um repositório privado.
5. Coloque as variáveis do `.env` nos segredos do app e convide o professor por e-mail.

Abra o app e o Aura antes da apresentação, porque os planos gratuitos pausam por inatividade.

## Limitações

- O recorte cobre 60 medicamentos, não toda a farmacologia.
- As bulas estão em inglês e são dos Estados Unidos.
- Uma inferência do grafo não comprova uma interação clínica.
- A qualidade do grafo depende da extração feita pelo LLM.

## Créditos

Dados: [openFDA](https://open.fda.gov). Bibliotecas: Neo4j Python Driver, Neo4j Graph Data Science, FastEmbed, Streamlit, PyVis, Pydantic, HTTPX, Google Gen AI SDK, Groq SDK e pytest. Embeddings: `paraphrase-multilingual-MiniLM-L12-v2`.
