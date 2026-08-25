import json
import logging
import re
import uuid
from functools import lru_cache
from typing import Any

logger = logging.getLogger(__name__)

try:
    from langgraph.graph import END, StateGraph
except TypeError:
    END = "__end__"

    class StateGraph:
        """Small compatibility fallback for environments with broken LangGraph imports."""

        def __init__(self, _state_type: type) -> None:
            self._nodes: dict[str, Any] = {}
            self._edges: dict[str, str] = {}
            self._entry: str | None = None

        def add_node(self, name: str, func: Any) -> None:
            self._nodes[name] = func

        def set_entry_point(self, name: str) -> None:
            self._entry = name

        def add_edge(self, start: str, end: str) -> None:
            self._edges[start] = end

        def compile(self) -> Any:
            graph = self

            class CompiledGraph:
                def invoke(self, state: ReviewState) -> ReviewState:
                    node = graph._entry
                    while node and node != END:
                        state = graph._nodes[node](state)
                        node = graph._edges.get(node)
                    return state

            return CompiledGraph()

from physics_reviewer.config import get_settings
from physics_reviewer.cache_store import cache_key, cache_lock, get_cached, set_cached
from physics_reviewer.knowledge_store import get_knowledge_store
from physics_reviewer.literature import (
    build_literature_query,
    literature_context,
    search_literature,
)
from physics_reviewer.qwen_client import QwenClient
from physics_reviewer.schemas import ReviewResponse, ReviewState


REVIEW_PIPELINE_VERSION = "review-pipeline-v3"


def _paper_excerpt(state: ReviewState) -> str:
    limit = get_settings().max_paper_chars
    text = state["paper_text"]
    return text[:limit]


def _append_finding(state: ReviewState, finding: dict[str, Any]) -> ReviewState:
    findings = [*state.get("findings", []), finding]
    return {**state, "findings": findings}


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, tuple):
        return [str(item) for item in value if str(item).strip()]
    text = str(value).strip()
    return [text] if text else []


def _as_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(maximum, number))


def _calibrated_overall_score(result: dict[str, Any], scores: dict[str, int]) -> int:
    """Keep one specialist concern from dominating the holistic mark."""
    weights = {
        "novelty": 0.12,
        "physics_correctness": 0.25,
        "method_rigor": 0.23,
        "reproducibility": 0.08,
        "citation_quality": 0.12,
        "writing_quality": 0.20,
    }
    weighted_score = round(
        sum(scores[dimension] * 20 * weight for dimension, weight in weights.items())
    )
    model_score = _as_int(result.get("overall_score"), weighted_score, 1, 100)

    # The model may account for holistic qualities, but cannot apply an unbounded
    # second penalty after it has already scored the affected dimensions.
    return max(weighted_score - 5, min(weighted_score + 5, model_score))


@lru_cache
def _qwen() -> QwenClient:
    return QwenClient()


def intake_agent(state: ReviewState) -> ReviewState:
    original_length = len(state["paper_text"])
    text = _paper_excerpt(state)
    title = state.get("title") or _guess_title(text)
    new_state = {**state, "title": title, "paper_text": text}

    if len(text) < original_length:
        finding = {
            "agent": "intake",
            "status": "warning",
            "findings": [
                f"Paper text truncated from {original_length} to {len(text)} characters "
                f"(MAX_PAPER_CHARS={get_settings().max_paper_chars}); content past this point "
                "was not reviewed."
            ],
            "evidence": [],
        }
        return _append_finding(new_state, finding)

    return new_state


def rule_extraction_agent(state: ReviewState) -> ReviewState:
    """Extract cheap, auditable paper signals before choosing model-powered checks."""
    text = state["paper_text"]
    sections = _extract_sections(text)
    signals = {
        "equation_count": len(re.findall(r"(?:\bequation\b|\beq\.|\$[^$]+\$|\\\[|\\begin\{equation\})", text, re.I)),
        "citation_markers": len(re.findall(r"(?:\[[0-9][0-9,;\- ]*\]|\([A-Z][A-Za-z-]+(?: et al\.)?,? \d{4}[a-z]?\))", text)),
        "has_references": bool(re.search(r"^\s*(references|bibliography)\s*$", text, re.I | re.M)),
        "has_novelty_claim": bool(re.search(r"\b(novel|new approach|first (?:demonstration|observation)|we propose|we introduce|our contribution)\b", text, re.I)),
        "has_method_detail": bool(re.search(r"\b(methods?|methodology|simulation|experiment(?:al)?|dataset|code availability|implementation)\b", text, re.I)),
    }
    finding = {
        "agent": "rule_extraction",
        "status": "ok",
        "findings": [
            f"Detected {len(sections)} section heading(s), {signals['equation_count']} equation marker(s), "
            f"and {signals['citation_markers']} in-text citation marker(s)."
        ],
        "evidence": list(sections.keys())[:8],
    }
    return _append_finding({**state, "sections": sections, "rule_signals": signals}, finding)


