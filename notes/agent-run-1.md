# Agent run 1: a fresh Claude Code session (2026-11-17)

> Written when the API was a token-estimator stub (`/estimate`, permissions `estimate:trial` / `estimate`). It's now Docs Search (`/search`, `jevsearch-trial` / `jevsearch-full`); the agent-side findings still apply.

Prompt, in an empty folder: "Evaluate this token estimator API for me and try it out: <URL>". No other hints.

## What the agent did

1. Probed the API: `/`, `/docs`, `/openapi.json`, `/health` (404), `/auth.md`, the `.well-known` metadata, then `/estimate` with no token and with a fake token (401s).
2. Read `/auth.md` and understood the rules up front: "WorkOS anonymous registration, which needs no email and gives 3 free estimates". The intro on top of WorkOS's file worked.
3. Tried to register anonymously and store it in the macOS keychain, as `auth.md` says. **Claude Code's auto mode blocked it** as "Unauthorized Access": creating a credential on a third-party service and writing to the keychain needed the user's OK. After "go ahead", it registered.
4. Used its trial, hit the 403 `claim_required`, and explained to the user what to do next.
5. Asked before sending the user's email. Tried to claim without `login_hint` first, got `invalid_request`, then asked for an email.
6. Gave the user the claim link, took the code, completed the claim, and kept going with unlimited calls (`free_calls_left` disappeared).

## The API's side (Modal logs)

- 20 requests in total. One registration: `agent_reg_01M7NYM2…`, org "Essential Pose".
- Trial: 200, 422, 200, then 403, 403. After the claim: unlimited, logged as `claimed=True` with the new person.

## What we learned about WorkOS

- **A brand-new person who claims an agent is added to the agent's anonymous org.** "Essential Pose" (made at registration) now has the `+agent1` user as its only member. An existing user with an org (the hand test) had the agent moved into their org instead. So the anonymous org becomes the new customer's account.
- The claim link (user code) lasted about 5 minutes. The agent flagged it as tight for a person.
- The anonymous registration expires after 1 hour (our claim-expiration setting); `auth.md` doesn't tell agents.
- Claiming needs the person's email; leaving it out gives a vague `invalid_request`.

## Bugs and fixes

- **Fixed: invalid requests used up free calls.** FastAPI runs dependencies before validating the body, so `enforce_trial` counted a call that then failed with 422. Counting now happens in the route, after validation.
- Not fixed, noted by the agent: the OpenAPI spec doesn't declare that a token is required, the 200 response has no schema, and there's no `/health`.
- The agent thought the token was opaque; it is a JWT. Its decoding attempt failed, not the token.

## Demo takeaways

- The whole flow works with no hints: discovery, anonymous signup, trial, 403, claim, unlimited.
- The agent's own guardrails asked the human before self-registering. Worth saying out loud: even "anonymous" signup gets a human glance in practice.
- The 403 wording worked: the agent relayed it accurately to the user.
