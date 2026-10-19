# Optional GitHub login

GitHub login adds identity and gates research chat. Public search and record pages
remain accessible without an account. This version has no user database, saved
preferences, saved searches or repository access. Login and OAuth state live only
in the server process. Restarting the server signs everyone out.

## Configure a GitHub App

Create a GitHub App under the project organization (or a personal account for local
experiments). For a server at `http://127.0.0.1:8030`:

- Set the homepage URL to `http://127.0.0.1:8030`.
- Set the user authorization callback URL to
  `http://127.0.0.1:8030/auth/github/callback`.
- Leave repository, organization and account permissions unset. No repository
  installation is needed to sign in. Disable webhooks for this identity-only app.
- Generate a client secret and use the **client ID**, not the application ID.

The registration and authentication flow are described in GitHub's
[login tutorial](https://docs.github.com/en/apps/creating-github-apps/writing-code-for-a-github-app/building-a-login-with-github-button-with-a-github-app)
and [user access token documentation](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-a-user-access-token-for-a-github-app).

Supply these environment variables to the server through your shell or deployment
secret configuration (do not commit the real secret):

```bash
export AMIGO3_GITHUB_CLIENT_ID='your-client-id'
export AMIGO3_GITHUB_CLIENT_SECRET='your-client-secret'
export AMIGO3_AUTH_ORIGIN='http://127.0.0.1:8030'
uv run --extra chat amigo3 serve --backend duckdb --chat-command 'claude-agent-acp'
```

Use that exact origin in the browser: `localhost` and `127.0.0.1` have different
cookies. The origin must have no trailing slash, path, query or fragment. HTTP is
allowed only for loopback; other origins require HTTPS. The callback is always
derived from the configured origin, never an incoming Host header. Partial
configuration fails at startup. With all three variables absent, the login controls
are hidden and the existing anonymous, local-only chat behavior is preserved.

## Behavior and boundaries

- Sign-in returns to the same path, query and fragment. External return URLs are
  rejected. Cancellation returns to the public home page with a notice.
- The server validates browser-bound, one-use OAuth state and exchanges the code
  with PKCE. It fetches GitHub's numeric ID and login, then discards the GitHub token.
- The browser gets an opaque HttpOnly, SameSite=Lax cookie, with Secure enabled for
  HTTPS. Only its hash is retained by the server. Tokens never go into JavaScript
  storage. Login expires after eight hours; pending OAuth flows expire after ten
  minutes. Pending flows and logins each have a 1,000-entry limit.
- `/api/me` reports whether login is configured and the current identity.
  POST `/auth/logout` requires a matching Origin and revokes the login on the server.
  Identity, auth and chat API responses are not cacheable.
- When login is configured, chat HTTP APIs and WebSocket connections require it.
  Chat lists, files, reconnect keys and approval defaults belong to the current
  login session. Another login cannot read or control them, even with a known key.
  Separate browser logins have separate workspaces, including for the same GitHub
  account. Drafts in sessionStorage are also separated by login session.
- Signing out or reaching login expiry stops that login's retained agent processes,
  deletes their temporary workspaces, and closes its active chat connections.
  The existing 30-minute disconnected-chat expiry also still applies.
- Without an allowlist, every authenticated GitHub account can use local chat.
  Set `AMIGO3_GITHUB_ALLOWLIST_FILE` to a JSON file containing numeric account IDs,
  for example `{"github_ids": [1234, 5678]}`, to restrict sign-in. Resolve a handle
  with `gh api users/HANDLE --jq .id`; IDs remain stable if a handle changes.
  Removed identities are denied on their next request and their sessions are
  terminated within five seconds. Empty, missing or malformed lists deny access;
  invalid initial configuration prevents startup. Mount the parent directory if
  editing an allowlist atomically inside a container.
- Set `AMIGO3_SITE_ACCESS=private` to require login for all pages, API routes,
  assets and downloads. This requires credentials and an allowlist. HTML navigation
  redirects to GitHub login; anonymous API requests receive 401. Private responses
  are not cacheable. The OAuth routes and identity-status endpoint remain available
  to anonymous callers. The default `public` mode preserves anonymous browsing.
  See [AWS deployment](deployment.md) for a private-site configuration.

**Chat is still local-only.** Login does not remove the CLI's loopback requirement
or the bridge's peer, Host and Origin checks. The agent still runs with the server
operator's credentials and tools. This change does not provide process isolation
for public multi-user hosting. Use one server worker: logins and chat state are
process-local. Durable accounts, shared sessions and public hosting can be designed
separately when needed.

## Verification

```bash
uv run --extra chat pytest tests/test_auth.py tests/test_chat.py tests/test_web_assets.py
AMIGO3_TEST_BROWSER=1 uv run --extra chat pytest tests/browser/test_login.py
```

The tests mock GitHub's token/profile endpoints and use the deterministic ACP
fixture agent; they need no credentials or model calls. They cover PKCE, invalid,
expired, missing and replayed state, cancellation, exchange failure, unsafe return
URLs, cookies, logout Origin checks, account isolation, chat expiry, and browser
sign-in/return navigation and cross-tab logout. A real GitHub registration still
needs a manual smoke test with the deployment's actual callback and credentials.
