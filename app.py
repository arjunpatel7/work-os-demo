"""Token estimator API, deployed on Modal.

Agents discover where to sign up (WorkOS AuthKit), every call to /estimate
needs a verified WorkOS agent token, and unclaimed agents get a few free
calls before they must ask their human to claim them.

Run locally:   uv run uvicorn app:web_app --reload
Dev on Modal:  uv run modal serve --env workos-demo app.py
Deploy:        uv run modal deploy --env workos-demo app.py

Live URL: https://arjunpatel7-workos-demo--token-estimator-web.us-east.modal.run
"""

import os
import urllib.request
from datetime import date
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

# The free trial for unclaimed agents. Change these for a live demo.
TRIAL_CALLS = int(os.environ.get("TRIAL_CALLS", "3"))           # per agent
DAILY_TRIAL_CAP = int(os.environ.get("DAILY_TRIAL_CAP", "500"))  # all unclaimed agents

# Call counts, kept in Modal's shared key-value store so they survive restarts.
# It behaves like a dict, so tests can swap in a plain {}.
usage = modal.Dict.from_name("token-estimator-usage", create_if_missing=True)

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

def auth_md_intro() -> str:
    """Our own note on top of WorkOS's instructions: the trial rules."""
    return (
        "# Token Estimator: how access works\n\n"
        f"- Register anonymously (below) and you get {TRIAL_CALLS} free estimates. "
        "No human needed.\n"
        "- After that, `POST /estimate` returns 403 `claim_required`. Ask your human "
        "to claim you (step 3 below). Claimed agents have unlimited use.\n"
        f"- Call it like this: `POST {PUBLIC_URL}/estimate` with "
        "`Authorization: Bearer <access_token>` and JSON `{\"task\": \"...\"}`.\n\n"
        "---\n\n"
    )


@web_app.get("/auth.md", response_class=PlainTextResponse)
def auth_md():
    """Serve WorkOS's agent instructions, fetched live so they match the dashboard,
    with our trial rules on top."""
    source = f"{AUTHKIT_DOMAIN}/agent/auth.md"
    # WorkOS blocks Python's default User-Agent ("Python-urllib"), so name ourselves.
    req = urllib.request.Request(source, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return auth_md_intro() + r.read().decode()
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


# --- The free trial ---

def claim_required(message: str) -> HTTPException:
    """A 403 telling an unclaimed agent to bring in its human."""
    return HTTPException(
        status_code=403,
        detail={
            "error": "claim_required",
            "message": f"{message} Ask your human to claim you: follow step 3 of "
                       f"{PUBLIC_URL}/auth.md (they open a link, sign in, and read you a "
                       "code). Claimed agents have unlimited use. Don't retry until "
                       "you're claimed.",
        },
        headers={"WWW-Authenticate": f'Bearer error="insufficient_scope", '
                                     f'scope="{FULL_SCOPE}", '
                                     f'resource_metadata="{METADATA_URL}"'},
    )


def enforce_trial(claims: Annotated[dict, Depends(verify_token)]) -> dict:
    """Let claimed agents through; count unclaimed agents' free calls."""
    if claims["claimed"]:
        return claims

    agent_key = f"agent:{claims['sub']}"
    day_key = f"day:{date.today().isoformat()}"  # a new key each day, so it resets itself

    if usage.get(day_key, 0) >= DAILY_TRIAL_CAP:
        raise claim_required("Free trial calls are used up for today.")
    used = usage.get(agent_key, 0)
    if used >= TRIAL_CALLS:
        raise claim_required(f"This agent has used its {TRIAL_CALLS} free estimates.")

    # Not atomic: two calls at the same instant could both slip through.
    # Fine for a trial; production would use Redis INCR or a SQL UPDATE.
    usage[agent_key] = used + 1
    usage[day_key] = usage.get(day_key, 0) + 1
    claims["free_calls_left"] = TRIAL_CALLS - used - 1
    return claims


@web_app.post("/estimate")
def estimate(body: EstimateRequest, claims: Annotated[dict, Depends(enforce_trial)]):
    result = estimate_tokens(body.task)
    if not claims["claimed"]:
        result["free_calls_left"] = claims["free_calls_left"]
    return result


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
