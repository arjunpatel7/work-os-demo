# Hand test: Agent Registration in Staging (2026-11-15)

One agent registered anonymously with curl, claimed by a person, and both access tokens decoded.

## Environment

| What | Value |
|---|---|
| AuthKit domain / issuer | `https://fortunate-aroma-74-staging.authkit.app` |
| JWKS (signing keys) | `https://fortunate-aroma-74-staging.authkit.app/oauth2/jwks` |
| Audience (`aud`) | `client_01M7GPP0885C97QVK8CAY04YXE` (the Staging client ID) |
| auth.md source | `https://fortunate-aroma-74-staging.authkit.app/agent/auth.md` |
| Permissions | `estimate:trial` (unverified), `estimate` (verified) |
| Claimed agents' org | Demo Customers, `org_01M7GQ4KVZE7TG7QXB9GH5MPKX` |

## Token before and after the claim

| Claim | Unclaimed | Claimed |
|---|---|---|
| `sub` | `agent_reg_01M7GQQRQ…` | same registration ID |
| `scope` | `estimate:trial` | `estimate` |
| `org_id` | a new throwaway org ("Cheerful Robot") | Demo Customers |
| `act` | absent | `{"sub": "user_01M7GQ6YX…"}` (the person) |
| `sub_profile` | `ai_agent` | `ai_agent` |
| lifetime | 900 s | 900 s |

Header: `alg: RS256`, with a `kid` to look up in the JWKS.

## What the API must do

- Verify signature (JWKS), `exp`, `iss` and `aud`.
- Read permissions from `scope`, a space-separated string, not `permissions`.
- Claimed = `scope` contains `estimate`. Count calls per `sub` otherwise.
- `sub` stays the same after the claim, so a claimed agent keeps its identity.

## Surprises (feedback for WorkOS)

1. Each anonymous registration creates its own organization with a random name. The docs don't say this. After the claim the agent moved to the person's org, but the throwaway org stayed behind with no users. Up to 100 an hour at the default limit.
2. Claiming an anonymous agent requires the Service auth method to be enabled. With only Anonymous on, the claim fails with `auth_method_disabled` and nothing in the dashboard warns you.
3. After enabling Service auth, the public metadata (`identity_types_supported`) kept listing only `anonymous` for several minutes, while the claim already worked. It caught up later, so it's a cache delay, not a bug.
4. The claim request uses `"type": "service_auth"` and needs the person's email as `login_hint`, so the agent has to ask its human for their email.
5. Docs say trusted/untrusted permissions; the dashboard says verified/unverified.
6. The Agent Registration docs don't mention claim expiration or the registration rate limits (100 per environment and 5 per IP per hour by default); the dashboard does.
7. A user created through the API with no password lands on a password prompt on the claim page. Google sign-in with the same email linked to that user.
8. The WorkOS MCP server has no operations for Agent Registration settings.
9. Agent Registration tokens include `sub_profile: "ai_agent"`, which the docs only list for Agent Auth tokens.
10. `https://<authkit>/agent/auth.md` returns 403 to Python's default User-Agent (`Python-urllib`) and 200 to others, so a server proxying it in Python must set its own User-Agent. The docs' proxy examples are all Node, nginx or Cloudflare, so they never hit this.
11. Agent permissions need two setup steps in two places. First, enable the permission for organization API keys (Authorization → Configuration). Then pick it under Agents → Configuration → Trust level permissions. The docs mention the first step in one sentence, and the trust-level lists are simply empty until it's done. Permission slugs can't be edited, so a rename means delete, re-create and redo both steps. Agents that already hold the old slug lose access.

## Still to check

- Is the throwaway org deleted when an unclaimed agent's claim window expires?
- Is the old unclaimed token revoked at claim time, or does it live until it expires?
