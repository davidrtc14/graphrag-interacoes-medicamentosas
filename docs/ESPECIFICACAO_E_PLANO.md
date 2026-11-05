# Especificação e plano: assistente GraphRAG de interações medicamentosas

Projeto final de Tendências em Ciência da Computação (prof. Ricardo Roberto de Lima).
**Apresentação:** quarta-feira, 11/11/2026. **Grupo:** 8 pessoas. **Valor:** até 5 pontos.
**Início do desenvolvimento:** quinta-feira, 05/11/2026.

## 1. Decisões

| Tema | Decisão |
|---|---|
| Domínio | Interações medicamentosas: cardiovasculares e os inibidores e indutores enzimáticos que interagem com eles |
| Dados | Bulas públicas da openFDA, sem dados de pacientes |
| Idioma | Perguntas e respostas em português; trechos citados no original, em inglês |
| LLM | Gemini (plano gratuito), com Groq de reserva e Ollama como opção de configuração |
| Embeddings | Modelo multilíngue leve, sem PyTorch em produção, se possível |
| Grafo | Neo4j Community com Graph Data Science no Docker; Neo4j Aura Free na hospedagem |
| Interface | Streamlit com subgrafo interativo |
| Hospedagem | Streamlit Community Cloud, em app privado, como etapa final |
| Custo | Somente ferramentas gratuitas |
| Apresentação | Slides de defesa com demonstração gravada; sistema pronto para rodar ao vivo, se pedirem |

**Fora do escopo:** recomendação clínica, dados de pacientes e cobertura completa de medicamentos. O sistema exibe o aviso de que é um projeto acadêmico e não substitui um profissional de saúde.

## 2. Dados e cota

Amostra medida em 04/11/2026, com 30 medicamentos do recorte:

- Os 30 têm a seção de interações na openFDA.
- 21 citam enzimas CYP na amostra lida, o que sustenta o multi-hop.
- A seção de interações tem em média 6.370 caracteres, ou cerca de 1.600 tokens.

**Escopo inicial: 60 medicamentos.** As 60 bulas foram baixadas em 05/11/2026 e geraram 624 trechos, ou cerca de 140 mil tokens de entrada. A estimativa é de 70 a 80 chamadas e de 250 a 300 mil tokens no total, com instruções e saída. Só ampliar para 100 a 150 se a cota permitir.

Cuidados na ingestão:

- Usar a lista fixa de medicamentos, para manter o grafo conectado.
- Preferir bulas de um único princípio ativo. Na amostra, lisinopril e hidroclorotiazida retornaram textos do mesmo tamanho, o que sugere uma bula combinada.
- Salvar as respostas brutas e as extrações em arquivo, para nunca gastar cota duas vezes.

## 3. Arquitetura

```text
openFDA -> trechos -> extração NER/RE (LLM) -> resolução de entidades -> Neo4j
                                                                            |
Pergunta -> busca vetorial e fulltext -> entidades -> travessia Cypher multi-hop
                                                                            |
           Interface <- resposta com citações <- LLM restrito ao subgrafo <-+
```

### Modelo LPG

- **Nós:** `Medicamento`, `Enzima`, `Efeito`, `Condicao`, `ClasseTerapeutica`, `Bula`, `Trecho`.
- **Relações clínicas:** `INIBE`, `INDUZ`, `E_METABOLIZADO_POR`, `INTERAGE_COM`, `PODE_CAUSAR`, `CONTRAINDICADO_EM`, `PERTENCE_A`.
- **Procedência:** `(:Bula)-[:TEM_TRECHO]->(:Trecho)` e `(:Trecho)-[:MENCIONA]->(entidade)`. Toda relação clínica guarda `trecho_ids`, `modelo` e `confianca_extracao`.
- **Constraints:** unicidade de `id` em cada rótulo e de `nome_canonico` em `Medicamento` e `Enzima`.
- **Índices:** BTree em nomes e identificadores, Fulltext em nomes e sinônimos e Vector em `Trecho.embedding`.

### Regras de resposta

1. O LLM responde somente com o subgrafo recuperado e cita os caminhos e trechos usados.
2. Sem evidência suficiente, o sistema se abstém em vez de responder.
3. A resposta separa **interação declarada na bula** de **interação inferida pelo grafo**. Uma inferência como A → CYP3A4 → B indica um possível mecanismo, não um fato clínico confirmado.
4. Citações são validadas por código: um ID inexistente no subgrafo invalida a resposta.
5. O Cypher gerado pelo LLM roda em transação somente leitura, com validação, limite de resultados e tempo máximo. Se falhar, o sistema usa consultas parametrizadas prontas.
6. O nível de confiança é calculado pela evidência (número de caminhos, similaridade e confiança da extração), não pela autoavaliação do LLM.

PageRank e Louvain são calculados uma vez e salvos como propriedades dos nós, para priorizar o contexto sem depender de uma sessão de análise durante o uso.

### Interface

Campo de pergunta, resposta com citações, subgrafo interativo, nós consultados, Cypher gerado, nível de confiança, trechos originais e aviso acadêmico.

## 4. Avaliação

Comparar **LLM puro**, **RAG vetorial** e **GraphRAG** com cerca de 40 perguntas, escritas com gabarito baseado nas bulas e fixadas antes da execução:

- 15 perguntas diretas
- 15 perguntas multi-hop
- 10 perguntas sem resposta nos dados

**Métricas:** acerto, afirmações sustentadas pela fonte, abstenção correta nas perguntas sem resposta, validade das citações e latência.

