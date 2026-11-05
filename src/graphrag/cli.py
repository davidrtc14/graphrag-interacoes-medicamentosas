"""Comandos do projeto: graphrag <comando>."""
from __future__ import annotations

import argparse
import json

from .config import DIR_EXTRACOES, carregar_config
from .llm import ErroLLM, criar_llm
from .medicamentos import Resolvedor, carregar_medicamentos
from .openfda import baixar_bulas, carregar_bulas, dividir_em_trechos


def _marcas(bulas: list[dict]) -> dict[str, str]:
    return {marca: b["generico"] for b in bulas for marca in b.get("marcas", [])}


def cmd_baixar(_args) -> None:
    faltando = baixar_bulas(carregar_medicamentos())
    print(f"Bulas disponíveis: {len(carregar_bulas())}. Sem bula adequada: {faltando or 'nenhum'}")


def cmd_schema(_args) -> None:
    from .grafo import conectar, criar_schema

    with conectar(carregar_config()) as driver:
        criar_schema(driver)
    print("Constraints e índices criados.")


def cmd_extrair(args) -> None:
    from .extracao import extrair_bula

    llm = criar_llm(carregar_config())
    bulas = carregar_bulas()
    if args.medicamentos:
        bulas = [b for b in bulas if b["generico"] in args.medicamentos]
    feitos = [b for b in bulas if (DIR_EXTRACOES / f"{b['generico']}.json").exists()]
    pendentes = [b for b in bulas if b not in feitos][: args.limite]
    print(f"Já extraídas: {len(feitos)}. Nesta execução: {len(pendentes)}.")
    falhas_seguidas = 0
    pulados = []
    for i, bula in enumerate(pendentes, 1):
        try:
            resultado = extrair_bula(bula["generico"], dividir_em_trechos(bula), llm)
        except ErroLLM as erro:
            pulados.append(bula["generico"])
            falhas_seguidas += 1
            print(f"[{i}/{len(pendentes)}] {bula['generico']}: falhou: {str(erro)[:300]}", flush=True)
            if falhas_seguidas >= 3 or llm.todos_esgotados:
                print("Parado após falhas seguidas. Execute de novo depois: o progresso está salvo.")
                break
            continue
        falhas_seguidas = 0
        print(f"[{i}/{len(pendentes)}] {bula['generico']}: {len(resultado['relacoes'])} relações, "
              f"{len(resultado['rejeitadas'])} rejeitadas, modelos {resultado['modelos']}", flush=True)
    print(f"Pulados nesta execução: {pulados or 'nenhum'}")


def cmd_carregar(args) -> None:
    from .embeddings import embutir
    from .grafo import carregar_grafo, conectar, criar_schema, estatisticas

    medicamentos = carregar_medicamentos()
    bulas = carregar_bulas()
    extracoes = [json.loads(a.read_text(encoding="utf-8")) for a in sorted(DIR_EXTRACOES.glob("*.json"))]
    resolvedor = Resolvedor(medicamentos, _marcas(bulas))
    with conectar(carregar_config()) as driver:
        if args.limpar:
            driver.execute_query("MATCH (n) DETACH DELETE n")
        criar_schema(driver)
        consolidado = carregar_grafo(driver, medicamentos, bulas, extracoes, resolvedor, embutir)
        print(f"Nomes unificados: {len(consolidado['unificacoes'])}. Autorreferências descartadas: {consolidado['descartadas']}.")
        print(json.dumps(estatisticas(driver), ensure_ascii=False, indent=1))


def cmd_algoritmos(_args) -> None:
    from .grafo import calcular_algoritmos, conectar

    with conectar(carregar_config()) as driver:
        print(calcular_algoritmos(driver))


def cmd_exportar(_args) -> None:
    from .grafo import conectar, exportar_grafo

    with conectar(carregar_config()) as driver:
        print(exportar_grafo(driver))


def cmd_importar(_args) -> None:
    from .grafo import conectar, importar_grafo

    with conectar(carregar_config()) as driver:
        print(importar_grafo(driver))


