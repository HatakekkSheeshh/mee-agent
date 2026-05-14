# Runtime Service Contract

Containers deployed on AgentBase Runtime must meet these requirements:

1. **Port**: Listen on port `8080`
2. **Health check**: Expose `GET /health` returning HTTP 200 when ready
3. **Main endpoint**: Serve requests at `POST /invocations`
   - The SDK extracts metadata from request headers (see `greennode_agentbase.runtime.models`):
     - `X-GreenNode-AgentBase-Session-Id` → `context.session_id` (**required** when agent uses short-term memory / checkpointer)
     - `X-GreenNode-AgentBase-User-Id` → `context.user_id` (**required** when agent uses short-term memory / checkpointer or long-term memory; also required for delegated API key or OAuth2 3LO token). Maps to `actor_id` in memory operations.
     - `X-GreenNode-AgentBase-Request-Id` → `context.request_id` (auto-generated if not provided)
     - `X-GreenNode-AgentBase-Custom-*` → collected into `context.request_headers` (along with `Authorization`), for passing custom data to the agent
4. **Automatic runtime management** (do not set manually):
   - The IAM service account and Agent Identity are managed by the AgentBase runtime system and injected into the container as `GREENNODE_CLIENT_ID`, `GREENNODE_CLIENT_SECRET`, `GREENNODE_AGENT_IDENTITY`
   - `GREENNODE_ENDPOINT_URL` is also auto-injected — contains the endpoint URL to call into the agent
   - The SDK automatically uses these — no manual credential configuration needed in agent code
