"""Docs search API for agents, deployed on Modal.

POST /search finds the docs sections that answer a question, using TypeSafe's
Jev to pick pages and then chunks from a site's llms.txt (see docs_search.py).

Agents discover where to sign up (WorkOS AuthKit), every call to /search
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
from typing import Annotated, Literal

import jwt
import modal
from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import PlainTextResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from docs_search import search as docs_search

# Docs sites agents can search. Only these are fetched: callers pick a name,
# never a URL. docs_search also refuses links that leave each index's own site.
SITES = {
    "workos": "https://workos.com/docs/llms.txt",
    "typesafe": "https://docs.typesafe.ai/llms.txt",
}

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
USER_AGENT = "docs-search-demo/0.1"

# Permissions (set in the WorkOS dashboard) that may call /search.
TRIAL_SCOPE = "jevsearch-trial"  # unclaimed agents
FULL_SCOPE = "jevsearch-full"    # claimed agents

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
    title="Docs Search",
    description="Returns the docs sections that answer a question, picked by TypeSafe's Jev.",
)


class SearchRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=1_000,
                          description="What you want to know.")
    site: Literal["workos", "typesafe"] = Field(description="Which docs to search.")


class SearchResult(BaseModel):
    url: str = Field(description="The docs page the chunk comes from.")
    relevance: float = Field(description="How well this chunk answers, 0 to 1.")
    text: str = Field(description="The chunk, with its heading path first.")


class SearchResponse(BaseModel):
    question: str
    verdict: Literal["answered", "partial", "not_found"] = Field(
        description="Whether the docs answer the question. Results are empty for not_found."
    )
    score: float = Field(description="Probability the returned chunks answer it.")
    results: list[SearchResult]
    pages_read: list[str]
    seconds: float
    free_calls_left: int | None = Field(
        None, description="Free calls left. Only present for unclaimed agents."
    )


@web_app.get("/health")
def health():
    """A cheap check that the service is up. No token needed, uses no free calls."""
    return {"status": "ok"}


@web_app.get("/")
def home():
    return {
        "name": "Docs Search",
        "usage": "POST /search with {\"question\": \"...\", \"site\": \"workos\" | \"typesafe\"}",
        "auth": f"Agents: read {PUBLIC_URL}/auth.md to get a token.",
    }


# --- Discovery: how an agent finds out where to sign up ---

def auth_md_intro() -> str:
    """Our own note on top of WorkOS's instructions: the trial rules."""
    return (
        "# Docs Search: how access works\n\n"
        f"- Register anonymously (below) and you get {TRIAL_CALLS} free searches. "
        "No human needed.\n"
        "- After that, `POST /search` returns 403 `claim_required`. Ask your human "
        "to claim you (step 3 below). Claimed agents have unlimited use.\n"
        f"- Call it like this: `POST {PUBLIC_URL}/search` with "
        "`Authorization: Bearer <access_token>` and JSON "
        "`{\"question\": \"...\", \"site\": \"workos\"}` (sites: workos, typesafe).\n\n"
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
        "resource_name": "Docs Search",
        "authorization_servers": [AUTHKIT_DOMAIN],
        "bearer_methods_supported": ["header"],
        "scopes_supported": [TRIAL_SCOPE, FULL_SCOPE],
    }


# --- Auth ---

def unauthorized(message: str) -> HTTPException:
    """A 401 that points the agent to sign-up (the start of discovery)."""
    return HTTPException(
        status_code=401,
        detail={"error": "invalid_token", "message": message},
        headers={"WWW-Authenticate": f'Bearer resource_metadata="{METADATA_URL}"'},
    )


# Reads "Authorization: Bearer <token>". Using FastAPI's helper also marks
# /search as token-protected in the generated docs (/docs, /openapi.json).
# auto_error=False so we send our own 401 with discovery directions.
bearer = HTTPBearer(auto_error=False, description="A WorkOS agent access token. See /auth.md.")


