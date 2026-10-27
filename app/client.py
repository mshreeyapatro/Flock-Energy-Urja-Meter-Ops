import asyncio
import hmac
import hashlib
import time
import logging
from typing import Optional, Dict, Any, List
import httpx
from bs4 import BeautifulSoup

from app.config import settings
from app.models import (
    MeterSummary,
    MeterListResponse,
    GeoLocation,
    HierarchyLevel,
    MeterHierarchy,
    MeterDetailResponse,
    ConsumptionReading,
    ConsumptionSummary,
    MeterConsumptionResponse,
    TransformerItem,
    HierarchyTreeResponse,
    AuthStatus,
)

logger = logging.getLogger("urja_client")
logging.basicConfig(level=logging.INFO)


class UrjaPortalClient:
    """
    Adapter and scraper client for the legacy Urja Meter Ops web portal.
    Handles automated authentication, cookie persistence, session revalidation,
    HTML parsing, and data type normalization.
    """

    def __init__(self, base_url: Optional[str] = None):
        self.base_url = (base_url or settings.portal_base_url).rstrip("/")
        self.email = settings.portal_email
        self.password = settings.portal_password

        # Persistent state
        self._client: Optional[httpx.AsyncClient] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._cookies: Dict[str, str] = {}
        self._is_authenticated = False
        self._user_name: Optional[str] = None
        self._user_email: Optional[str] = None

        # Cached export cache for instant fast lookups (meter_id -> full dict)
        self._export_cache: Dict[str, Dict[str, Any]] = {}
        self._export_cache_time: float = 0.0

    async def get_client(self) -> httpx.AsyncClient:
        """Returns or initializes the persistent HTTPX async client bound to current loop."""
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None

        # If client is missing, closed, or loop has changed (e.g. between test cases)
        if (
            self._client is None
            or self._client.is_closed
            or (current_loop is not None and self._loop != current_loop)
        ):
            # Capture any existing cookies before recreating
            if self._client and not self._client.is_closed:
                try:
                    for k, v in self._client.cookies.items():
                        self._cookies[k] = v
                except Exception:
                    pass

            self._loop = current_loop
            self._client = httpx.AsyncClient(
                timeout=settings.request_timeout,
                follow_redirects=True,
                cookies=self._cookies,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                    )
                },
            )
        return self._client

    async def close(self):
        """Closes the HTTP client session."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    # ==================== Authentication & Session Management ====================

    async def login(self, email: Optional[str] = None, password: Optional[str] = None) -> AuthStatus:
        """
        Submits login credentials to the portal, bypasses SvelteKit CSRF origin checks,
        and saves session cookies into the persistent client session.
        """
        login_email = email or self.email
        login_password = password or self.password

        client = await self.get_client()
        login_url = f"{self.base_url}/login"

        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": self.base_url,
            "Referer": f"{self.base_url}/login",
        }
        payload = {
            "email": login_email,
            "password": login_password,
        }

        try:
            response = await client.post(login_url, data=payload, headers=headers)
        except Exception as e:
            logger.error(f"Network error during login: {e}")
            self._is_authenticated = False
            return AuthStatus(
                authenticated=False,
                message=f"Failed to connect to Urja portal: {str(e)}"
            )

        # Check response - SvelteKit returns 200 with JSON redirect or 303 redirect
        if response.status_code in (200, 302, 303):
            # Save cookies to internal store
            for k, v in client.cookies.items():
                self._cookies[k] = v

            has_session = any("session" in k.lower() for k in client.cookies.keys())
            if has_session or response.status_code == 200:
                self._is_authenticated = True
                self._user_email = login_email
                self._user_name = "Ops Desk"
                logger.info(f"Successfully authenticated as {login_email}")
                return AuthStatus(
                    authenticated=True,
                    email=self._user_email,
                    user_name=self._user_name,
                    message="Authentication successful."
                )

        self._is_authenticated = False
        return AuthStatus(
            authenticated=False,
            message=f"Login failed. Portal returned status {response.status_code}."
        )

    async def ensure_authenticated(self) -> bool:
        """Ensures an active authenticated session exists. Re-authenticates if needed."""
        if self._is_authenticated and self._cookies:
            return True
        status = await self.login()
        return status.authenticated

    async def get_auth_status(self) -> AuthStatus:
        """Returns the current cached authentication state."""
        return AuthStatus(
            authenticated=self._is_authenticated,
            email=self._user_email,
            user_name=self._user_name,
            message="Active session" if self._is_authenticated else "Not authenticated"
        )

    async def _request_with_reauth(
        self,
        method: str,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        params: Optional[Dict[str, Any]] = None,
        data: Optional[Any] = None,
    ) -> httpx.Response:
        """
        Executes an HTTP request with automatic re-authentication if the session expired.
        """
        await self.ensure_authenticated()
        client = await self.get_client()

        req_headers = headers or {}

        response = await client.request(method, url, headers=req_headers, params=params, data=data)

        # Sync any newly updated cookies into persistent store
        for k, v in client.cookies.items():
            self._cookies[k] = v

        # Detect session expiration (401 Unauthorized, 403 Forbidden, or redirected to /login)
        is_redirected_to_login = str(response.url).endswith("/login") and not url.endswith("/login")
        if response.status_code in (401, 403) or is_redirected_to_login:
            logger.warning("Session expired or unauthorized. Attempting re-authentication...")
            self._is_authenticated = False
            auth_res = await self.login()
            if auth_res.authenticated:
                client = await self.get_client()
                response = await client.request(method, url, headers=req_headers, params=params, data=data)
            else:
                raise Exception("Authentication session expired and auto-login failed.")

        return response

    # ==================== HMAC Export Enrichment ====================

    async def _fetch_export_cache(self, force_refresh: bool = False) -> Dict[str, Dict[str, Any]]:
        """
        Fetches the complete dataset using the HMAC-signed export endpoint (/portal/export).
        Caches results for 10 minutes to minimize overhead.
        """
        now = time.time()
        if not force_refresh and self._export_cache and (now - self._export_cache_time) < 600:
            return self._export_cache

        try:
            # 1. Fetch signing secret from /portal/keys
            keys_resp = await self._request_with_reauth("GET", f"{self.base_url}/portal/keys")
            if keys_resp.status_code != 200:
                logger.warning(f"Could not fetch keys: {keys_resp.status_code}")
                return self._export_cache

            secret = keys_resp.json().get("data", {}).get("signingSecret", "")
            if not secret:
                return self._export_cache

            # 2. Construct HMAC-SHA256 signature
            timestamp = str(int(now))
            path = "/portal/export"
            query = "page=1"
            method = "GET"
            message = f"{method}\n{path}\n{query}\n{timestamp}".encode("utf-8")
            signature = hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()

            # 3. Call export
            export_resp = await self._request_with_reauth(
                "GET",
                f"{self.base_url}/portal/export?{query}",
                headers={"x-timestamp": timestamp, "x-signature": signature},
            )

            if export_resp.status_code == 200:
                data = export_resp.json().get("data", [])
                cache = {item["meterId"]: item for item in data if "meterId" in item}
                self._export_cache = cache
                self._export_cache_time = now
                logger.info(f"Successfully refreshed export cache with {len(cache)} meters.")
                return cache
        except Exception as e:
            logger.warning(f"Failed to refresh export cache: {e}")

        return self._export_cache

    # ==================== Meter Endpoints ====================

    async def get_meters(self, query: str = "", page: int = 1) -> MeterListResponse:
        """
        Retrieves a paginated list of smart meters, supporting text search by ID/serial.
        """
        url = f"{self.base_url}/portal/meters/search"
        params = {"q": query, "page": page}

        resp = await self._request_with_reauth("GET", url, params=params)
        if resp.status_code != 200:
            raise Exception(f"Failed to fetch meters from portal (status {resp.status_code})")

        payload = resp.json()
        raw_meters = payload.get("data", [])
        total = payload.get("total", len(raw_meters))

        meters: List[MeterSummary] = []
        for m in raw_meters:
            meters.append(
                MeterSummary(
                    meter_id=m.get("meterId", "N/A"),
                    serial_number=m.get("serialNo", "N/A"),
                    make=m.get("make", "N/A"),
                    phase_type=m.get("phaseType", "N/A"),
                    install_status=m.get("installStatus", "UNKNOWN"),
                    dt_code=m.get("dtCode"),
                )
            )

        page_size = 20
        total_pages = max(1, (total + page_size - 1) // page_size)

        return MeterListResponse(
            total=total,
            page=page,
            page_size=page_size,
            total_pages=total_pages,
            query=query,
            data=meters,
        )

    async def get_meter_details(self, meter_id: str) -> MeterDetailResponse:
        """
        Fetches full meter details by combining:
        1. HTML parsing of /meters/{id} (SSR nameplate & breadcrumbs hierarchy)
        2. /portal/meters/{id}/geo (normalized GPS coordinates)
        3. Internal HMAC export cache for complete attribute validation
        """
        # Fetch HTML representation
        html_url = f"{self.base_url}/meters/{meter_id}"
        html_resp = await self._request_with_reauth("GET", html_url)

        nameplate_attrs: Dict[str, str] = {}
        breadcrumbs: List[str] = []

        if html_resp.status_code == 200:
            soup = BeautifulSoup(html_resp.text, "html.parser")

            # Parse breadcrumbs hierarchy
            nav_elem = soup.find("nav", class_=lambda c: c and "flex" in c and "text-xs" in c)
            if nav_elem:
                spans = nav_elem.find_all("span")
                for s in spans:
                    text = s.get_text(strip=True)
                    if text and text != "›":
                        breadcrumbs.append(text)

            # Parse Nameplate definition list (<dl>)
            dl_elem = soup.find("dl")
            if dl_elem:
                for div in dl_elem.find_all("div", recursive=False):
                    dt = div.find("dt")
                    dd = div.find("dd")
                    if dt and dd:
                        key = dt.get_text(strip=True)
                        val = dd.get_text(strip=True)
                        nameplate_attrs[key] = val

        # Fetch Geo coordinates
        geo_location: Optional[GeoLocation] = None
        try:
            geo_url = f"{self.base_url}/portal/meters/{meter_id}/geo"
            geo_resp = await self._request_with_reauth("GET", geo_url)
            if geo_resp.status_code == 200:
                geo_data = geo_resp.json().get("data", {})
                lat = geo_data.get("latitude")
                lng = geo_data.get("longitude")
                geo_location = GeoLocation(
                    latitude=float(lat) if lat is not None else None,
                    longitude=float(lng) if lng is not None else None,
                )
        except Exception as e:
            logger.warning(f"Error fetching geo for meter {meter_id}: {e}")

        # Check export cache for any missing fields (like hierarchy codes, build, installType)
        cached_data: Optional[Dict[str, Any]] = None
        export_cache = await self._fetch_export_cache()
        if meter_id in export_cache:
            cached_data = export_cache[meter_id]

        # Extract structured hierarchy
        hierarchy_obj: Optional[MeterHierarchy] = None
        if cached_data and "hierarchy" in cached_data:
            ch = cached_data["hierarchy"]
            hierarchy_obj = MeterHierarchy(
                zone=HierarchyLevel(name=ch.get("zone", {}).get("name"), code=ch.get("zone", {}).get("code")),
                circle=HierarchyLevel(name=ch.get("circle", {}).get("name"), code=ch.get("circle", {}).get("code")),
                division=HierarchyLevel(name=ch.get("division", {}).get("name"), code=ch.get("division", {}).get("code")),
                subdivision=HierarchyLevel(name=ch.get("subdivision", {}).get("name"), code=ch.get("subdivision", {}).get("code")),
                substation=HierarchyLevel(name=ch.get("substation", {}).get("name"), code=ch.get("substation", {}).get("code")),
                feeder=HierarchyLevel(name=ch.get("feeder", {}).get("name"), code=ch.get("feeder", {}).get("code")),
                dt=HierarchyLevel(name=ch.get("dt", {}).get("name"), code=ch.get("dt", {}).get("code")),
                breadcrumbs=breadcrumbs,
            )
        elif breadcrumbs:
            hierarchy_obj = MeterHierarchy(breadcrumbs=breadcrumbs)

        # Fallback values
        serial = (
            nameplate_attrs.get("Serial No")
            or (cached_data.get("serialNo") if cached_data else None)
            or "N/A"
        )
        make = (
            nameplate_attrs.get("Make")
            or (cached_data.get("make") if cached_data else None)
            or "N/A"
        )
        phase_type = (
            nameplate_attrs.get("Phase Type")
            or (cached_data.get("phaseType") if cached_data else None)
            or "single"
        )
        status = (
            nameplate_attrs.get("Installation Status")
            or (cached_data.get("installStatus") if cached_data else None)
            or "UNKNOWN"
        )
        install_type = (
            nameplate_attrs.get("Installation Type")
            or (cached_data.get("installType") if cached_data else None)
        )
        build = (
            nameplate_attrs.get("Build")
            or (cached_data.get("build") if cached_data else None)
        )
        dt_code = (
            nameplate_attrs.get("DT")
            or (cached_data.get("dtCode") if cached_data else None)
        )

        return MeterDetailResponse(
            meter_id=meter_id,
            serial_number=serial,
            make=make,
            phase_type=phase_type,
            install_status=status,
            install_type=install_type,
            build=build,
            dt_code=dt_code,
            geo=geo_location,
            hierarchy=hierarchy_obj,
            nameplate_attributes=nameplate_attrs,
        )

    # ==================== Consumption Endpoints ====================

    async def get_meter_consumption(self, meter_id: str) -> MeterConsumptionResponse:
        """
        Retrieves consumption history for a meter, transforms numeric strings to floats,
        and computes summary statistics (min/max kWh, voltage range, latest reading).
        """
        url = f"{self.base_url}/portal/meters/{meter_id}/energy"
        resp = await self._request_with_reauth("GET", url)

        if resp.status_code != 200:
            raise Exception(f"Failed to fetch consumption data for meter {meter_id} (status {resp.status_code})")

        raw_data = resp.json().get("data", [])

        def parse_float(val: Any) -> Optional[float]:
            if val is None or val == "" or val == "?":
                return None
            try:
                return round(float(val), 3)
            except (ValueError, TypeError):
                return None

        readings: List[ConsumptionReading] = []
        kwh_values: List[float] = []
        volt_values: List[float] = []

        for item in raw_data:
            ts = item.get("timestamp", "")
            kwh = parse_float(item.get("kwh"))
            kvah = parse_float(item.get("kvah"))
            volt = parse_float(item.get("voltR"))

            if kwh is not None:
                kwh_values.append(kwh)
            if volt is not None:
                volt_values.append(volt)

            readings.append(
                ConsumptionReading(
                    timestamp=ts,
                    kwh=kwh,
                    kvah=kvah,
                    volt_r=volt,
                )
            )

        summary = ConsumptionSummary(
            total_readings=len(readings),
            latest_reading=readings[-1] if readings else None,
            min_kwh=min(kwh_values) if kwh_values else None,
            max_kwh=max(kwh_values) if kwh_values else None,
            min_volt_r=min(volt_values) if volt_values else None,
            max_volt_r=max(volt_values) if volt_values else None,
        )

        return MeterConsumptionResponse(
            meter_id=meter_id,
            summary=summary,
            readings=readings,
        )

    # ==================== Hierarchy & Transformers ====================

    async def get_hierarchy_tree(self) -> HierarchyTreeResponse:
        """
        Builds a comprehensive technical hierarchy tree of the electrical distribution grid.
        Organizes Transformers, Feeders, and connected Meters.
        """
        # Fetch DTs
        transformers: List[TransformerItem] = []
        page = 1
        while True:
            url = f"{self.base_url}/portal/dts"
            resp = await self._request_with_reauth("GET", url, params={"page": page})
            if resp.status_code != 200:
                break

            data = resp.json()
            raw_dts = data.get("data", [])
            if not raw_dts:
                break

            for d in raw_dts:
                transformers.append(
                    TransformerItem(
                        code=d.get("code", ""),
                        name=d.get("name", ""),
                        feeder_code=d.get("feederCode", ""),
                        capacity_kva=float(d.get("capacityKva", 0)),
                    )
                )

            total = data.get("total", 0)
            if len(transformers) >= total or len(raw_dts) < 20:
                break
            page += 1

        # Build tree representation
        export_cache = await self._fetch_export_cache()

        tree: Dict[str, Any] = {}
        for m_id, item in export_cache.items():
            h = item.get("hierarchy", {})
            zone_name = h.get("zone", {}).get("name", "Unknown Zone")
            feeder_name = h.get("feeder", {}).get("name", "Unknown Feeder")
            dt_code = item.get("dtCode", "Unknown DT")

            if zone_name not in tree:
                tree[zone_name] = {"feeders": {}}

            if feeder_name not in tree[zone_name]["feeders"]:
                tree[zone_name]["feeders"][feeder_name] = {"transformers": {}}

            if dt_code not in tree[zone_name]["feeders"][feeder_name]["transformers"]:
                tree[zone_name]["feeders"][feeder_name]["transformers"][dt_code] = {
                    "meters": []
                }

            tree[zone_name]["feeders"][feeder_name]["transformers"][dt_code]["meters"].append({
                "meter_id": m_id,
                "serial_number": item.get("serialNo"),
                "make": item.get("make"),
                "install_status": item.get("installStatus")
            })

        return HierarchyTreeResponse(
            total_transformers=len(transformers),
            transformers=transformers,
            tree=tree,
        )

# Global singleton client instance
portal_client = UrjaPortalClient()
