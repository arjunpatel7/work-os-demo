# Let Agents Sign Up and Use Your Tool with WorkOS Agent Registration

A FastAPI template that lets AI agents try your API with no signup. An agent registers itself anonymously with WorkOS and gets 3 free calls to a Jev search endpoint hosted on Modal. Then it asks its human to claim it, which unlocks unlimited use.

## Try it

Give a fresh Claude Code session only the URL:

> Use the docs search API at <your endpoint here> to find out how to start a claim for an anonymous WorkOS agent registration.

## How it works

With no token, the API returns a 401 that points the agent to `/auth.md`, WorkOS's registration steps with this API's trial rules on top. The agent registers anonymously with WorkOS and gets a token with the trial permission. The API checks the token against WorkOS's public keys and counts calls per agent. After 3 calls it returns `403 claim_required`. The agent asks its human for their email, the human signs in and reads it a code, and the agent's next token carries the full permission.

## Run your own

1. In the WorkOS dashboard:
   - enable the **Anonymous** and **Service auth** agent identity types
   - create a trial permission and a full permission, enable both for organization API keys, then map them under Agents → Configuration → Trust level permissions (trial → Untrusted, full → Trusted)
2. Copy `.env.example` to `.env` and fill it in. If your permission slugs differ, change `TRIAL_SCOPE` and `FULL_SCOPE` at the top of `app.py`.
3. Deploy with [uv](https://docs.astral.sh/uv/) and [Modal](https://modal.com). Modal reads `.env` when you deploy:
   ```bash
   uv sync
   uv run modal deploy --env workos-demo app.py
   ```
   The first deploy prints your URL. Put it in `.env` as `PUBLIC_URL` and deploy again.

## Use it for your own tool

Everything above `# --- Your tool goes here ---` in `app.py` is reusable. Replace the `docs_search` call and its request/response models with your tool. Protect the route with `Depends(enforce_trial)`, and call `count_trial_call` after a successful call.

This is a demo: the trial counter isn't atomic, and claimed agents have no rate limit. `notes/` has what I learned along the way, including feedback on Agent Registration.