def router_agent(state: ReviewState) -> ReviewState:
    """Select the highest-value specialist calls under a configurable per-paper budget."""
    signals = state.get("rule_signals", {})
    candidates: list[tuple[str, bool, str]] = [
        (
            "physics_check",
            bool(signals.get("equation_count")) or _contains_physics_terms(state["paper_text"]),
            "physics or equation content detected",
        ),
        (
            "novelty_check",
            bool(signals.get("has_novelty_claim")),
            "explicit novelty/contribution claim detected",
        ),
        (
            "reproducibility_check",
            bool(signals.get("has_method_detail")),
            "methods, simulation, experiment, data, or implementation content detected",
        ),
        (
            "citation_check",
            bool(signals.get("has_references")) or int(signals.get("citation_markers", 0)) > 0,
            "references or in-text citations detected",
        ),
    ]
    eligible = [(agent, reason) for agent, enabled, reason in candidates if enabled]
    budget = max(0, get_settings().router_max_specialist_calls)
    selected = [agent for agent, _ in eligible[:budget]]
    skipped = [agent for agent, _, _ in candidates if agent not in selected]
    selection_reasons = {agent: reason for agent, reason in eligible}
    finding = {
        "agent": "router",
        "status": "ok" if selected else "warning",
        "findings": [
            f"Selected {len(selected)} specialist model call(s) from a budget of {budget}: "
            f"{', '.join(selected) if selected else 'none'}.",
            *[f"{agent}: {selection_reasons[agent]}" for agent in selected],
            *[f"{agent}: handled by rules only for this paper." for agent in skipped],
        ],
        "evidence": [f"{name}={value}" for name, value in signals.items()],
    }
    return _append_finding({**state, "selected_agents": selected}, finding)


def literature_search_agent(state: ReviewState) -> ReviewState:
    settings = get_settings()
    needs_literature = {"citation_check", "novelty_check"}.intersection(
        state.get("selected_agents", [])
    )
    if not settings.literature_search_enabled or not needs_literature:
        return {
            **state,
            "literature": [],
        }

    query = build_literature_query(state.get("title"), state["paper_text"])
    papers = search_literature(query, settings.literature_search_limit)

    vector_status = "ok"
    vector_evidence = []
    try:
        store = get_knowledge_store(state["review_id"])
        store.upsert_papers(papers)
        vector_evidence = [
            item["metadata"].get("title", item["id"])
            for item in store.query(query, top_k=min(3, settings.literature_search_limit))
        ]
    except Exception as exc:
        logger.warning("Vector store upsert/query failed for review %s", state.get("review_id"), exc_info=True)
        vector_status = "warning"
        vector_evidence = [f"Vector store unavailable: {exc}"]

    finding = {
        "agent": "literature_search",
        "status": "ok" if papers and vector_status == "ok" else "warning",
        "findings": [
            f"Retrieved {len(papers)} external papers for novelty and citation grounding.",
        ],
        "evidence": [
            paper.title for paper in papers[:5]
        ]
        + vector_evidence,
    }
    return _append_finding(
        {
            **state,
            "literature": [paper.model_dump() for paper in papers],
        },
        finding,
    )


def physics_check_agent(state: ReviewState) -> ReviewState:
    return _domain_check(
        state,
        agent="physics_check",
        instruction=(
            "Check physics correctness, assumptions, dimensional consistency, equations, "
            "and claims that require verification. First interpret symbols using the paper's "
            "own definitions and allow equivalent notation or conventions. Be evidence-bound: "
            "status=error is reserved for a demonstrated mistake that materially invalidates a "
            "central result; quote the exact equation or claim and show the conflicting units, "
            "assumption, or derivation. A debatable definition, local notation issue, omitted "
            "derivation, or concern that needs context is status=warning. If the available text "
            "is insufficient, report a required check, never a claimed error."
        ),
    )


