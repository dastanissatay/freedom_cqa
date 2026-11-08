"""CQA — точка входа FastAPI.

Запуск (из корня репозитория):  uvicorn app.main:app --app-dir backend --reload
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import analytics, appeals, batch, checklists, dataset, db, evaluator, export, references
from .config import ROOT, get_settings
from .llm.pricing import price_for


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    db.init(s.db_file)
    checklists.ensure_seed()
    yield


app = FastAPI(title="CQA — Conversation Quality Analytics", version="1.0", lifespan=lifespan)

origins = [o.strip() for o in get_settings().cors_origins.split(",") if o.strip()]
app.add_middleware(CORSMiddleware, allow_origins=origins or ["*"], allow_methods=["*"], allow_headers=["*"])

for r in (dataset.router, checklists.router, evaluator.router, appeals.router, batch.router,
          references.router, references.quality_router, export.router, analytics.router):
    app.include_router(r)


@app.get("/api/config")
def api_config():
    s = get_settings()
    return {
        "provider": s.llm_provider,
        "model": s.llm_model if s.llm_provider != "mock" else "mock",
        "effort": s.llm_effort if s.llm_provider == "anthropic" else None,
        "price_per_m": price_for("mock" if s.llm_provider == "mock" else s.llm_model),
        "default_group_id": s.default_group_id,
        "dataset_api_configured": bool(s.dataset_api_url),
        "pii_masking": s.pii_masking,
        "team_name": s.team_name,
    }


@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse("/web/index.html")


app.mount("/web", StaticFiles(directory=ROOT / "web", html=True), name="web")
