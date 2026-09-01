# AgentCore Identity — 2LO and 3LO Outbound Authorization

How an agent calls a downstream API **as itself** (2LO) or **as the end user** (3LO)
without hand-rolling OAuth. AgentCore Identity owns the client secret, the PKCE/`state`
machinery, the code exchange, per-user token storage, and refresh.

> **Validated against `bedrock-agentcore` 1.22.0.** Grant types confirmed against the live
> `bedrock-agentcore-control` API model: `CLIENT_CREDENTIALS`, `AUTHORIZATION_CODE`,
> `TOKEN_EXCHANGE`.

## Table of Contents

1. [Inbound vs Outbound Auth](#inbound-vs-outbound)
2. [2LO vs 3LO — Which Do You Need?](#2lo-vs-3lo)
3. [Core Concepts](#core-concepts)
4. [Credential Providers](#credential-providers)
5. [2LO: Client Credentials](#2lo-client-credentials)
6. [3LO: User Federation](#3lo-user-federation)
7. [Three Ways to Wire 3LO](#three-ways-to-wire-3lo)
8. [What This Replaces](#what-this-replaces)
9. [Gotchas](#gotchas)
10. [IAM Permissions](#iam-permissions)

---

## Inbound vs Outbound

Two independent problems that are easy to conflate:

| | Question | Mechanism |
|---|---|---|
| **Inbound** | Who is calling *my* agent? | Runtime/Gateway `authorizerType: CUSTOM_JWT` with your OIDC discovery URL, or SigV4 |
| **Outbound** | How does my agent authenticate to a *downstream* API? | AgentCore Identity credential providers + token vault |

```
End user ──JWT──▶ Gateway (CUSTOM_JWT validates inbound)
                     │
                     │  outbound: fetch/refresh the downstream token
                     ▼
                  Token Vault ──▶ downstream IdP (Okta, ServiceNow, Google, …)
                     │
                     │  injects Authorization: Bearer <downstream token>
                     ▼
                  Your MCP server / API
```

The inbound JWT's `sub` claim becomes the **vault key** for 3LO. That is the whole trick:
the identity you authenticated becomes the identity whose downstream token you look up.

---

## 2LO vs 3LO

| | 2LO (two-legged) | 3LO (three-legged) |
|---|---|---|
| OAuth grant | `client_credentials` | `authorization_code` + PKCE |
| `auth_flow` / `grantType` | `M2M` / `CLIENT_CREDENTIALS` | `USER_FEDERATION` / `AUTHORIZATION_CODE` |
| Acts as | The application itself | The end user |
| Consent | None | User consents once, in a browser |
| Token scope | One token for the whole workload | One token **per user** |
| Downstream sees | A service account | The real user; their ACLs apply |
| Attribution | All actions attributed to the service account | Actions attributed correctly |

**Choose 3LO when the downstream system has per-user permissions you must respect.** If a
ServiceNow incident should be attributed to the person who asked for it, and ServiceNow ACLs
should limit what they can read, 2LO is wrong no matter how convenient — a service account
sees everything and attributes everything to itself.

**Choose 2LO when the agent legitimately acts on its own behalf**: reading a shared product
catalog, writing to a system-owned audit log, calling an internal service with no user model.

There is also `TOKEN_EXCHANGE` (RFC 8693), plus a `JWT_AUTHORIZATION_GRANT` variant, for
exchanging the inbound token directly for a downstream one — useful when the downstream IdP
trusts your inbound issuer and you want to skip interactive consent entirely.

---

## Core Concepts

| Concept | What it is |
|---|---|
| **Workload identity** | The agent's identity in the directory. Carries `allowedResourceOauth2ReturnUrls`. Vault entries are keyed by (workload identity + user `sub`). |
| **Credential provider** | Stored config for one downstream IdP: client ID, client secret, endpoints. Returns a `callbackUrl` you must register with the IdP. |
| **Token vault** | Encrypted store (AWS Secrets Manager, optional CMK) holding access + refresh tokens. Refreshes automatically. |

---

## Credential Providers

### Built-in vendors

`GoogleOauth2`, `GithubOauth2`, `SlackOauth2`, `SalesforceOauth2`, `MicrosoftOauth2`,
`AtlassianOauth2`, `LinkedinOauth2`, `XOauth2`, `OktaOauth2`, `Auth0Oauth2`,
`CognitoOauth2`, and others. Endpoints are known; supply only credentials.

```python
identity_create_oauth2_provider(
    name="github-provider",
    credential_provider_vendor="GithubOauth2",
    oauth2_provider_config_input={
        "githubOauth2ProviderConfig": {"clientId": "Iv1.abc...", "clientSecret": "..."}
    },
)
# Response includes callbackUrl — register it with the IdP's OAuth app.
```

### CustomOauth2 with discovery

```python
{"customOauth2ProviderConfig": {
    "clientId": "...", "clientSecret": "...",
    "oauthDiscovery": {
        "discoveryUrl": "https://idp.example.com/.well-known/openid-configuration"
    },
}}
```

### CustomOauth2 with manual endpoints

Required when the IdP has no reliable OIDC discovery document. ServiceNow is the canonical
case — it exposes `/oauth_auth.do` and `/oauth_token.do` and no discovery doc:

```python
{"customOauth2ProviderConfig": {
    "clientId": "...",
    "clientSecret": "...",
    "clientAuthenticationMethod": "CLIENT_SECRET_POST",
    "oauthDiscovery": {
        "authorizationServerMetadata": {
            "issuer": "https://acme.service-now.com",
            "authorizationEndpoint": "https://acme.service-now.com/oauth_auth.do",
            "tokenEndpoint": "https://acme.service-now.com/oauth_token.do",
            "responseTypes": ["code"],
            # Do NOT also set tokenEndpointAuthMethods here — see Gotchas.
        }
    },
}}
```

### Client secret: managed vs external

| `clientSecretSource` | Behaviour |
|---|---|
| `MANAGED` (default) | Pass `clientSecret` inline; AgentCore stores it in Secrets Manager |
| `EXTERNAL` | Point at a secret you already own: `clientSecretConfig={"secretId": arn, "jsonKey": "..."}` |

Use `EXTERNAL` when the secret is already under rotation by another system.

> **Create providers with the CLI, not through a chat assistant.** The MCP
> `identity_create_oauth2_provider` tool takes `clientSecret` as a parameter, so driving it
> from an LLM conversation puts a production secret into chat history and observability
> tooling. `agentcore add credential --type oauth --client-secret ...` reads it from the
> shell instead. Reserve the MCP tools for test credentials and scripted automation.

```bash
agentcore add credential \
  --name MyOAuthProvider --type oauth \
  --discovery-url https://idp.example.com/.well-known/openid-configuration \
  --client-id my-client-id --client-secret my-client-secret \
  --scopes read,write
```

---

## 2LO: Client Credentials

### In agent code

```python
from bedrock_agentcore.identity.auth import requires_access_token

@requires_access_token(
    provider_name="my-service-provider",
    auth_flow="M2M",
    scopes=["catalog.read"],
)
async def fetch_catalog(*, access_token: str):
    # Injected at call time. Never enters the model's context.
    return httpx.get(URL, headers={"Authorization": f"Bearer {access_token}"}).json()
```

The decorator injects `access_token` as a keyword argument. **Do not return it, log it, or
put it in a tool result** — that would place a live credential into the conversation, where
it is one prompt injection away from exfiltration.

For static keys there is `@requires_api_key`; for SigV4-style AWS auth,
`@requires_iam_access_token`.

### On a Gateway target

```python
credential_provider_configurations=[{
    "credentialProviderType": "OAUTH",
    "credentialProvider": {"oauthCredentialProvider": {
        "providerArn": provider_arn,
        "scopes": ["catalog.read"],
        "grantType": "CLIENT_CREDENTIALS",
    }},
}]
```

The Gateway fetches, caches, refreshes, and injects the token. Your MCP server holds no
credentials at all.

---

## 3LO: User Federation

The full sequence, which the Gateway normally runs for you:

```
1. GetWorkloadAccessTokenForJWT(workloadName, userToken=<inbound JWT>)
      → workload access token.  The inbound `sub` becomes the vault key.

2. GetResourceOauth2Token(..., oauth2Flow="USER_FEDERATION")
      → no token cached yet, so returns { authorizationUrl, sessionUri }

3. User opens authorizationUrl, logs in, consents.
      IdP redirects to the provider's callbackUrl; AgentCore exchanges the code.

4. CompleteResourceTokenAuth(userIdentifier={"userToken": <inbound JWT>}, sessionUri)
      → validates the session binding.  Replaces your `state` check.

5. GetResourceOauth2Token(...) again
      → { accessToken }, now cached per (workload + user sub) and auto-refreshing.
```

Request `offline_access` in scopes or you get no refresh token and the vault cannot renew
silently — the user is sent back through consent every time the access token expires.

### In agent code

```python
@requires_access_token(
    provider_name="servicenow-provider",
    auth_flow="USER_FEDERATION",
    scopes=["openid", "profile", "offline_access"],
)
async def create_incident(short_description: str, *, access_token: str):
    return httpx.post(
        f"{INSTANCE}/api/now/table/incident",
        headers={"Authorization": f"Bearer {access_token}"},
        json={"short_description": short_description},
    ).json()
```

---

## Three Ways to Wire 3LO

Ordered by how much work AgentCore does for you.

### A. Gateway + MCP-server target — maximum offload (recommended)

The Gateway obtains, caches, refreshes, and **injects** the per-user token as an
`Authorization` header. Your MCP server has zero auth code.

```python
# Gateway — note the MCP protocol version, which the 3LO grant requires.
create_gateway(
    name=gw_name, roleArn=role_arn, protocolType="MCP",
    protocolConfiguration={"mcp": {"supportedVersions": ["2025-11-25"]}},
    authorizerType="CUSTOM_JWT",
    authorizerConfiguration={"customJWTAuthorizer": {
        "discoveryUrl": cognito_discovery_url,
        "allowedClients": [client_id],
    }},
)

# Target — outbound 3LO
create_gateway_target(
    gatewayIdentifier=gw_id, name="servicenow-mcp",
    targetConfiguration={"mcp": {"mcpServer": {
        "endpoint": "https://my-mcp-host.example.com/mcp",
        "listingMode": "DEFAULT",
    }}},
    credentialProviderConfigurations=[{
        "credentialProviderType": "OAUTH",
        "credentialProvider": {"oauthCredentialProvider": {
            "providerArn": provider_arn,
            "scopes": ["openid", "profile", "offline_access"],
            "grantType": "AUTHORIZATION_CODE",
            "defaultReturnUrl": "https://app.example.com/oauth/done",
        }},
    }],
)
```

The MCP server then just reads the injected header:

```python
from fastmcp.server.dependencies import get_http_headers

def _bearer() -> str:
    # include={"authorization"} is REQUIRED — see Gotchas.
    headers = get_http_headers(include={"authorization"})
    auth = headers.get("authorization", "")
    return auth[7:] if auth.lower().startswith("bearer ") else auth
```

On the first call for a user with no cached token, the target reports `PENDING_AUTH` and the
tool result carries a "Needs Authorization" response with the authorize URL.

### B. Decorator inside a Lambda target — the fallback

**The Gateway does not manage 3LO for Lambda targets, only MCP-server targets.** If you
cannot host an HTTP MCP endpoint, keep the tools in a Lambda and fetch the token in-process
with `@requires_access_token(auth_flow="USER_FEDERATION")`. Still per-user, still no
PKCE/`state`/Secrets code — but the Lambda has to surface the consent URL itself.

### C. Driving the data-plane APIs directly

Only when you are building your own consent UI. Use a **standalone** workload identity, and
bind with `userToken` — see Gotchas for both.

```python
dp = boto3.client("bedrock-agentcore", region_name=region)

wat = dp.get_workload_access_token_for_jwt(
    workloadName=standalone_workload_name, userToken=inbound_jwt,
)["workloadAccessToken"]

resp = dp.get_resource_oauth2_token(
    workloadIdentityToken=wat,
    resourceCredentialProviderName=provider_name,
    scopes=["openid", "profile", "offline_access"],
    oauth2Flow="USER_FEDERATION",
    resourceOauth2ReturnUrl=return_url,
    forceAuthentication=False,
)

if "accessToken" not in resp:
    # Send the user to resp["authorizationUrl"], then after consent:
    dp.complete_resource_token_auth(
        userIdentifier={"userToken": inbound_jwt},   # NOT a bare sub
        sessionUri=resp["sessionUri"],
    )
    resp = dp.get_resource_oauth2_token(..., sessionUri=resp["sessionUri"])

downstream_token = resp["accessToken"]
```

---

## What This Replaces

Migrating a hand-rolled 3LO implementation deletes essentially all of it:

| Your code | Replacement | Configured in |
|---|---|---|
| `_generate_pkce_pair()` | internal to the flow | — |
| `state` + PKCE store | session binding (`sessionUri` + `CompleteResourceTokenAuth`) | — |
| `build_authorize_url()` | `GetResourceOauth2Token` → `authorizationUrl` | provider |
| `redirect_uri` handler | provider `callbackUrl` | register with the IdP |
| `exchange_code()` POST | provider exchanges at `tokenEndpoint` | automatic |
| `store_tokens(principal_id, ...)` | token vault, keyed per (workload + `sub`) | automatic |
| Secrets Manager token rows | token vault | automatic |
| Manual refresh on `expires_in` | automatic refresh | needs `offline_access` |
| `_client_id` / `_client_secret` / endpoint URLs | provider config | provider |

`principal_id` becomes the inbound JWT `sub`. Nothing else needs to change conceptually.

---

## Gotchas

Each of these produces a confusing failure rather than a clear error.

**The Gateway's workload identity is service-linked and cannot be borrowed.** Calling
`GetWorkloadAccessTokenForJWT` with it from outside fails with *"WorkloadIdentity is linked
to a service."* A standalone caller — your own backend, a consent UI — needs its own
workload identity created via `identity_create_workload_identity`.

**`tokenEndpointAuthMethods` and `clientAuthenticationMethod` are mutually exclusive.**
Setting both inside `authorizationServerMetadata` is rejected with *"tokenEndpointAuthMethods
is deprecated … Both cannot be provided together."* Set the client auth method once, at the
top level of `customOauth2ProviderConfig`.

**Bind the session with `userToken`, not `userId`.** The session is keyed off the token the
workload identity was derived from. Passing a bare `sub` as `userIdentifier` yields *"Invalid
or expired session."*

**MCP protocol version `2025-11-25` is required** in the Gateway's
`protocolConfiguration.mcp.supportedVersions` for the authorization-code grant on
MCP-server targets. Omit it and the 3LO grant silently will not engage.

**FastMCP's `get_http_headers()` strips `authorization` by default** as a sensitive header.
Without `include={"authorization"}` you receive nothing and every tool looks like the user
never consented — the most expensive way to fail, because the symptom points at OAuth.

**Register the provider's `callbackUrl` with the IdP.** It is returned on create and printed
by `get_oauth2_credential_provider`. Forgetting it surfaces as an IdP-side redirect_uri
mismatch, not an AgentCore error.

**Do not fingerprint tokens by prefix.** OIDC access tokens are JWTs whose leading segment
(the base64 header) is often byte-identical across users. A prefix comparison makes two
different users look like the same user — which will make a per-user isolation test pass
when isolation is in fact broken. Hash the whole token.

**Generated policy/consent state is not permanent.** Vault entries refresh automatically, but
a user who revokes consent at the IdP will fail on next refresh; handle re-consent as a
normal path, not an exception.

---

## IAM Permissions

Control plane, for managing providers and identities:

```json
{
  "Effect": "Allow",
  "Action": [
    "bedrock-agentcore:CreateWorkloadIdentity",
    "bedrock-agentcore:GetWorkloadIdentity",
    "bedrock-agentcore:UpdateWorkloadIdentity",
    "bedrock-agentcore:DeleteWorkloadIdentity",
    "bedrock-agentcore:ListWorkloadIdentities",
    "bedrock-agentcore:CreateOauth2CredentialProvider",
    "bedrock-agentcore:GetOauth2CredentialProvider",
    "bedrock-agentcore:UpdateOauth2CredentialProvider",
    "bedrock-agentcore:DeleteOauth2CredentialProvider",
    "bedrock-agentcore:ListOauth2CredentialProviders",
    "bedrock-agentcore:CreateApiKeyCredentialProvider",
    "bedrock-agentcore:GetApiKeyCredentialProvider",
    "bedrock-agentcore:GetTokenVault",
    "bedrock-agentcore:SetTokenVaultCMK",
    "bedrock-agentcore:PutResourcePolicy",
    "bedrock-agentcore:GetResourcePolicy"
  ],
  "Resource": "*"
}
```

The Gateway execution role additionally needs the outbound-auth data-plane actions
(`GetWorkloadAccessTokenForJWT`, `GetResourceOauth2Token`) so it can fetch user tokens.

For a customer-managed token vault key, the KMS key policy must grant the AgentCore service
principal `kms:Encrypt`, `kms:Decrypt`, `kms:GenerateDataKey`, and `kms:DescribeKey`. The key
must be in the vault's region; multi-region keys work, single-region keys from another region
do not.

---

## Related

- `references/gateway-and-mcp.md` — Gateway and target configuration
- `references/security.md` — inbound auth, the token propagation chain, RLS at the data layer
- `references/policy.md` — Cedar authorization on top of an authenticated identity
