"""FastAPI HTTP service for Laya Decision Router."""

import asyncio
import hmac
import json
import logging
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional, Union

from config import AppConfig
from domotica import DomoticaAction, DomoticaEngine
from engine import LayaRouterEngine
from fastapi import Depends, FastAPI, Header, HTTPException, status
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from question_sets import list_question_sets
from resolver import HAResolver
from ui import render_gui_html

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("laya.main")

config = AppConfig.load()
engine = LayaRouterEngine(config)
ha_resolver = HAResolver()
domotica_engine = DomoticaEngine(ha_resolver)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: initialize models and sync HA registry in background."""
    logger.info("Laya Router v2.0 starting up...")
    loop = asyncio.get_running_loop()
    loop.run_in_executor(None, engine.initialize)
    loop.run_in_executor(None, ha_resolver.sync)
    yield
    logger.info("Laya Router shutting down...")


app = FastAPI(
    title="Laya Router",
    description="Local sub-50ms System 1 decision engine, fast-path domotica controller, and OpenAI conversation bridge for Home Assistant",
    version="2.0.0",
    lifespan=lifespan,
)


def verify_api_key(
    authorization: Optional[str] = Header(default=None),
    x_ingress_path: Optional[str] = Header(default=None),
) -> None:
    """Validate Bearer authentication against configured LAYA_API_KEY.

    Requests passing through Home Assistant Ingress (carrying X-Ingress-Path)
    are verified by the Home Assistant Core security perimeter.
    """
    if x_ingress_path:
        return

    if not config.api_key:
        return

    expected = f"Bearer {config.api_key}".encode("utf-8")
    supplied = (authorization or "").encode("utf-8")

    if not hmac.compare_digest(supplied, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing bearer token.",
            headers={"WWW-Authenticate": "Bearer"},
        )


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    """Home Assistant Ingress WebUI playground."""
    return HTMLResponse(content=render_gui_html())


class TurnItem(BaseModel):
    role: Optional[str] = None
    content: str


class RouteRequest(BaseModel):
    prompt: str = Field(..., min_length=1, description="The latest user message to route")
    recent_turns: List[Union[str, TurnItem, Dict[str, Any]]] = Field(
        default_factory=list,
        description="Previous turns (max 2 used for context window)",
    )
    question_set: str = Field(
        default="hermes-v1",
        description="Question set key containing criteria and categories",
    )
    session_id: Optional[str] = Field(
        default=None,
        description="Optional session tracking identifier",
    )


class RouteConfidence(BaseModel):
    family: float
    effort: float


class RouteResponse(BaseModel):
    family: str
    effort: str
    confidence: RouteConfidence
    checkpoint: str
    latency_ms: float
    question_set: str
    provider: str = Field(description="Active upstream model provider")
    model: str = Field(description="Target model identifier mapped to the resolved task family")
    max_tokens: Optional[int] = Field(default=None, description="Maximum output token budget for the model")
    temperature: Optional[float] = Field(default=None, description="Sampling temperature override")
    thinking_budget: Optional[int] = Field(default=None, description="Internal reasoning thinking token budget")
    needs_memory: Optional[bool] = Field(default=None, description="Whether memory context lookup is required")
    allowed_tools: Optional[List[str]] = Field(default=None, description="Whitelisted tool identifiers for this turn")
    override_applied: Optional[bool] = Field(default=None, description="Whether this decision was served via an exact sticky override")
    override_author: Optional[str] = Field(default=None, description="Author of the sticky override")
    fallback_reason: Optional[str] = Field(default=None, description="Reason for fallback or confidence gating")


@app.get("/health")
def health() -> Dict[str, Any]:
    """Health and readiness probe for Home Assistant watchdog and monitoring."""
    is_ready = engine.ready
    return {
        "status": "ok" if is_ready else "loading",
        "ready": is_ready,
        "loaded": engine.loaded_checkpoints,
        "device": engine.device,
        "laya_version": engine.laya_version,
        "revisions": {
            "pinned": config.laya_revision,
        },
        "provider": config.provider,
    }


@app.get("/v1/models", dependencies=[Depends(verify_api_key)])
def get_models_config() -> Dict[str, Any]:
    """Return active provider, model mappings, family profiles, and architecture notes."""
    return {
        "provider": config.provider,
        "gateway_url": config.gateway_url,
        "models": config.get_models_map(),
        "families": {name: f.to_dict() for name, f in config.families.items()},
        "supported_providers": ["gemini", "litellm", "openrouter", "custom"],
        "architecture_limitation_note": (
            "LLM client integrations cannot switch provider credentials dynamically mid-session. "
            "Routing across multiple upstream provider vendors (e.g. Anthropic, Google, OpenAI) "
            "requires a unified gateway (LiteLLM or OpenRouter) configured as the client's single provider endpoint."
        ),
    }


@app.get("/v1/question-sets", dependencies=[Depends(verify_api_key)])
def get_question_sets() -> Dict[str, Any]:
    """Return all active question sets and their criteria text for inspection/debugging."""
    return {
        "question_sets": list_question_sets(),
        "default": "hermes-v1",
    }


@app.post(
    "/v1/route",
    response_model=RouteResponse,
    dependencies=[Depends(verify_api_key)],
)
def route_turn(req: RouteRequest) -> RouteResponse:
    """Decision endpoint for one user turn.

    Categorizes task_family and reasoning effort using local System 1 weights.
    """
    if not engine.ready:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Laya decision engine is still preloading checkpoints.",
        )

    try:
        decision = engine.route(
            prompt=req.prompt,
            recent_turns=req.recent_turns,
            question_set=req.question_set,
            session_id=req.session_id,
        )
        return RouteResponse(**decision)
    except KeyError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except Exception as e:
        logger.exception("Routing failed for prompt: %s", req.prompt[:100])
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Inference error: {e}",
        ) from e


@app.post("/v1/systemone", dependencies=[Depends(verify_api_key)])
def systemone_passthrough(body: Dict[str, Any]) -> JSONResponse:
    """Raw Jev-compatible wire protocol passthrough."""
    if not engine.ready:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Laya decision engine is still preloading checkpoints.",
        )

    try:
        result = engine.systemone(body)
        return JSONResponse(content=result)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)) from e
    except Exception as e:
        logger.exception("Systemone inference error")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Inference failed",
        ) from e


class DomoticaRouteRequest(BaseModel):
    prompt: str = Field(description="Natural language home automation command")
    execute: bool = Field(default=False, description="Whether to execute the resolved service against Home Assistant REST API")


class ChatCompletionRequest(BaseModel):
    model: Optional[str] = Field(default="laya-v2", description="Requested model identifier")
    messages: List[Dict[str, Any]] = Field(description="Conversation messages")
    tools: Optional[List[Dict[str, Any]]] = None
    tool_choice: Optional[Union[str, Dict[str, Any]]] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    stream: Optional[bool] = False


@app.post(
    "/v1/domotica/route",
    response_model=DomoticaAction,
    dependencies=[Depends(verify_api_key)],
)
def route_domotica_action(req: DomoticaRouteRequest) -> DomoticaAction:
    """Fast-Path Domotica endpoint: resolves commands directly to HA actions in <50ms."""
    try:
        return domotica_engine.route_and_execute(req.prompt, execute=req.execute)
    except Exception as exc:
        logger.exception("Domotica routing error for prompt: %s", req.prompt[:100])
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Domotica routing error: {exc}",
        ) from exc


@app.post("/v1/domotica/sync", dependencies=[Depends(verify_api_key)])
def sync_domotica_registry() -> Dict[str, Any]:
    """Force an immediate refresh of cached Home Assistant entities and areas."""
    success = ha_resolver.sync(force=True)
    return {
        "synced": success,
        "entity_count": len(ha_resolver._entities),
        "last_sync": ha_resolver._last_sync_time,
    }


class CorrectionRequest(BaseModel):
    prompt: str = Field(description="Exact prompt to override")
    family: str = Field(description="Target task family: quick, smarthome, general, code, deep")
    effort: Optional[str] = Field(default=None, description="Reasoning effort: light, normal, deep, high")
    tools: Optional[str] = Field(default=None, description="Tool policy: none, all, ha_only")
    memory: Optional[str] = Field(default=None, description="Memory policy: none, full, gated")
    author: Optional[str] = Field(default="user", description="Author identifier")


@app.post("/v1/corrections", dependencies=[Depends(verify_api_key)])
def add_correction(req: CorrectionRequest) -> Dict[str, Any]:
    """Register an exact normalized prompt override and queue candidate for review (EV-03, OB-03)."""
    target_family = req.family.strip().lower()
    if target_family not in config.families:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown family '{req.family}'. Available families: {list(config.families.keys())}",
        )
    entry = engine.save_override(
        prompt=req.prompt,
        family=target_family,
        effort=req.effort,
        tools=req.tools,
        memory=req.memory,
        author=req.author or "user",
    )
    return {"success": True, "override": entry, "active_count": len(engine.overrides)}


@app.get("/v1/corrections", dependencies=[Depends(verify_api_key)])
def list_corrections() -> Dict[str, Any]:
    """List all registered overrides with hit stats."""
    return {
        "overrides": list(engine.overrides.values()),
        "total_count": len(engine.overrides),
    }


@app.post("/v1/chat/completions", dependencies=[Depends(verify_api_key)])
def chat_completions(req: ChatCompletionRequest) -> Any:
    """OpenAI-compatible chat completions endpoint for Home Assistant Conversation / Assist."""
    # Extract latest user message
    user_prompt = ""
    for msg in reversed(req.messages):
        if isinstance(msg, dict) and msg.get("role") == "user":
            content = msg.get("content")
            if isinstance(content, str) and content.strip():
                user_prompt = content.strip()
                break

    action = domotica_engine.parse(user_prompt)
    import time
    created_ts = int(time.time())
    resp_id = f"chatcmpl-laya-{created_ts}"

    if req.stream:
        async def event_generator():
            if action.fast_path and action.openai_tool_call:
                # Stream tool call delta
                chunk_delta = {
                    "id": resp_id,
                    "object": "chat.completion.chunk",
                    "created": created_ts,
                    "model": "laya-v2-domotica",
                    "choices": [{
                        "index": 0,
                        "delta": {
                            "role": "assistant",
                            "tool_calls": [action.openai_tool_call],
                        },
                        "finish_reason": None,
                    }],
                }
                yield f"data: {json.dumps(chunk_delta)}\n\n"
                chunk_end = {
                    "id": resp_id,
                    "object": "chat.completion.chunk",
                    "created": created_ts,
                    "model": "laya-v2-domotica",
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}],
                }
                yield f"data: {json.dumps(chunk_end)}\n\n"
            else:
                text_content = (
                    f"Opdracht ontvangen voor {action.domain or 'apparaat'}: "
                    f"{action.service or 'actie'} op {action.target_name or 'bestemming'}."
                    if action.is_domotica
                    else f"Laya Router heeft je vraag ontvangen: '{user_prompt}'."
                )
                chunk_text = {
                    "id": resp_id,
                    "object": "chat.completion.chunk",
                    "created": created_ts,
                    "model": "laya-v2",
                    "choices": [{
                        "index": 0,
                        "delta": {"role": "assistant", "content": text_content},
                        "finish_reason": None,
                    }],
                }
                yield f"data: {json.dumps(chunk_text)}\n\n"
                chunk_end = {
                    "id": resp_id,
                    "object": "chat.completion.chunk",
                    "created": created_ts,
                    "model": "laya-v2",
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                }
                yield f"data: {json.dumps(chunk_end)}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(event_generator(), media_type="text/event-stream")

    # Non-streaming response
    if action.fast_path and action.openai_tool_call and req.tools:
        # Return tool call for HA Assist
        return {
            "id": resp_id,
            "object": "chat.completion",
            "created": created_ts,
            "model": "laya-v2-domotica",
            "choices": [{
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [action.openai_tool_call],
                },
                "finish_reason": "tool_calls",
            }],
            "usage": {"prompt_tokens": 15, "completion_tokens": 20, "total_tokens": 35},
        }

    # Text message fallback
    content = (
        f"{action.domain.capitalize() if action.domain else 'Apparaat'} "
        f"{action.service or 'actie'} uitgevoerd voor {action.target_name or 'geselecteerde ruimte'}."
        if action.is_domotica
        else f"Laya verwerkt: '{user_prompt}'."
    )
    return {
        "id": resp_id,
        "object": "chat.completion",
        "created": created_ts,
        "model": "laya-v2",
        "choices": [{
            "index": 0,
            "message": {
                "role": "assistant",
                "content": content,
            },
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": 10, "completion_tokens": 15, "total_tokens": 25},
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        log_level=config.log_level.lower(),
    )
