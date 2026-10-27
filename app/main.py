import logging
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import settings
from app.client import portal_client
from app.models import (
    LoginRequest,
    AuthStatus,
    MeterListResponse,
    MeterDetailResponse,
    MeterConsumptionResponse,
    HierarchyTreeResponse,
    HealthResponse,
)

logger = logging.getLogger("api")
logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: initialize client and warm up authentication session
    logger.info("Initializing Flock Energy Urja Meter Ops API service...")
    try:
        auth_status = await portal_client.login()
        if auth_status.authenticated:
            logger.info("Auto-authentication with Urja Portal successful at startup.")
        else:
            logger.warning(f"Startup auth warning: {auth_status.message}")
    except Exception as e:
        logger.warning(f"Could not connect to portal at startup: {e}")

    yield

    # Shutdown
    logger.info("Shutting down API service and releasing client connections...")
    await portal_client.close()


app = FastAPI(
    title=settings.api_title,
    version=settings.api_version,
    description=settings.api_description,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

# Enable CORS for downstream frontend consumers
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ==================== Health & Meta Endpoints ====================

@app.get(
    "/health",
    response_model=HealthResponse,
    tags=["System"],
    summary="System Healthcheck",
    description="Returns service status, downstream Urja portal connectivity, and authentication state.",
)
async def health_check():
    auth_state = await portal_client.get_auth_status()
    return HealthResponse(
        status="healthy",
        portal_connected=True,
        authenticated=auth_state.authenticated,
        version=settings.api_version,
    )


# ==================== Authentication Endpoints ====================

@app.post(
    "/api/v1/auth/login",
    response_model=AuthStatus,
    tags=["Authentication"],
    summary="Portal Authentication",
    description=(
        "Authenticates against the legacy Urja portal using operator credentials. "
        "Establishes a persistent cookie session and returns authentication status. "
        "If no credentials are provided in the payload, default configured credentials are used."
    ),
)
async def login(req: Optional[LoginRequest] = None):
    try:
        email = req.get_email() if req else None
        pwd = req.password if req else None
        result = await portal_client.login(email=email, password=pwd)
        if not result.authenticated:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=result.message
            )
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Login error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Internal authentication error: {str(e)}"
        )


@app.get(
    "/api/v1/auth/status",
    response_model=AuthStatus,
    tags=["Authentication"],
    summary="Authentication Session Status",
    description="Checks whether the API wrapper has an active session with the legacy portal.",
)
async def get_auth_status():
    return await portal_client.get_auth_status()


# ==================== Meters Endpoints ====================

@app.get(
    "/api/v1/meters",
    response_model=MeterListResponse,
    tags=["Meters"],
    summary="List and Search Smart Meters",
    description=(
        "Retrieves a paginated list of smart meters. "
        "Supports query parameter `q` to search across meter IDs and serial numbers."
    ),
)
async def list_meters(
    q: str = Query(default="", description="Search query by meter ID or serial number"),
    page: int = Query(default=1, ge=1, description="Page number (1-indexed)"),
):
    try:
        return await portal_client.get_meters(query=q, page=page)
    except Exception as e:
        logger.error(f"Error listing meters: {e}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to retrieve meters from legacy portal: {str(e)}"
        )


@app.get(
    "/api/v1/meters/{meter_id}",
    response_model=MeterDetailResponse,
    tags=["Meters"],
    summary="Get Meter Details",
    description=(
        "Fetches granular metadata for a specific meter ID. "
        "Parses server-side rendered HTML nameplate specifications, retrieves normalized GPS coordinates, "
        "and reconstructs the grid hierarchy (Zone -> Feeder -> DT)."
    ),
)
async def get_meter_by_id(meter_id: str):
    try:
        meter = await portal_client.get_meter_details(meter_id)
        if not meter:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Meter '{meter_id}' not found in portal registry."
            )
        return meter
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching meter {meter_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to fetch meter details from legacy portal: {str(e)}"
        )


@app.get(
    "/api/v1/meters/{meter_id}/consumption",
    response_model=MeterConsumptionResponse,
    tags=["Meters"],
    summary="Get Meter Consumption History",
    description=(
        "Retrieves time-series energy consumption readings for a meter. "
        "Normalizes string metrics to numeric floating point values (kWh, kVAh, Voltage) "
        "and computes dataset summary statistics (min/max kWh, voltage range, latest reading)."
    ),
)
async def get_meter_consumption(meter_id: str):
    try:
        consumption = await portal_client.get_meter_consumption(meter_id)
        return consumption
    except Exception as e:
        logger.error(f"Error fetching consumption for meter {meter_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to fetch consumption data for meter '{meter_id}': {str(e)}"
        )


# ==================== Hierarchy Endpoints ====================

@app.get(
    "/api/v1/hierarchy",
    response_model=HierarchyTreeResponse,
    tags=["Hierarchy"],
    summary="Retrieve Electrical Grid Hierarchy",
    description=(
        "Builds and returns an organized network hierarchy tree mapping distribution transformers, "
        "feeders, and their connected smart meters."
    ),
)
async def get_hierarchy():
    try:
        return await portal_client.get_hierarchy_tree()
    except Exception as e:
        logger.error(f"Error generating hierarchy tree: {e}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to build hierarchy tree: {str(e)}"
        )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host=settings.api_host, port=settings.api_port, reload=True)
