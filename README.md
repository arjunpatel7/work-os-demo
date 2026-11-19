# Agent free trial: a claimable API with WorkOS Agent Registration

A small FastAPI template that lets any AI agent try your API in seconds. You don't sign anyone up or hand out keys, and an agent can't run up an unlimited bill.

An agent finds the API on its own and **registers itself anonymously** with WorkOS. It gets **3 free calls**, then a `403 claim_required` that tells it to **ask its human to claim it**. Once the human claims it, the same agent has unlimited use, tied to that person and their organization.

The example tool behind the trial is **Docs Search**. It finds the sections of a docs site (from its `llms.txt`) that answer a question, using TypeSafe's Jev model. On our 11-question test it answered WorkOS docs questions about as accurately as Claude Code's own web search, in less than half the time and at about 30% lower cost.

## Who it's for

This is for anyone shipping an API or tool for agents who wants agents to self-serve a trial without a human signing up first. The idea is the same one Neon uses for [claimable Postgres](https://workos.com/blog/neon-claimable-postgres-auth-md-case-study). [Teaching your API to onboard AI agents](https://workos.com/blog/teaching-your-api-to-onboard-ai-agents) shows the agent's side. This repo is the server side you can run.

## How it works

1. **Discovery.** The agent calls `POST /search` with no token and gets a `401` with `WWW-Authenticate: Bearer resource_metadata="…/.well-known/oauth-protected-resource"`. That metadata points to the WorkOS AuthKit domain. `/auth.md` serves WorkOS's agent instructions, with this API's trial rules on top.
2. **Anonymous registration.** Following `auth.md`, the agent registers with WorkOS. It needs no human or browser for this. It exchanges its identity for a short-lived access token carrying the trial permission.
3. **Metered trial.** The API checks the token locally against WorkOS's public keys (JWKS). It counts calls per agent (`sub`), with a daily cap across all unclaimed agents. Each response includes `free_calls_left`.
4. **Human claim.** When the free calls run out, the API returns `403 claim_required` with instructions. The agent asks its human for their email and starts the claim. The human signs in and reads the agent a code. The agent's next token keeps the same `sub` but now carries the full permission, the human (`act`) and their organization (`org_id`).

| Piece | Where |
|---|---|
| Protected-resource metadata and `/auth.md` | `app.py`, "Discovery" section |
| Token verification (`verify_token`) | `app.py`, "Auth" section |
| Trial counting and the claim prompt (`enforce_trial`, `claim_required`) | `app.py`, "The free trial" section |
| The tool itself | `docs_search.py`, called from the `/search` route |

## Use it for your own API

1. Point the settings at the top of `app.py` to your WorkOS environment: `AUTHKIT_DOMAIN`, `WORKOS_CLIENT_ID`, `PUBLIC_URL`.
2. Set `TRIAL_SCOPE` and `FULL_SCOPE` to the permission slugs you create in WorkOS (below).
3. Under `# --- Your tool goes here ---`, replace the `docs_search` call and the request/response models with your tool. Protect any new route with `Depends(enforce_trial)`, or `Depends(verify_token)` if it shouldn't use up free calls.

## WorkOS setup

In the WorkOS dashboard, for your environment:

1. **Agent identity types:** enable **Anonymous** and **Service auth**. Service auth is required for claims.
2. **Create two permissions.** In this repo they are `jevsearch-trial` and `jevsearch-full`. This takes two separate steps:
   - **Allow them on organization API keys** (Authorization → Configuration → Organization API key permissions). Only permissions enabled here can be given to agents; until then, the next step's lists won't show them.
   - **Map them by trust level** (Agents → Configuration → Trust level permissions). Put the trial permission under **Untrusted** (anonymous agents) and the full one under **Trusted** (claimed agents). The dashboard may label these unverified and verified.

   Renaming a permission means deleting it and creating a new one (the slug can't be edited), then doing both steps again.
3. **Copy two URLs from the Agents configuration page** (both read-only): the **auth.md source**, which `/auth.md` reverse-proxies, and the **authorization server URL**, whose domain goes in `AUTHKIT_DOMAIN`. WorkOS doesn't need to know this API's URL.
4. **Authentication:** enable a sign-in method the claiming human can use without a password, such as Google or Magic Auth.

## Run it

Requires [uv](https://docs.astral.sh/uv/), a [Modal](https://modal.com) account and a TypeSafe API key.

```bash
uv sync
echo "TYPESAFE_API_KEY=<your key>" > .env       # used for local runs
uv run uvicorn app:web_app --reload            # local
uv run modal secret create typesafe --env workos-demo TYPESAFE_API_KEY=<your key>
uv run modal deploy --env workos-demo app.py   # deploy
```

Local runs share the deployed app's trial counts (a Modal `Dict`).

## Try it

Give a fresh coding agent (for example Claude Code) only the URL:

> Use the docs search API at `<PUBLIC_URL>` to find out how to start a claim for an anonymous WorkOS agent registration.

It should hit the 401, read `/auth.md`, register itself, search, and eventually ask you to claim it.

## Production notes

This is a demo. Before charging anyone:

- **Atomic counting.** The trial counter isn't atomic, so parallel calls can each get a free search. Use Redis `INCR` or a SQL `UPDATE`.
- **Revocation.** Tokens are checked locally and live about 15 minutes. Use WorkOS's token validation endpoint if you need instant revocation.
- **Daily cap.** One abuser re-registering could use up the shared daily cap for everyone. WorkOS rate-limits registrations, but consider per-IP or per-org limits too.
- **Claimed agents are unlimited.** Add a rate limit or billing per organization.
- **Fetch safety.** The docs fetcher only follows same-site HTTPS links from allow-listed sites. It doesn't re-check redirects.

`notes/` has what I learned along the way, including feedback on Agent Registration.
