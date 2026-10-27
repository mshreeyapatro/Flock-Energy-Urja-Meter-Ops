# Protocol Discovery & Reverse Engineering Document: Urja Meter Ops

## 1. Executive Summary

This document details the reverse engineering and network inspection findings for the legacy portal **Urja Meter Ops** (`https://urja-ops.flockenergy.tech`). The portal is a modern SvelteKit web application with client-side hydration, server-side rendered routes, internal REST APIs, and an HMAC-SHA256 cryptographic export verification mechanism.

---

## 2. Reconnaissance & Architecture Overview

### 2.1 Technology Stack of the Legacy Application
- **Frontend / Framework**: SvelteKit with client-side hydration (`_app/immutable/entry/start.*.js`, `app.*.js`, and route component nodes).
- **Styling**: TailwindCSS classes in rendered markup.
- **Session / Auth Engine**: `better-auth` using secure HTTP-only cookies (`__Secure-better-auth.session_token`).
- **Data Delivery**: Hybrid approach:
  - Initial meter details are rendered server-side as HTML (`<dl>`, `<nav>`, `<table>`).
  - Dynamic widgets (location coordinates and time-series consumption readings) are fetched asynchronously via internal `/portal/*` REST endpoints.
  - A secure bulk export endpoint protected by dynamic HMAC-SHA256 signatures (`/portal/export`).

---

## 3. Authentication & Session Management Workflow

### 3.1 Form Submission Endpoint
- **URL**: `POST https://urja-ops.flockenergy.tech/login`
- **Content-Type**: `application/x-www-form-urlencoded`
- **Payload**:
  ```
  email=operator@urja.local&password=urja-ops-2026
  ```

### 3.2 Security Quirks & CSRF Origin Protection
- **SvelteKit CSRF Guard**: SvelteKit enforces origin checking on all form action POSTs. Submitting without proper origin headers results in:
  ```json
  HTTP 403 Forbidden
  "Cross-site POST form submissions are forbidden"
  ```
- **Required Headers**:
  - `Origin: https://urja-ops.flockenergy.tech`
  - `Referer: https://urja-ops.flockenergy.tech/login`
  - `User-Agent: Mozilla/5.0 ...`

### 3.3 Session Tokens & Cookies
- On successful authentication, the server returns an HTTP 200/303 redirect with the header:
  ```http
  Set-Cookie: __Secure-better-auth.session_token=<token_hash>; Max-Age=3600; Path=/; HttpOnly; Secure; SameSite=Lax
  ```
- The session token has a lifespan of 1 hour (3600 seconds).
- Downstream requests must attach this cookie in the `Cookie` header.

---

## 4. Internal API & Route Inventory

Through automated bundle decompilation and network trajectory inspection, the following routes and internal endpoints were discovered:

### 4.1 Internal Endpoints Summary Table

| Endpoint / Route | Method | Purpose | Response Type | Security Requirements |
| :--- | :--- | :--- | :--- | :--- |
| `/login` | `POST` | Authenticates operator | JSON redirect | `Origin`, `Referer` headers |
| `/portal/meters/search` | `GET` | Paginated search of meters | JSON | Session Cookie |
| `/meters/{meter_id}` | `GET` | SSR Nameplate & Hierarchy | HTML / SvelteKit Data | Session Cookie |
| `/portal/meters/{id}/geo` | `GET` | Meter GPS latitude & longitude | JSON | Session Cookie |
| `/portal/meters/{id}/energy`| `GET` | Historical energy consumption | JSON | Session Cookie |
| `/portal/dts` | `GET` | Distribution Transformers list | JSON | Session Cookie |
| `/portal/keys` | `GET` | Dynamic signing secret for HMAC | JSON | Session Cookie |
| `/portal/export` | `GET` | Full meter export with hierarchy| JSON | HMAC-SHA256 Signature |
| `/api/auth/sign-out` | `POST` | Invalidates operator session | JSON | Session Cookie |

---

## 5. Detailed Endpoint Payloads & Protocols

### 5.1 Meter Search (`GET /portal/meters/search?q={query}&page={page}`)
- **Parameters**:
  - `q` (string): Search query (matches meterId or serialNo).
  - `page` (integer): 1-indexed page number (page size = 20).
