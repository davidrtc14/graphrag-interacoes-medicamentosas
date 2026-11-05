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

- [Git](https://git-scm.com)
- [Python](https://www.python.org/downloads/) 3.11 ou superior
- [Docker Desktop](https://www.docker.com/products/docker-desktop/) aberto e em execução
- Chave gratuita do [Google AI Studio](https://aistudio.google.com) e, como reserva, do [Groq](https://console.groq.com); basta uma delas para o sistema funcionar

## Como inicializar

O caminho mais rápido usa o grafo pronto do repositório e não gasta cota com a extração. Na primeira execução, o modelo de embeddings e a imagem do Neo4j são baixados automaticamente.

### 1. Baixar o projeto

```bash
git clone https://github.com/davidrtc14/graphrag-interacoes-medicamentosas.git
```

```bash
cd graphrag-interacoes-medicamentosas
```

### 2. Criar o ambiente e instalar as dependências

No Windows (PowerShell):

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m pip install -e .
Copy-Item .env.example .env
```

No Linux ou macOS:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m pip install -e .
cp .env.example .env
```

Se o PowerShell bloquear a ativação, execute `Set-ExecutionPolicy -Scope Process RemoteSigned` e tente de novo.

### 3. Configurar o `.env`

Abra o arquivo `.env` e preencha:

- `NEO4J_PASSWORD`: uma senha sua, com pelo menos 8 caracteres
- `GEMINI_API_KEY` e `GROQ_API_KEY`: suas chaves

O `.env` fica fora do Git. Nunca envie esse arquivo nem suas chaves para o repositório.

### 4. Iniciar o Neo4j

```bash
docker compose up -d --wait
```

O painel do Neo4j fica em <http://localhost:7474>, com o usuário `neo4j` e a senha do `.env`.

### 5. Carregar o grafo pronto

```bash
graphrag importar
```

### 6. Abrir a interface

```bash
streamlit run app/streamlit_app.py
```

A interface abre em <http://localhost:8501>. Para testar pelo terminal:

```bash
graphrag perguntar "A claritromicina pode alterar o efeito da sinvastatina?"
```

### Nas próximas vezes

Abra o Docker Desktop, entre na pasta do projeto, ative o ambiente (passo 2) e execute os passos 4 e 6. O grafo continua salvo no Docker.

Para desligar o Neo4j, use `docker compose down`. Esse comando mantém os dados.

### Problemas comuns

| Problema | O que fazer |
|---|---|
| `graphrag` ou `streamlit` não é reconhecido | Ative o ambiente virtual (passo 2) |
| Erro ao conectar ao Neo4j | Abra o Docker Desktop e repita o passo 4 |
| Falha de autenticação no Neo4j após trocar a senha | A senha fica gravada no primeiro início. Volte à senha anterior ou apague os dados com `docker compose down -v` e repita os passos 4 e 5 |
| "Nenhum provedor configurado" | Preencha ao menos uma chave no `.env` |
| "Todos os provedores falharam" | Os planos gratuitos têm limites. Aguarde e tente de novo |

## Reconstruir o grafo do zero

Este caminho é opcional e consome a cota gratuita do LLM.

```bash
graphrag baixar
graphrag schema
graphrag extrair --limite 5
graphrag extrair
graphrag carregar --limpar
graphrag algoritmos
graphrag exportar
```

O comando `extrair` salva cada bula em `data/extracoes` e não refaz o que já existe. Se a cota acabar, basta executá-lo de novo: ele continua de onde parou. O repositório já traz as extrações das 60 bulas, então, para refazê-las, apague antes os arquivos dessa pasta. A opção `--limpar` apaga a base do Neo4j antes de recarregá-la.

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
