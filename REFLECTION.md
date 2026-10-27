# Engineering Reflection & Architectural Decisions

## Project: Flock Energy - Urja Meter Ops REST API Wrapper

---

### 1. Assumptions Made

1. **Session Longevity & Concurrency**:
   - The legacy Urja portal issues session cookies (`__Secure-better-auth.session_token`) with a Max-Age of 3600 seconds (1 hour). We assumed that sessions can expire at any time due to idle timeouts, server restarts, or token invalidation. Therefore, our adapter layer does not rely on static cookie lifetimes; instead, it uses a reactive re-authentication mechanism that intercepts 401/403 or redirect responses and re-authenticates automatically.
2. **Credential Storage & Multi-Tenancy**:
   - For this service, the credentials (`operator@urja.local` / `urja-ops-2026`) are configured as default system-level credentials via environment variables (`.env`). However, the API was designed so that callers can also explicitly pass their own operator credentials via `POST /api/v1/auth/login`.
3. **Data Availability in Legacy Endpoints**:
   - We observed that meter location (`/portal/meters/{id}/geo`) and consumption (`/portal/meters/{id}/energy`) are loaded asynchronously on the frontend. We assumed that client applications querying `GET /api/v1/meters/{id}` expect a fully-fused, coherent response containing both static nameplate specifications and geographic coordinates without needing multiple client roundtrips.
4. **Data Normalization Tolerances**:
   - The legacy application returns telemetry readings where values can intermittently be string-encoded numbers (`"48438.74"`), empty strings, or error symbols (`"?"`). We assumed that downstream consumers require strictly typed floating-point numbers or `null` rather than arbitrary string representations.

---

### 2. Difficult Technical Hurdles & Resolutions

1. **SvelteKit Origin & CSRF Rejection**:
   - *Hurdle*: Initial programmatic POST requests to the `/login` route resulted in an immediate `403 Forbidden` with the error `Cross-site POST form submissions are forbidden`.
   - *Resolution*: SvelteKit form actions enforce strict origin checks. By reverse engineering the frontend JavaScript bundle, we inspected how the client dispatches form submissions and added explicit `Origin: https://urja-ops.flockenergy.tech` and `Referer: https://urja-ops.flockenergy.tech/login` headers to our HTTP client, satisfying the server's CSRF policy.
2. **Dynamic HMAC-SHA256 Cryptographic Verification**:
   - *Hurdle*: The portal's full export route (`/portal/export`) is protected by cryptographic HMAC signing that was not documented in standard Swagger docs. Direct requests without proper signatures fail.
   - *Resolution*: By analyzing the compiled client chunk `4.2Bgc2kUI.js`, we reverse-engineered the signing routine: fetching the dynamic secret from `/portal/keys`, constructing the canonical multi-line message `[METHOD, PATH, QUERY, TIMESTAMP].join('\n')`, generating a SHA-256 HMAC, and injecting `x-timestamp` and `x-signature` headers. We implemented this in Python's standard `hmac` and `hashlib` modules, enabling our service to verify grid hierarchy against authoritative backend data.
3. **Asyncio Event Loop Isolation During Pytest Execution**:
   - *Hurdle*: In `pytest-asyncio`, each asynchronous test function executes in a newly instantiated event loop. Because the singleton `httpx.AsyncClient` was initially instantiated during the first test, subsequent tests failed with `RuntimeError: Event loop is closed`.
   - *Resolution*: We updated `UrjaPortalClient.get_client()` to inspect `asyncio.get_running_loop()`. If the active event loop differs from the loop on which the client was created, the client is refreshed while safely preserving all session cookies in a persistent in-memory dictionary.

---

### 3. Mistakes, Bugs & Edge Cases Discovered

1. **Form Input Naming Discrepancy**:
   - *Discovery*: While traditional documentation often references `username` and `password`, the legacy portal form specifically binds `<input name="email">`. Sending `username` caused silent validation failures on the portal. Our Pydantic model (`LoginRequest`) accommodates both `email` and `username` aliases to prevent downstream consumer errors.
2. **Missing Sensor Readings**:
   - *Discovery*: Certain meters had missing voltage or power values marked as `"?"` during line outages. A naive `float(val)` parser would crash with `ValueError`. We built a resilient `parse_float` helper that catches conversion failures and safely returns `None`.
3. **Breadcrumb Entity Encoding**:
   - *Discovery*: The legacy HTML breadcrumb trail used the HTML character entity `›` between hierarchy nodes. Naive text extraction concatenated entity symbols into node names. We added filtering to prune navigation delimiters from the clean breadcrumb array.

---

### 4. Architectural Trade-offs & Decisions

1. **FastAPI vs. Flask/Express**:
   - *Decision*: We chose **FastAPI** with **Pydantic v2** and **HTTPX**.
   - *Rationale*: FastAPI automatically generates standards-compliant OpenAPI 3.1 definitions (`openapi.json`) and provides interactive Swagger UI at `/docs` and ReDoc at `/redoc`. Pydantic ensures strong type validation and serialization, catching upstream data contract violations before they reach client applications.
2. **Async HTTPX vs. Synchronous Requests**:
   - *Decision*: We adopted non-blocking asynchronous I/O with `httpx.AsyncClient`.
   - *Rationale*: Scraping legacy pages often incurs upstream network latency. An asynchronous event-driven server ensures our API service can handle high concurrent throughput without blocking OS threads.
3. **Tiered Data Fetching Strategy**:
   - *Decision*: For individual meter queries, we fetch the live server-rendered HTML and geo endpoints directly. For bulk hierarchy queries, we leverage the HMAC-signed export endpoint with in-memory caching (10-minute TTL).
   - *Rationale*: Scraping 403 individual HTML pages one by one for a hierarchy view would place unnecessary strain on the legacy portal and lead to 30+ second response times. Using the cryptographically signed export allows instant hierarchy generation while keeping single-meter queries real-time.

---

### 5. Future Improvements & Production Hardening

1. **Distributed Session Storage (Redis)**:
   - For multi-instance horizontal scaling behind a load balancer, session cookies should be stored in a centralized Redis cache rather than process-local memory.
2. **Upstream Circuit Breaker & Rate Limiter**:
   - Implementing a circuit breaker (e.g. using `tenacity` or `pybreaker`) to protect the legacy portal from being overwhelmed during traffic spikes.
3. **Webhooks / Change Data Capture (CDC)**:
   - Introducing periodic background polling of meter status changes to emit WebSocket or Webhook events to downstream energy management platforms whenever a meter status transitions to `Decommissioned` or `Active`.
4. **End-to-End Containerization**:
   - Adding a multi-stage `Dockerfile` and `docker-compose.yml` for unified one-command deployment across cloud environments.
