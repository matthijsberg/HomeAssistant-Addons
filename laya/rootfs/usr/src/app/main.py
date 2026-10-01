"""FastAPI HTTP service for Laya Decision Router."""

import asyncio
import hmac
import logging
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional, Union

from config import AppConfig
from engine import LayaRouterEngine
from fastapi import Depends, FastAPI, Header, HTTPException, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from question_sets import list_question_sets

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("laya.main")

config = AppConfig.load()
engine = LayaRouterEngine(config)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: initialize and preload models in background before serving traffic."""
    logger.info("Laya Router starting up...")
    loop = asyncio.get_running_loop()
    loop.run_in_executor(None, engine.initialize)
    yield
    logger.info("Laya Router shutting down...")


app = FastAPI(
    title="Laya Router",
    description="Local System 1 decision engine & Gemini router for Home Assistant and Hermes Agent",
    version="0.2.0",
    lifespan=lifespan,
)


def verify_api_key(authorization: Optional[str] = Header(default=None)) -> None:
    """Validate Bearer authentication against configured LAYA_API_KEY."""
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


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        log_level=config.log_level.lower(),
    )
