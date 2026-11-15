"""Token estimator API, deployed on Modal.

Piece 2: agents can discover where to sign up (WorkOS AuthKit).
Tokens are required but not yet verified; that's piece 3.

Run locally:   uv run uvicorn app:web_app --reload
Dev on Modal:  uv run modal serve --env workos-demo app.py
Deploy:        uv run modal deploy --env workos-demo app.py

Live URL: https://arjunpatel7-workos-demo--token-estimator-web.us-east.modal.run
"""

import os
import urllib.request

import modal
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from estimator import estimate_tokens

# --- Settings (public addresses, not secrets) ---

# Where agents sign up: your WorkOS AuthKit domain.
AUTHKIT_DOMAIN = os.environ.get(
    "AUTHKIT_DOMAIN", "https://fortunate-aroma-74-staging.authkit.app"
)
# This API's own public address.
PUBLIC_URL = os.environ.get(
    "PUBLIC_URL", "https://arjunpatel7-workos-demo--token-estimator-web.us-east.modal.run"
)
METADATA_URL = f"{PUBLIC_URL}/.well-known/oauth-protected-resource"

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
        "auth": f"Agents: read {PUBLIC_URL}/auth.md to get a token.",
    }


# --- Discovery: how an agent finds out where to sign up ---

@web_app.get("/auth.md", response_class=PlainTextResponse)
def auth_md():
    """Serve WorkOS's agent instructions, fetched live so they match the dashboard."""
    source = f"{AUTHKIT_DOMAIN}/agent/auth.md"
    # WorkOS blocks Python's default User-Agent ("Python-urllib"), so name ourselves.
    req = urllib.request.Request(source, headers={"User-Agent": "token-estimator-demo/0.1"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.read().decode()
    except OSError:
        raise HTTPException(
            status_code=502,
            detail=f"Couldn't fetch the agent instructions. Read them directly at {source}",
        )


@web_app.get("/.well-known/oauth-protected-resource")
def protected_resource_metadata():
    """Tells agents which server issues tokens for this API (RFC 9728)."""
    return {
        "resource": PUBLIC_URL,
        "resource_name": "Token Estimator",
        "authorization_servers": [AUTHKIT_DOMAIN],
        "bearer_methods_supported": ["header"],
        "scopes_supported": ["estimate:trial", "estimate"],
    }


# --- Auth ---

def require_token(request: Request) -> str:
    """Return the bearer token, or reply 401 with directions to sign up.

    Piece 2 only checks that a token is present. Piece 3 verifies it.
    """
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer ") or not header[7:].strip():
        raise HTTPException(
            status_code=401,
            detail={
                "error": "invalid_token",
                "message": f"This API needs an access token. Read {PUBLIC_URL}/auth.md "
                           "to register; it's free to try.",
            },
            headers={"WWW-Authenticate": f'Bearer resource_metadata="{METADATA_URL}"'},
        )
    return header[7:].strip()


@web_app.post("/estimate")
def estimate(body: EstimateRequest, request: Request):
    require_token(request)
    return estimate_tokens(body.task)


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
