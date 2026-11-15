"""Token estimator API, deployed on Modal.

Piece 1: one public endpoint, no auth yet.

Run locally:   uv run uvicorn app:web_app --reload
Dev on Modal:  uv run modal serve --env workos-demo app.py
Deploy:        uv run modal deploy --env workos-demo app.py

Live URL: https://arjunpatel7-workos-demo--token-estimator-web.us-east.modal.run
"""

import modal
from fastapi import FastAPI
from pydantic import BaseModel, Field

from estimator import estimate_tokens

# --- The web app (plain FastAPI, runs anywhere) ---

web_app = FastAPI(
    title="Token Estimator",
    description="Estimates how many tokens a coding agent will spend on a task.",
)


class EstimateRequest(BaseModel):
    task: str = Field(..., min_length=1, max_length=4_000,
                      description="A description of the coding task.")


@web_app.get("/")
def home():
    return {
        "name": "Token Estimator",
        "usage": "POST /estimate with {\"task\": \"<what you are about to do>\"}",
    }


@web_app.post("/estimate")
def estimate(request: EstimateRequest):
    return estimate_tokens(request.task)


# --- Modal wrapping (only used when deploying to Modal) ---

image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install("fastapi>=0.163.1")
    .add_local_python_source("estimator")
)

app = modal.App("token-estimator", image=image)


@app.function()
@modal.asgi_app()
def web():
    return web_app
