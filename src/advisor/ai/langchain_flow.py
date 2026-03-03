from __future__ import annotations

import json
from typing import Any, Tuple

from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_fixed

from advisor.ai.prompt_templates import DEEP_SYSTEM_PROMPT, LIGHT_SYSTEM_PROMPT, USER_PROMPT_TEMPLATE
from advisor.config import AppConfig
from advisor.models import AnalysisRequest, Recommendation, RiskChecks

try:
    from langchain_core.exceptions import OutputParserException
    from langchain_core.output_parsers import PydanticOutputParser
    from langchain_core.prompts import ChatPromptTemplate
    from langchain_openai import ChatOpenAI

    LANGCHAIN_AVAILABLE = True
except Exception:  # pragma: no cover - optional dependency import guard
    OutputParserException = ValueError
    LANGCHAIN_AVAILABLE = False


class AIAnalyzer:
    def __init__(self, config: AppConfig):
        self.config = config
        self.parser = None
        self.light_prompt = None
        self.deep_prompt = None
        self.light_llm = None
        self.deep_llm = None

        if LANGCHAIN_AVAILABLE and config.openai_enabled:
            self.parser = PydanticOutputParser(pydantic_object=Recommendation)
            self.light_prompt = ChatPromptTemplate.from_messages(
                [("system", LIGHT_SYSTEM_PROMPT), ("human", USER_PROMPT_TEMPLATE)]
            )
            self.deep_prompt = ChatPromptTemplate.from_messages(
                [("system", DEEP_SYSTEM_PROMPT), ("human", USER_PROMPT_TEMPLATE)]
            )
            self.light_llm = ChatOpenAI(
                model=config.openai_model_light,
                api_key=config.openai_api_key,
                temperature=0,
            )
            self.deep_llm = _build_deep_llm(config)

    def analyze(self, request: AnalysisRequest, deep: bool) -> Tuple[Recommendation, str, str]:
        if not LANGCHAIN_AVAILABLE:
            return fallback_recommendation("LangChain/OpenAI dependencies unavailable"), "fallback", ""
        if not self.config.openai_enabled:
            return fallback_recommendation("OPENAI_API_KEY not configured"), "fallback", ""

        try:
            recommendation, raw = self._invoke_and_parse(request, deep)
            model_name = self.config.openai_model_deep if deep else self.config.openai_model_light
            return recommendation, model_name, raw
        except Exception as exc:
            return fallback_recommendation(f"AI parse failure fallback: {exc}"), "fallback", ""

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_fixed(1),
        retry=retry_if_exception_type((OutputParserException, ValueError, json.JSONDecodeError)),
        reraise=True,
    )
    def _invoke_and_parse(self, request: AnalysisRequest, deep: bool) -> Tuple[Recommendation, str]:
        assert self.parser is not None
        assert self.light_prompt is not None
        assert self.deep_prompt is not None

        prompt = self.deep_prompt if deep else self.light_prompt
        llm = self.deep_llm if deep else self.light_llm
        if llm is None:
            raise ValueError("LLM client is not configured")

        message = prompt.format_messages(
            cycle_ts=request.cycle_ts.isoformat(),
            portfolio_json=json.dumps(request.portfolio.model_dump(mode="json"), default=str),
            risk_json=json.dumps(request.risk_metrics.model_dump(mode="json"), default=str),
            triggers_json=json.dumps([item.model_dump(mode="json") for item in request.triggers], default=str),
            instruments_json=json.dumps(
                [item.model_dump(mode="json") for item in request.key_instruments], default=str
            ),
            format_instructions=self.parser.get_format_instructions(),
        )

        response = llm.invoke(message)
        raw_text = extract_text(response.content)
        recommendation = parse_recommendation(raw_text)
        return recommendation, raw_text


def _build_deep_llm(config: AppConfig):
    kwargs: dict[str, Any] = {
        "model": config.openai_model_deep,
        "api_key": config.openai_api_key,
        "temperature": 0,
    }

    # OpenAI Responses API compatible web search tool configuration.
    kwargs["model_kwargs"] = {"tools": [{"type": "web_search_preview"}]}

    try:
        return ChatOpenAI(use_responses_api=True, **kwargs)
    except TypeError:
        return ChatOpenAI(**kwargs)


def parse_recommendation(payload: str) -> Recommendation:
    cleaned = payload.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        cleaned = cleaned.replace("json", "", 1).strip()
    data = json.loads(cleaned)
    return Recommendation.model_validate(data)


def extract_text(content: Any) -> str:
    if isinstance(content, str):
        return content

    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
                continue
            if isinstance(item, dict):
                if "text" in item and isinstance(item["text"], str):
                    parts.append(item["text"])
                    continue
                if item.get("type") == "output_text" and isinstance(item.get("text"), str):
                    parts.append(item["text"])
        return "\n".join(parts)

    return str(content)


def fallback_recommendation(note: str) -> Recommendation:
    return Recommendation(
        decision="NO_ACTION",
        action_type="HOLD",
        target_symbols=[],
        rationale="Fallback decision due to unavailable or invalid AI response.",
        risk_checks=RiskChecks(margin_ok=True, leverage_ok=True, concentration_ok=True),
        confidence=0.0,
        ttl_minutes=5,
        monitoring_note=note,
    )
