# Authentication Guide

This guide covers setting up authentication for Soliplex Ingester using OAuth2 Proxy.

## Overview

Soliplex Ingester uses [OAuth2 Proxy](https://oauth2-proxy.github.io/oauth2-proxy/) as a reverse proxy to handle OIDC authentication. This approach:

- Requires **zero code changes** to the application
- Supports any OIDC-compliant identity provider
- Handles session management, token refresh, and logout automatically
- Forwards user identity to the backend via HTTP headers

### Architecture

```
┌──────────┐     ┌───────────────┐     ┌─────────────────────┐
│  User    │────▶│ OAuth2 Proxy  │────▶│ Soliplex Ingester   │
│ Browser  │     │   (OIDC)      │     │   (API + UI)        │
└──────────┘     └───────────────┘     └─────────────────────┘
                        │
                        ▼
                 ┌──────────────┐
                 │ OIDC Provider │
                 │ (Keycloak,   │
                 │  Auth0, etc) │
                 └──────────────┘
```

---

## Quick Start

### 1. Configure Your OIDC Provider

Create a new client/application in your OIDC provider with these settings:

| Setting | Value |
|---------|-------|
| Client Type | Confidential |
| Protocol | OpenID Connect |
| Redirect URI | `http://localhost:4180/oauth2/callback` |
| Scopes | `openid`, `email`, `profile` |

Note the **Client ID** and **Client Secret**.

### 2. Create Environment File

```bash
cd docker
cp .env.auth.example .env.auth
```

Edit `.env.auth` with your OIDC settings:

```bash
OAUTH2_PROXY_OIDC_ISSUER_URL=https://your-idp.example.com/realms/your-realm
OAUTH2_PROXY_CLIENT_ID=soliplex-ingester
OAUTH2_PROXY_CLIENT_SECRET=your-secret
OAUTH2_PROXY_COOKIE_SECRET=$(openssl rand -base64 32 | tr -- '+/' '-_')
OAUTH2_PROXY_REDIRECT_URL=http://localhost:4180/oauth2/callback
```

### 3. Start with Authentication

```bash
docker compose -f docker-compose.yml -f docker-compose.auth.yml --env-file .env.auth up
```

### 4. Access the Application

- **With auth:** http://localhost:4180 (redirects to OIDC login)
- **Direct API (no auth):** http://localhost:8002 (if port still exposed)

---

## Provider-Specific Configuration

### Keycloak

1. Create a new client in your realm:
   - Client ID: `soliplex-ingester`
   - Client Protocol: `openid-connect`
   - Access Type: `confidential`
   - Valid Redirect URIs: `http://localhost:4180/oauth2/callback`

2. Create an Audience mapper (required):
   - Go to Client → Mappers → Add Mapper
   - Mapper Type: `Audience`
   - Included Client Audience: `soliplex-ingester`

3. (Optional) Add Groups mapper for group-based access:
   - Go to Client Scopes → groups → Mappers
   - Mapper Type: `Group Membership`
   - Token Claim Name: `groups`

4. Environment settings:
   ```bash
   OAUTH2_PROXY_OIDC_ISSUER_URL=https://keycloak.example.com/realms/your-realm
   # Or use keycloak-oidc provider for role support:
   # Add to oauth2-proxy.cfg: provider = "keycloak-oidc"
   ```

### Auth0

1. Create a new Regular Web Application
2. Configure Allowed Callback URLs: `http://localhost:4180/oauth2/callback`
3. Enable the following connections as needed

4. Environment settings:
   ```bash
   OAUTH2_PROXY_OIDC_ISSUER_URL=https://your-tenant.auth0.com/
   ```

### Azure AD

1. Register a new application in Azure Portal
2. Add redirect URI: `http://localhost:4180/oauth2/callback`
3. Create a client secret

4. Environment settings:
   ```bash
   OAUTH2_PROXY_OIDC_ISSUER_URL=https://login.microsoftonline.com/{tenant-id}/v2.0
   # Or use azure provider:
   # Add to oauth2-proxy.cfg: provider = "azure"
   ```

### Okta

1. Create a new Web Application
2. Set Sign-in redirect URI: `http://localhost:4180/oauth2/callback`

3. Environment settings:
   ```bash
   OAUTH2_PROXY_OIDC_ISSUER_URL=https://your-org.okta.com
   ```

---

## Access Control

### Email Domain Restriction

Restrict access to specific email domains:

```bash
# .env.auth
OAUTH2_PROXY_EMAIL_DOMAINS=yourcompany.com,partner.com
```

### Group-Based Access

Restrict access to users in specific groups:

```ini
# oauth2-proxy.cfg
allowed_groups = ["soliplex-users", "soliplex-admins"]
```

Requires your OIDC provider to include a `groups` claim in the token.

### Role-Based Access (Keycloak)

```ini
# oauth2-proxy.cfg (with provider = "keycloak-oidc")
allowed_roles = ["soliplex-user"]
# Or client roles:
allowed_roles = ["soliplex-ingester:admin"]
```

---

## Backend Integration

OAuth2 Proxy forwards user identity via HTTP headers. The backend receives:

| Header | Description |
|--------|-------------|
| `X-Auth-Request-User` | Username/subject |
| `X-Auth-Request-Email` | User's email |
| `X-Auth-Request-Groups` | User's groups (if available) |
| `X-Auth-Request-Access-Token` | OAuth access token |
| `X-Forwarded-User` | Alias for user |
| `X-Forwarded-Email` | Alias for email |

### Reading Headers in FastAPI

```python
from fastapi import Request, HTTPException, Depends

def get_current_user(request: Request) -> dict:
    """Extract user from OAuth2 Proxy headers."""
    user = request.headers.get("X-Auth-Request-User")
    email = request.headers.get("X-Auth-Request-Email")

    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    return {"user": user, "email": email}

@router.get("/protected")
async def protected_endpoint(user: dict = Depends(get_current_user)):
    return {"message": f"Hello {user['email']}"}
```

---

## API Key Fallback

For programmatic access (scripts, CI/CD), you can enable API key authentication alongside OIDC:

### Enable API Key Support

```bash
# .env.auth
API_KEY_ENABLED=true
API_KEY=$(openssl rand -hex 32)
```

### Using API Keys

Both methods work through the OAuth2 Proxy port (4180):

```bash
# Through OAuth2 Proxy (recommended - single port for all clients)
curl -H "Authorization: Bearer your-api-key" http://localhost:4180/api/v1/batch/

# Direct to API (if port 8002 is exposed)
curl -H "Authorization: Bearer your-api-key" http://localhost:8002/api/v1/batch/
```

**How it works:** OAuth2 Proxy is configured with `skip_jwt_bearer_tokens = true`, which allows requests with an `Authorization: Bearer` header to pass through without OIDC authentication. The backend then validates the token.

---

## Production Deployment

### Enable HTTPS

1. Update redirect URL in OIDC provider
2. Configure SSL certificates
3. Update environment:
   ```bash
   OAUTH2_PROXY_COOKIE_SECURE=true
   OAUTH2_PROXY_REDIRECT_URL=https://your-domain.com/oauth2/callback
   ```

### Enable Redis Session Storage

For high availability and session sharing:

```bash
# .env.auth
OAUTH2_PROXY_SESSION_STORE_TYPE=redis
OAUTH2_PROXY_REDIS_CONNECTION_URL=redis://redis:6379
```

### Restrict CORS Origins

Update the application's CORS settings to only allow your domain.

---

## Troubleshooting

### "Invalid redirect" Error

Ensure the redirect URL in `.env.auth` exactly matches what's configured in your OIDC provider.

### "Token audience doesn't match"

For Keycloak, add an Audience mapper to your client (see Keycloak section above).

### Headers Not Reaching Backend

Ensure `set_xauthrequest = true` is in `oauth2-proxy.cfg` and the backend trusts proxy headers.

### Session Expires Frequently

Increase cookie lifetime:
```ini
# oauth2-proxy.cfg
cookie_expire = "168h"
cookie_refresh = "1h"
```

### Debug Logging

Enable verbose logging:
```ini
# oauth2-proxy.cfg
standard_logging = true
auth_logging = true
request_logging = true
```

---

## Files Reference

| File | Purpose |
|------|---------|
| `docker/docker-compose.auth.yml` | Docker Compose overlay for auth |
| `docker/oauth2-proxy/oauth2-proxy.cfg` | OAuth2 Proxy configuration |
| `docker/.env.auth.example` | Environment variable template |
| `docker/nginx/nginx-auth.conf` | Nginx config (optional) |
| `docker/nginx/locations.conf` | Nginx location blocks |

---

## See Also

- [OAuth2 Proxy Documentation](https://oauth2-proxy.github.io/oauth2-proxy/)
- [CONFIGURATION.md](CONFIGURATION.md) - Environment variables
- [API.md](API.md) - API reference
