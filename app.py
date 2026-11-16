"""Token estimator API, deployed on Modal.

Piece 3: agents discover where to sign up (WorkOS AuthKit), and every call
to /estimate needs a verified WorkOS agent token. No usage limits yet (piece 4).

Run locally:   uv run uvicorn app:web_app --reload
Dev on Modal:  uv run modal serve --env workos-demo app.py
Deploy:        uv run modal deploy --env workos-demo app.py

Live URL: https://arjunpatel7-workos-demo--token-estimator-web.us-east.modal.run
"""

import os
import urllib.request
from typing import Annotated

import jwt
import modal
from fastapi import Depends, FastAPI, HTTPException, Request
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
# Your WorkOS client ID. Agent tokens name it as their audience ("aud").
CLIENT_ID = os.environ.get("WORKOS_CLIENT_ID", "client_01M7GPP0885C97QVK8CAY04YXE")

METADATA_URL = f"{PUBLIC_URL}/.well-known/oauth-protected-resource"
USER_AGENT = "token-estimator-demo/0.1"

# Permissions (set in the WorkOS dashboard) that may call /estimate.
TRIAL_SCOPE = "estimate:trial"  # unclaimed agents
FULL_SCOPE = "estimate"         # claimed agents

# Fetches WorkOS's public signing keys once and caches them.
jwks_client = jwt.PyJWKClient(
    f"{AUTHKIT_DOMAIN}/oauth2/jwks", headers={"User-Agent": USER_AGENT}
)

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
    req = urllib.request.Request(source, headers={"User-Agent": USER_AGENT})
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

def unauthorized(message: str) -> HTTPException:
    """A 401 that points the agent to sign-up (the start of discovery)."""
    return HTTPException(
        status_code=401,
        detail={"error": "invalid_token", "message": message},
        headers={"WWW-Authenticate": f'Bearer resource_metadata="{METADATA_URL}"'},
    )


def verify_token(request: Request) -> dict:
    """Check the agent's WorkOS token and return its claims.

    401 if the token is missing, forged, expired or meant for someone else.
    403 if it's genuine but lacks a permission for this API.
    """
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer ") or not header[7:].strip():
        raise unauthorized(f"This API needs an access token. Read {PUBLIC_URL}/auth.md "
                           "to register; it's free to try.")
    token = header[7:].strip()

    try:
        # Find the WorkOS key that signed this token (by its "kid"), then check
        # the signature, expiry, issuer and audience in one call.
        key = jwks_client.get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            key,
            algorithms=["RS256"],
            issuer=AUTHKIT_DOMAIN,
            audience=CLIENT_ID,
            options={"require": ["exp", "iss", "aud", "sub"]},
        )
    except jwt.ExpiredSignatureError:
        raise unauthorized("Your access token expired. Exchange your identity assertion "
                           f"for a new one (step 4 of {PUBLIC_URL}/auth.md).")
    except jwt.PyJWTError:
        raise unauthorized(f"Invalid access token. Read {PUBLIC_URL}/auth.md to get one.")

    scopes = claims.get("scope", "").split()
    if TRIAL_SCOPE not in scopes and FULL_SCOPE not in scopes:
        raise HTTPException(
            status_code=403,
            detail={"error": "insufficient_scope",
                    "message": "This token isn't allowed to use the estimator."},
            headers={"WWW-Authenticate": f'Bearer error="insufficient_scope", '
                                         f'resource_metadata="{METADATA_URL}"'},
        )

    claims["claimed"] = FULL_SCOPE in scopes
    print(f"call agent={claims['sub']} claimed={claims['claimed']} "
          f"org={claims.get('org_id')} person={claims.get('act', {}).get('sub')}")
    return claims


@web_app.post("/estimate")
def estimate(body: EstimateRequest, claims: Annotated[dict, Depends(verify_token)]):
    return estimate_tokens(body.task)


# --- Modal wrapping (only used when deploying to Modal) ---

image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install("fastapi>=0.163.1", "pyjwt[crypto]>=2.15.1")
    .add_local_python_source("estimator")
)

app = modal.App("token-estimator", image=image)


@app.function()
@modal.asgi_app()
def web():
    return web_app