Duas pessoas conferem as notas por amostragem. Os números do artigo vêm desta execução, mesmo que sejam desfavoráveis. O relatório também registra as limitações, como o tamanho do conjunto de perguntas.

## 5. Estrutura do repositório

```text
data/        lista de medicamentos, bulas brutas e extrações salvas
src/         ingestão, extração, grafo, recuperação, provedores de LLM
app/         interface Streamlit
eval/        perguntas, gabarito, execução e resultados
tests/       testes operacionais
docs/        esta especificação, artigo, slides e guia da defesa
docker-compose.yml, .env.example, requirements.txt, README.md
```

O arquivo `.env` e as chaves nunca entram no repositório. O grafo pronto é versionado como exportação, para quem for reproduzir não refazer a extração. Bibliotecas são creditadas no `README.md` e no artigo.

## 6. Cronograma

Cada dia termina com um critério verificável. A folga está na segunda e na terça.

| Data | Trabalho | Pronto quando |
|---|---|---|
| **Qui 05/11** | Contas e chaves; Neo4j no Docker; schema; cota real do Gemini; testes da seção 8; extração de 5 bulas | Schema criado, 5 bulas no grafo e riscos testados |
| **Sex 06/11** | Extração dos 60 medicamentos; resolução de entidades; revisão por amostragem | Grafo completo, sem duplicatas na amostra revisada |
| **Sáb 07/11** | Busca híbrida; multi-hop; algoritmos de grafo; geração de Cypher; regras de resposta; interface | Pergunta de 2 saltos respondida na interface, com subgrafo e citações |
| **Dom 08/11** | Perguntas e gabarito; avaliação das 3 configurações; correções; testes; hospedagem | Resultados salvos e link funcionando para os convidados |
| **Seg 09/11** | Congelar o código; `README.md`; artigo SBC; slides; gravação da demonstração | Primeira versão completa de todas as entregas |
| **Ter 10/11** | Revisão pelo grupo; reprodução do zero em outro computador; ensaio; backup do grafo | Ensaio cronometrado concluído e entregas finais no repositório |
| **Qua 11/11** | Reativar os serviços com antecedência; apresentação | Defesa realizada |

## 7. Divisão do grupo

Cada dupla revisa sua frente e precisa conseguir explicá-la na banca.

| Dupla | Responsabilidade |
|---|---|
| **A: Dados** | Lista de medicamentos, revisão das extrações e checagem das respostas |
| **B: Grafo e recuperação** | Schema, consultas Cypher, algoritmos e explicação do multi-hop |
| **C: Interface e hospedagem** | Testes de uso, hospedagem, gravação da demonstração e plano de reserva |
| **D: Avaliação e escrita** | Perguntas e gabarito, artigo, referências e slides |

Uma pessoa fica responsável pelas contas do Aura e do Streamlit. Nomes: definir na quinta.

## 8. Riscos e reservas

| Risco | Como testar ou reduzir | Se acontecer |
|---|---|---|
| Cota do Gemini baixa | Ver o limite no AI Studio na quinta | Dividir a extração em mais dias ou usar o Groq; em último caso, reduzir de 60 para 30 a 40 medicamentos |
| Extração ruim | Revisar as 5 primeiras bulas antes de processar o restante | Ajustar o prompt e o schema antes de gastar cota |
| Índice vetorial limitado no Aura Free | Testar na quinta | Rodar localmente na demonstração e registrar a limitação |
| Memória insuficiente no Streamlit | Testar um app mínimo com o modelo de embeddings na quinta | Usar Gemini Embedding somente nas consultas |
| Falha ou cota esgotada na apresentação | Demonstração gravada dentro dos slides | Usar o Groq, a versão local ou as respostas salvas |
| Pausa dos serviços gratuitos | Abrir os dois antes da apresentação | Usar a versão local e o backup do grafo |
| Atraso geral | Acompanhar os critérios do cronograma | Cortar nesta ordem: hospedagem, Ollama e tamanho do conjunto de dados |

A hospedagem é o primeiro corte. Os itens do enunciado não são cortados.

## 9. Antes de começar

Para fazer até quinta de manhã:

- [ ] Criar a chave gratuita no [Google AI Studio](https://aistudio.google.com) e conferir a cota
- [ ] Criar a chave gratuita no [Groq](https://console.groq.com)
- [ ] Criar a conta do [Neo4j Aura Free](https://neo4j.com/product/auradb/)
- [ ] Criar a conta do GitHub do projeto e a do [Streamlit Community Cloud](https://streamlit.io/cloud)
- [ ] Deixar o Docker Desktop aberto no computador de desenvolvimento
- [ ] Definir as duplas

**Perguntas para o professor:**

1. Quanto tempo cada grupo terá, e ele espera ver o sistema rodando?
2. Existe alguma pesquisa além do artigo técnico? O enunciado não pede.
3. O artigo tem limite de páginas ou seções obrigatórias? Qual é o prazo de entrega?

## 10. Critério de conclusão

| Critério do enunciado | Peso | Evidência |
|---|---|---|
| Modelagem de grafos | 25% | Schema, constraints e índices criados por script e verificados em teste |
| Engenharia de GraphRAG/KAG | 30% | Pipeline reproduzível, busca híbrida, respostas com 2 ou mais saltos e resultados da avaliação |
| Interface e explicabilidade | 20% | Subgrafo interativo, nós consultados, Cypher, confiança e trechos originais |
| Código e artigo | 25% | Repositório organizado, `README.md` testado em outro computador, testes passando e artigo SBC revisado |

**Teste final:** uma pessoa do grupo que não participou da configuração segue o `README.md` em outro computador e obtém uma resposta multi-hop com citações.