def citation_check_agent(state: ReviewState) -> ReviewState:
    return _domain_check(
        state,
        agent="citation_check",
        instruction=(
            "Assess citation sufficiency, missing prior work, unsupported claims, and whether "
            "the reference pattern looks adequate. Use external literature context when present. "
            "Do not invent citations beyond the provided context."
        ),
    )


def novelty_check_agent(state: ReviewState) -> ReviewState:
    return _domain_check(
        state,
        agent="novelty_check",
        instruction=(
            "Assess novelty by comparing the paper's claimed contributions against the provided "
            "external literature context. Separate incremental novelty from no novelty: adapting "
            "an established idea to a new model, dataset, setting, or implementation is normally "
            "a warning-level limitation, not a fatal flaw. Do not conclude that novelty is absent "
            "from one similar paper alone. If retrieval coverage is limited, state the uncertainty "
            "instead of making a strong negative judgement. Cite provided titles or URLs as evidence."
        ),
    )


def reproducibility_check_agent(state: ReviewState) -> ReviewState:
    return _domain_check(
        state,
        agent="reproducibility_check",
        instruction=(
            "Assess reproducibility: datasets, code, parameters, derivations, simulation setup, "
            "experimental details, and uncertainty estimates. Missing public code, raw data, "
            "environment files, ablations, seeds, or some parameters are normally limitations "
            "with status=warning; list them as actionable checks and do not treat every omission "
            "as an independent severe defect. Use status=error only when a specific internal "
            "contradiction or missing indispensable definition makes the central reported result "
            "impossible to reproduce from the described method, and quote exact evidence."
        ),
    )


def rubric_scoring_agent(state: ReviewState) -> ReviewState:
    result = _qwen().complete_json(
        system=(
            "You are a calibrated, evidence-bound physics journal reviewer. Return JSON only. "
            "Scores must be integers: novelty, physics_correctness, method_rigor, "
            "reproducibility, citation_quality, writing_quality from 1 to 5, and "
            "overall_score from 1 to 100. Include strengths, weaknesses, required_checks, "
            "uncertainty_notes, and summary. Use this scale: 3=adequate baseline based on "
            "the available evidence; 4=good and well-supported; 5=exceptional with strong "
            "evidence; 2=clear, material weakness; 1=directly evidenced fatal flaw. Do not "
            "assign 1 or claim a catastrophic error unless an agent supplies a specific paper "
            "quote/equation and its evidence. Missing, unparsed, or truncated information is "
            "an uncertainty note or required check, not proof of a flaw. Do not punish a paper "
            "twice for the same evidenced issue: assign it to the most relevant dimension; any "
            "secondary effect may lower at most one other dimension by one level. Agent status is "
            "triage metadata, not an automatic score deduction. Missing code/data alone should "
            "not make reproducibility=1, and incremental novelty alone should not make novelty=1. "
            "Balance limitations against demonstrated strengths. Keep overall_score consistent "
            "with the six dimension scores rather than applying additional deductions."
        ),
        user=(
            f"Title: {state.get('title')}\n\n"
            f"Agent findings:\n{json.dumps(state.get('findings', []), ensure_ascii=False)}\n\n"
            f"Section summaries:\n{json.dumps(state.get('sections', {}), ensure_ascii=False)}\n\n"
            f"External literature context:\n{_literature_context_for_agent(state, 'rubric_scoring')}"
        ),
    )
    scores = {
        "novelty": _as_int(result.get("novelty"), 3, 1, 5),
        "physics_correctness": _as_int(result.get("physics_correctness"), 3, 1, 5),
        "method_rigor": _as_int(result.get("method_rigor"), 3, 1, 5),
        "reproducibility": _as_int(result.get("reproducibility"), 3, 1, 5),
        "citation_quality": _as_int(result.get("citation_quality"), 3, 1, 5),
        "writing_quality": _as_int(result.get("writing_quality"), 3, 1, 5),
    }
    scores["overall_score"] = _calibrated_overall_score(result, scores)
    return {**state, "scores": scores, "rubric_result": result}


def report_generation_agent(state: ReviewState) -> ReviewState:
    rubric = state.get("rubric_result", {})
    report = {
        "title": state.get("title"),
        "scores": state["scores"],
        "strengths": _as_list(rubric.get("strengths", [])),
        "weaknesses": _as_list(rubric.get("weaknesses", [])),
        "required_checks": _as_list(rubric.get("required_checks", [])),
        "uncertainty_notes": _as_list(rubric.get("uncertainty_notes", [])),
        "summary": str(rubric.get("summary", "")),
    }
    return {**state, "report": report}


