from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, HTTPException, Path as PathParam, Query
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .store import (
    IdempotencyConflictError,
    create_transaction,
    get_ranking,
    get_user_summary,
    init_db,
    seed_demo_data,
    validate_user_id,
)


ROOT_DIR = Path(__file__).resolve().parents[1]
STATIC_DIR = ROOT_DIR / "static"

app = FastAPI(title="Transaction Ranking Service", version="1.0.0")

cors_origins = [origin.strip() for origin in os.getenv("CORS_ORIGINS", "").split(",") if origin.strip()]
if cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


class TransactionInput(BaseModel):
    userId: str = Field(..., min_length=2, max_length=32)
    amount: int = Field(..., ge=1, le=10000)
    idempotencyKey: str = Field(..., min_length=8, max_length=128)
    note: str | None = Field(default=None, max_length=120)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(_, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={
            "error": "Validation failed",
            "details": exc.errors(),
        },
    )


@app.on_event("startup")
def startup() -> None:
    init_db()
    seed_demo_data()


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def home() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.post("/transaction")
def post_transaction(payload: TransactionInput):
    try:
        user_id = validate_user_id(payload.userId)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if payload.idempotencyKey.strip() != payload.idempotencyKey:
        raise HTTPException(status_code=422, detail="idempotencyKey must not contain leading or trailing spaces")

    note = payload.note.strip() if payload.note and payload.note.strip() else None

    try:
        saved = create_transaction(
            user_id=user_id,
            amount=payload.amount,
            idempotency_key=payload.idempotencyKey,
            note=note,
        )
    except IdempotencyConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return {
        "message": "Transaction recorded",
        "transaction": {
            "idempotencyKey": saved["idempotency_key"],
            "userId": saved["user_id"],
            "rawAmount": saved["raw_amount"],
            "effectivePoints": saved["effective_points"],
            "note": saved["note"],
            "createdAt": saved["created_at"],
        },
    }


@app.get("/summary/{user_id}")
def summary(
    user_id: Annotated[str, PathParam(min_length=2, max_length=32)],
):
    try:
        cleaned = validate_user_id(user_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    summary_data = get_user_summary(cleaned)
    if not summary_data:
        raise HTTPException(status_code=404, detail="User not found")
    return summary_data


@app.get("/ranking")
def ranking(limit: Annotated[int, Query(ge=1, le=100)] = 10):
    return {
        "limit": limit,
        "ranking": get_ranking(limit=limit),
    }