def cmd_status(_args) -> None:
    from .grafo import conectar, estatisticas

    with conectar(carregar_config()) as driver:
        print(json.dumps(estatisticas(driver), ensure_ascii=False, indent=1))


def cmd_perguntar(args) -> None:
    from .embeddings import embutir
    from .grafo import conectar
    from .resposta import responder

    config = carregar_config()
    with conectar(config) as driver:
        r = responder(args.pergunta, criar_llm(config), driver, embutir, modo=args.modo)
    print(r.texto)
    print(f"\nConfiança: {r.nivel} ({r.confianca}) | Modelo: {r.modelo or '-'} | {r.latencia_s:.1f}s")
    for citacao in r.citacoes:
        print(f"[{citacao}] {r.evidencias[citacao][:240]}")


def cmd_avaliar(args) -> None:
    from .avaliacao import DIR_RESULTADOS, MODOS, carregar_perguntas, executar, gerar_revisao, resumir
    from .embeddings import embutir
    from .grafo import conectar

    config = carregar_config()
    perguntas = carregar_perguntas()
    if args.limite:
        perguntas = perguntas[: args.limite]
    with conectar(config) as driver:
        registros = executar(perguntas, criar_llm(config), driver, embutir, DIR_RESULTADOS / "respostas.jsonl", MODOS)
    resumo = resumir(registros)
    (DIR_RESULTADOS / "metricas.json").write_text(json.dumps(resumo, ensure_ascii=False, indent=1), encoding="utf-8")
    gerar_revisao(registros, DIR_RESULTADOS / "revisao.csv")
    print(json.dumps(resumo, ensure_ascii=False, indent=1))


def main() -> None:
    analisador = argparse.ArgumentParser(prog="graphrag", description=__doc__)
    comandos = analisador.add_subparsers(dest="comando", required=True)
    comandos.add_parser("baixar", help="baixa as bulas da openFDA").set_defaults(func=cmd_baixar)
    comandos.add_parser("schema", help="cria constraints e índices").set_defaults(func=cmd_schema)
    extrair = comandos.add_parser("extrair", help="extrai entidades e relações, retomando de onde parou")
    extrair.add_argument("--limite", type=int, default=1000, help="máximo de bulas nesta execução")
    extrair.add_argument("--medicamentos", nargs="*", help="restringe aos genéricos informados")
    extrair.set_defaults(func=cmd_extrair)
    carregar = comandos.add_parser("carregar", help="carrega as extrações no Neo4j")
    carregar.add_argument("--limpar", action="store_true", help="apaga a base antes; ela é refeita a partir de data/extracoes")
    carregar.set_defaults(func=cmd_carregar)
    comandos.add_parser("algoritmos", help="calcula PageRank e Louvain").set_defaults(func=cmd_algoritmos)
    comandos.add_parser("exportar", help="salva o grafo pronto em data/grafo").set_defaults(func=cmd_exportar)
    comandos.add_parser("importar", help="carrega o grafo pronto, sem gastar cota").set_defaults(func=cmd_importar)
    comandos.add_parser("status", help="mostra a contagem de nós e relações").set_defaults(func=cmd_status)
    perguntar = comandos.add_parser("perguntar", help="responde a uma pergunta no terminal")
    perguntar.add_argument("pergunta")
    perguntar.add_argument("--modo", choices=["graphrag", "rag_vetorial", "llm_puro"], default="graphrag")
    perguntar.set_defaults(func=cmd_perguntar)
    avaliar = comandos.add_parser("avaliar", help="compara os três modos e salva as métricas, retomando de onde parou")
    avaliar.add_argument("--limite", type=int, help="usa só as primeiras perguntas")
    avaliar.set_defaults(func=cmd_avaliar)
    args = analisador.parse_args()
    try:
        args.func(args)
    except ErroLLM as erro:
        raise SystemExit(f"Erro: {erro}") from erro


if __name__ == "__main__":
    main()