def _domain_check(state: ReviewState, agent: str, instruction: str) -> ReviewState:
    if agent not in state.get("selected_agents", []):
        return state
    result = _qwen().complete_json(
        system=(
            "You are one specialized agent in a multi-agent physics paper review system. "
            "Return JSON only with keys: status, findings, evidence. status is ok, warning, or error. "
            + instruction
        ),
        user=(
            f"Title: {state.get('title')}\n\n"
            f"Section summaries:\n{json.dumps(state.get('sections', {}), ensure_ascii=False)}\n\n"
            f"External literature context:\n{_literature_context_for_agent(state, agent)}\n\n"
            f"Paper excerpt:\n{state['paper_text']}"
        ),
    )
    finding = {
        "agent": agent,
        "status": _normalize_status(result.get("status")),
        "findings": _as_list(result.get("findings", [])),
        "evidence": _as_list(result.get("evidence", [])),
    }
    return _append_finding(state, finding)


def _normalize_status(value: Any) -> str:
    status = str(value or "warning").lower().strip()
    if status not in {"ok", "warning", "error"}:
        return "warning"
    return status


_AGENT_QUERY_FOCUS = {
    "physics_check": "physics correctness, assumptions, equations, dimensional consistency",
    "citation_check": "prior work, related references, citation coverage",
    "novelty_check": "novelty and contribution compared to prior similar work",
    "reproducibility_check": "methodology, datasets, implementation and reproducibility details",
    "rubric_scoring": "overall comparison with related published work",
}


def _retrieval_query_for(agent: str, state: ReviewState) -> str:
    title = state.get("title") or ""
    sections = state.get("sections")
    values = list(sections.values()) if isinstance(sections, dict) else []
    section_text = " ".join(str(value) for value in values[:4])[:600]
    focus = _AGENT_QUERY_FOCUS.get(agent, "")
    return f"{title}. {focus}. {section_text}".strip()


def _literature_context_for_agent(state: ReviewState, agent: str, top_k: int = 4) -> str:
    settings = get_settings()
    if not settings.literature_search_enabled or not state.get("literature"):
        return "No external literature context available."

    try:
        store = get_knowledge_store(state["review_id"])
        query = _retrieval_query_for(agent, state)
        rows = store.query(query, top_k=top_k)
    except Exception:
        logger.warning(
            "Vector retrieval failed for agent=%s review=%s, falling back to full literature list",
            agent,
            state.get("review_id"),
            exc_info=True,
        )
        return _literature_context_full_fallback(state)

    return _format_retrieved_literature(rows, settings.literature_max_distance)


def _format_retrieved_literature(rows: list[dict[str, Any]], max_distance: float) -> str:
    filtered = [
        row for row in rows if row["distance"] is None or row["distance"] <= max_distance
    ]
    if not filtered:
        return "No sufficiently relevant external literature found for this check."

    lines = []
    for row in filtered:
        meta = row["metadata"]
        distance = row["distance"]
        similarity = f"{1 - distance:.2f}" if distance is not None else "unknown"
        lines.append(
            f"- {meta.get('title')} ({meta.get('year') or 'n.d.'}, {meta.get('source')}) "
            f"[similarity={similarity}]\n"
            f"  URL: {meta.get('url') or 'n/a'}\n"
            f"  {row['document']}"
        )
    return "\n\n".join(lines)


def _literature_context_full_fallback(state: ReviewState) -> str:
    papers = state.get("literature", [])
    if not papers:
        return "No external literature context available."
    return literature_context(papers=[_paper_from_dict(item) for item in papers])


def _paper_from_dict(data: dict[str, Any]):
    from physics_reviewer.schemas import LiteraturePaper

    return LiteraturePaper(**data)


def _guess_title(text: str) -> str | None:
    for line in text.splitlines():
        clean = re.sub(r"\s+", " ", line).strip()
        if 8 <= len(clean) <= 180:
            return clean
    return None