- **Sample Response**:
  ```json
  {
    "data": [
      {
        "meterId": "J100000",
        "serialNo": "SE33962",
        "make": "HPL",
        "phaseType": "single",
        "installStatus": "Decommissioned",
        "dtCode": "DT-001"
      }
    ],
    "total": 403
  }
  ```

### 5.2 Meter Detail Page (`GET /meters/{meter_id}`)
- **Rendered HTML Structure**:
  - **Breadcrumbs Hierarchy**: Located in `<nav class="... text-xs text-slate-500">` containing spans separated by `›`:
    `Zone › Circle › Division › Subdivision › Substation › Feeder › DT`
  - **Nameplate Attributes**: Located in `<dl class="divide-y divide-slate-100">`:
    - `<dt>` contains parameter name (e.g. `Meter ID`, `Serial No`, `Make`, `Phase Type`, `Installation Status`, `Installation Type`).
    - `<dd>` contains parameter value (e.g. `J100000`, `SE33962`, `HPL`, `single`, `Decommissioned`, `Whole Current`).

### 5.3 Meter Geo Coordinates (`GET /portal/meters/{meter_id}/geo`)
- **Sample Response**:
  ```json
  {
    "data": {
      "latitude": "26.938961002479868",
      "longitude": "75.83095696146852"
    }
  }
  ```
- **Anomaly**: Coordinates are returned as string representations rather than native IEEE 754 floating-point numbers.

### 5.4 Meter Energy History (`GET /portal/meters/{meter_id}/energy`)
- **Sample Response**:
  ```json
  {
    "data": [
      {
        "timestamp": "23/06/2026 23:30",
        "kwh": "48438.74",
        "kvah": "52313.84",
        "voltR": "226"
      }
    ]
  }
  ```
- **Anomalies**:
  - `kwh`, `kvah`, and `voltR` are formatted as strings.
  - In cases of sensor communication drops, values can be empty string `""`, `"?"`, or omitted.
  - Dates use the non-ISO format `DD/MM/YYYY HH:MM`.

### 5.5 Distribution Transformers (`GET /portal/dts?page={page}`)
- **Sample Response**:
  ```json
  {
    "data": [
      {
        "code": "DT-001",
        "name": "Malviya Nagar DT 1",
        "feederCode": "F-001",
        "capacityKva": 100
      }
    ],
    "total": 40
  }
  ```

### 5.6 Cryptographic HMAC Export (`GET /portal/export`)
- **Discovery**: In the compiled JavaScript chunk `4.2Bgc2kUI.js`, an export feature was found:
  1. The client queries `GET /portal/keys` which returns:
     ```json
     { "data": { "signingSecret": "I3dZPPf5CgTp7JyGNMI8i6z8LFR7TmSR" } }
     ```
  2. The client calculates the current Unix timestamp in seconds `timestamp = str(int(time.time()))`.
  3. The canonical string payload is constructed by joining HTTP method, path, query string, and timestamp with newlines:
     ```
     GET\n/portal/export\npage=1\n<timestamp>
     ```
  4. An HMAC-SHA256 signature is generated using `signingSecret` as the key.
  5. The request is dispatched with headers:
     - `x-timestamp: <timestamp>`
     - `x-signature: <hex_signature>`
  6. The portal responds with all 403 meters complete with full nested hierarchy and geo coordinates in a single request.

---

## 6. Adapter Layer Normalization Strategy

To transform these messy and heterogeneous inputs into a clean REST API, our adapter layer (`UrjaPortalClient`):
1. **Automates Session Re-authentication**: Intercepts 401/403 responses and transparently issues a re-login using persistent cookie jars.
2. **Normalizes Numeric Types**: Automatically parses string-based floats (`"48438.74"` -> `48438.74`) and handles sentinel error values (`"?"` -> `null`).
3. **Fuses Hybrid Data Sources**: Combines HTML nameplate scraping, dynamic geo coordinates, and HMAC-verified metadata into an authoritative unified `MeterDetailResponse`.
4. **Calculates Summary Analytics**: Aggregates time-series consumption metrics into instant analytics (`min_kwh`, `max_kwh`, `min_volt_r`, `max_volt_r`, `latest_reading`).
