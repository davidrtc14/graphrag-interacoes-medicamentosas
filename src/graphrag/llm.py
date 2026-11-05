"""Provedores de LLM com troca automática quando um deles falha."""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Protocol

import httpx

from .config import Config


class ErroLLM(RuntimeError):
    """Nenhum provedor conseguiu responder."""


@dataclass(frozen=True)
class RespostaLLM:
    texto: str
    modelo: str

    def json(self) -> dict:
        return interpretar_json(self.texto)


class LLM(Protocol):
    def gerar(self, prompt: str, sistema: str = "", json_mode: bool = False) -> RespostaLLM: ...


class CotaDiariaEsgotada(ErroLLM):
    """A cota do dia acabou: insistir só desperdiça tempo."""


def _eh_cota_diaria(erro: Exception) -> bool:
    return bool(re.search(r"per ?day|PerDay|tokens per day|TPD|RPD", str(erro)))


def interpretar_json(texto: str) -> dict:
    """Interpreta JSON, aceitando o bloco cercado que alguns modelos devolvem."""
    limpo = re.sub(r"^```(?:json)?\s*|\s*```$", "", texto.strip())
    try:
        return json.loads(limpo)
    except json.JSONDecodeError:
        inicio, fim = limpo.find("{"), limpo.rfind("}")
        if inicio == -1 or fim <= inicio:
            raise
        return json.loads(limpo[inicio : fim + 1])


def _com_novas_tentativas(chamada, eh_limite, tentativas: int = 3, espera: float = 20.0):
    """Repete a chamada ao atingir o limite por minuto dos planos gratuitos."""
    for tentativa in range(tentativas):
        try:
            return chamada()
        except Exception as erro:
            if eh_limite(erro) and _eh_cota_diaria(erro):
                raise CotaDiariaEsgotada(str(erro)[:200]) from erro
            if not eh_limite(erro) or tentativa == tentativas - 1:
                raise
            time.sleep(espera * (tentativa + 1))
    raise AssertionError("inalcançável")


class Gemini:
    def __init__(self, chave: str, modelo: str) -> None:
        from google import genai

        self._cliente = genai.Client(api_key=chave)
        self.modelo = modelo

    def gerar(self, prompt: str, sistema: str = "", json_mode: bool = False) -> RespostaLLM:
        from google.genai import errors, types

        config = types.GenerateContentConfig(
            system_instruction=sistema or None,
            response_mime_type="application/json" if json_mode else None,
        )
        resposta = _com_novas_tentativas(
            lambda: self._cliente.models.generate_content(model=self.modelo, contents=prompt, config=config),
            lambda e: isinstance(e, errors.APIError) and e.code == 429,  # Em 503, passa logo para a reserva.
        )
        if not resposta.text:
            raise ErroLLM(f"{self.modelo} devolveu resposta vazia")
        return RespostaLLM(resposta.text, self.modelo)


class Groq:
    def __init__(self, chave: str, modelo: str) -> None:
        import groq

        self._groq = groq
        self._cliente = groq.Groq(api_key=chave)
        self.modelo = modelo

    def gerar(self, prompt: str, sistema: str = "", json_mode: bool = False) -> RespostaLLM:
        mensagens = [{"role": "system", "content": sistema}] if sistema else []
        mensagens.append({"role": "user", "content": prompt})
        extras = {"response_format": {"type": "json_object"}} if json_mode else {}

        def chamar(**opcoes):
            return _com_novas_tentativas(
                lambda: self._cliente.chat.completions.create(model=self.modelo, messages=mensagens, **opcoes),
                lambda e: isinstance(e, self._groq.RateLimitError),
            )

        try:
            resposta = chamar(**extras)
        except self._groq.BadRequestError as erro:
            # O modo JSON do Groq recusa saídas quase válidas; sem ele, quem valida é interpretar_json.
            if "json_validate_failed" not in str(erro):
                raise
            resposta = chamar()
        return RespostaLLM(resposta.choices[0].message.content or "", self.modelo)


class Ollama:
    def __init__(self, url: str, modelo: str) -> None:
        self.url = url.rstrip("/")
        self.modelo = modelo

    def gerar(self, prompt: str, sistema: str = "", json_mode: bool = False) -> RespostaLLM:
        mensagens = [{"role": "system", "content": sistema}] if sistema else []
        mensagens.append({"role": "user", "content": prompt})
        corpo = {"model": self.modelo, "messages": mensagens, "stream": False}
        if json_mode:
            corpo["format"] = "json"
        resposta = httpx.post(f"{self.url}/api/chat", json=corpo, timeout=300)
        resposta.raise_for_status()
        return RespostaLLM(resposta.json()["message"]["content"], self.modelo)


class Encadeado:
    """Usa o primeiro provedor que responder, na ordem configurada."""

    def __init__(self, provedores: list[LLM]) -> None:
        if not provedores:
            raise ErroLLM("Nenhum provedor configurado. Preencha as chaves no .env.")
        self.provedores = provedores
        self.esgotados: set[int] = set()

    def gerar(self, prompt: str, sistema: str = "", json_mode: bool = False) -> RespostaLLM:
        erros = []
        for i, provedor in enumerate(self.provedores):
            if i in self.esgotados:
                continue
            try:
                resposta = provedor.gerar(prompt, sistema, json_mode)
                if json_mode:
                    resposta.json()  # Saída cortada ou inválida também passa para o próximo.
                return resposta
            except CotaDiariaEsgotada as erro:
                self.esgotados.add(i)
                erros.append(f"{type(provedor).__name__}: cota diária: {str(erro)[:120]}")
            except Exception as erro:
                erros.append(f"{type(provedor).__name__}: {type(erro).__name__}: {str(erro)[:200]}")
        raise ErroLLM("Todos os provedores falharam: " + " | ".join(erros))

    @property
    def todos_esgotados(self) -> bool:
        return len(self.esgotados) == len(self.provedores)


def criar_llm(config: Config) -> Encadeado:
    provedores: list[LLM] = []
    for nome in config.provedores:
        if nome == "gemini" and config.gemini_key:
            # Vários modelos: o plano gratuito às vezes devolve 503 em um deles.
            provedores += [Gemini(config.gemini_key, m.strip()) for m in config.gemini_model.split(",") if m.strip()]
        elif nome == "groq" and config.groq_key:
            provedores.append(Groq(config.groq_key, config.groq_model))
        elif nome == "ollama":
            provedores.append(Ollama(config.ollama_url, config.ollama_model))
    return Encadeado(provedores)