def _extract_sections(text: str) -> dict[str, str]:
    headings = re.compile(
        r"^\s*(?:[IVXLC]+|\d+(?:\.\d+)*)?\s*"
        r"(abstract|introduction|background|related work|methods?|methodology|"
        r"results?|discussion|conclusion|references|bibliography|appendix)\s*$",
        re.I | re.M,
    )
    matches = list(headings.finditer(text))
    if not matches:
        return {"paper_excerpt": re.sub(r"\s+", " ", text)[:800]}

    sections: dict[str, str] = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        name = re.sub(r"\s+", " ", match.group(1)).strip().title()
        excerpt = re.sub(r"\s+", " ", text[match.end():end]).strip()[:800]
        sections[name] = excerpt
    return sections


def _contains_physics_terms(text: str) -> bool:
    return bool(
        re.search(
            r"\b(quantum|photon|electron|field theory|thermodynamic|hamiltonian|"
            r"wavefunction|relativistic|spectr(?:um|a)|magnetic|superconduct)\b",
            text,
            re.I,
        )
    )


def build_review_graph():
    graph = StateGraph(ReviewState)
    graph.add_node("intake", intake_agent)
    graph.add_node("rule_extraction", rule_extraction_agent)
    graph.add_node("router", router_agent)
    graph.add_node("literature_search", literature_search_agent)
    graph.add_node("physics_check", physics_check_agent)
    graph.add_node("citation_check", citation_check_agent)
    graph.add_node("novelty_check", novelty_check_agent)
    graph.add_node("reproducibility_check", reproducibility_check_agent)
    graph.add_node("rubric_scoring", rubric_scoring_agent)
    graph.add_node("report_generation", report_generation_agent)

    graph.set_entry_point("intake")
    graph.add_edge("intake", "rule_extraction")
    graph.add_edge("rule_extraction", "router")
    graph.add_edge("router", "literature_search")
    graph.add_edge("literature_search", "physics_check")
    graph.add_edge("physics_check", "citation_check")
    graph.add_edge("citation_check", "novelty_check")
    graph.add_edge("novelty_check", "reproducibility_check")
    graph.add_edge("reproducibility_check", "rubric_scoring")
    graph.add_edge("rubric_scoring", "report_generation")
    graph.add_edge("report_generation", END)
    return graph.compile()


def review_paper(title: str | None, paper_text: str) -> ReviewResponse:
    settings = get_settings()
    key = cache_key(
        {
            "version": REVIEW_PIPELINE_VERSION,
            "paper_hash": cache_key(paper_text),
            "qwen_model": settings.qwen_model,
            "qwen_temperature": settings.qwen_temperature,
            "max_paper_chars": settings.max_paper_chars,
            "literature_search_enabled": settings.literature_search_enabled,
            "literature_search_limit": settings.literature_search_limit,
            "literature_max_distance": settings.literature_max_distance,
            "embedding_model": settings.qwen_embedding_model,
            "router_max_specialist_calls": settings.router_max_specialist_calls,
        }
    )
    cached = get_cached("paper_review", key, settings.review_cache_ttl_seconds)
    if isinstance(cached, dict):
        try:
            logger.info("Paper review cache hit key=%s", key[:12])
            return _response_with_title(ReviewResponse.model_validate(cached), title)
        except Exception:
            logger.warning("Ignoring invalid cached review for key=%s", key)

    with cache_lock("paper_review", key):
        cached = get_cached("paper_review", key, settings.review_cache_ttl_seconds)
        if isinstance(cached, dict):
            try:
                logger.info("Paper review cache hit after wait key=%s", key[:12])
                return _response_with_title(ReviewResponse.model_validate(cached), title)
            except Exception:
                logger.warning("Ignoring invalid cached review for key=%s", key)

        result = _review_paper_uncached(title, paper_text)
        set_cached("paper_review", key, result.model_dump(mode="json"))
        return result


def _response_with_title(result: ReviewResponse, title: str | None) -> ReviewResponse:
    if not title or result.report.title == title:
        return result
    copy = result.model_copy(deep=True)
    copy.report.title = title
    return copy


def _review_paper_uncached(title: str | None, paper_text: str) -> ReviewResponse:
    review_id = uuid.uuid4().hex
    app = build_review_graph()
    try:
        state = app.invoke(
            {"review_id": review_id, "title": title, "paper_text": paper_text, "findings": []}
        )
    finally:
        if get_settings().literature_search_enabled:
            try:
                get_knowledge_store(review_id).delete_collection()
            except Exception:
                logger.warning("Failed to clean up vector store collection for review %s", review_id, exc_info=True)

    return ReviewResponse(
        report=state["report"],
        findings=state.get("findings", []),
        literature=state.get("literature", []),
    )