def verify_token(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> dict:
    """Check the agent's WorkOS token and return its claims.

    401 if the token is missing, forged, expired or meant for someone else.
    403 if it's genuine but lacks a permission for this API.
    """
    if credentials is None or not credentials.credentials.strip():
        raise unauthorized(f"This API needs an access token. Read {PUBLIC_URL}/auth.md "
                           "to register; it's free to try.")
    token = credentials.credentials.strip()

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
                    "message": "This token isn't allowed to use docs search."},
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
                       f"{PUBLIC_URL}/auth.md. Ask them for their email first; the claim "
                       "request needs it as login_hint. They open a link, sign in, and "
                       "read you a code. Claimed agents have unlimited use. Once the "
                       "claim is complete, get a fresh access token (step 4): your "
                       "current one still carries trial permissions. Don't retry "
                       "until then.",
        },
        headers={"WWW-Authenticate": f'Bearer error="insufficient_scope", '
                                     f'scope="{FULL_SCOPE}", '
                                     f'resource_metadata="{METADATA_URL}"'},
    )


def trial_keys(claims: dict) -> tuple[str, str]:
    agent_key = f"agent:{claims['sub']}"
    day_key = f"day:{date.today().isoformat()}"  # a new key each day, so it resets itself
    return agent_key, day_key


def enforce_trial(claims: Annotated[dict, Depends(verify_token)]) -> dict:
    """Refuse unclaimed agents that are out of free calls. Claimed agents pass.

    This only checks. Counting happens in the route, after FastAPI has
    validated the request body, so a rejected request never costs a call.
    """
    if claims["claimed"]:
        return claims
    agent_key, day_key = trial_keys(claims)
    if usage.get(day_key, 0) >= DAILY_TRIAL_CAP:
        raise claim_required("Free trial calls are used up for today.")
    if usage.get(agent_key, 0) >= TRIAL_CALLS:
        raise claim_required(f"This agent has used its {TRIAL_CALLS} free searches.")
    return claims


def count_trial_call(claims: dict) -> int:
    """Count one free call and return how many are left.

    Not atomic: two calls at the same instant could both slip through.
    Fine for a trial; production would use Redis INCR or a SQL UPDATE.
    """
    agent_key, day_key = trial_keys(claims)
    used = usage.get(agent_key, 0) + 1
    usage[agent_key] = used
    usage[day_key] = usage.get(day_key, 0) + 1
    return TRIAL_CALLS - used


# --- Your tool goes here ---
# Everything above is the reusable agent free trial: discovery, token checks,
# trial counting and the claim handoff. To put it in front of your own API,
# swap the docs_search call below (and the request/response models) for your
# tool, and protect any new route with Depends(enforce_trial).

@web_app.post("/search", response_model=SearchResponse, response_model_exclude_none=True)
def search(body: SearchRequest, claims: Annotated[dict, Depends(enforce_trial)]):
    # Reaching here means the token, the trial and the body are all valid.
    try:
        result = docs_search(body.question, SITES[body.site])
    except Exception as e:  # a failed search is an error, never an empty answer
        print(f"search failed: {e!r}")
        raise HTTPException(status_code=502,
                            detail="Docs search is unavailable right now. Try again shortly.")
    # Count the free call only after a successful search.
    if not claims["claimed"]:
        result["free_calls_left"] = count_trial_call(claims)
    return result


# --- Modal wrapping (only used when deploying to Modal) ---

image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install("fastapi>=0.163.1", "pyjwt[crypto]>=2.15.1")
    .add_local_python_source("docs_search")
)

app = modal.App("token-estimator", image=image)


# TYPESAFE_API_KEY comes from a Modal secret named "typesafe". Create it once:
#   uv run modal secret create typesafe --env workos-demo TYPESAFE_API_KEY=<key>
@app.function(secrets=[modal.Secret.from_name("typesafe")])
@modal.asgi_app()
def web():
    return web_app
