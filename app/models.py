from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field

# ==================== Authentication Models ====================

class LoginRequest(BaseModel):
    email: Optional[str] = Field(default=None, description="Operator email address")
    username: Optional[str] = Field(default=None, description="Alternative alias for email")
    password: str = Field(description="Operator password")

    def get_email(self) -> str:
        return self.email or self.username or ""

class AuthStatus(BaseModel):
    authenticated: bool = Field(description="Whether a valid session is currently established")
    email: Optional[str] = Field(default=None, description="Logged in operator email")
    user_name: Optional[str] = Field(default=None, description="Logged in operator display name")
    message: str = Field(description="Authentication status message")

# ==================== Meter Models ====================

class MeterSummary(BaseModel):
    meter_id: str = Field(..., description="Unique meter identifier (e.g., J100000)")
    serial_number: str = Field(..., description="Physical serial number stamped on the meter")
    make: str = Field(..., description="Meter manufacturer (e.g., HPL, Secure, Genus)")
    phase_type: str = Field(..., description="Phase classification (e.g., single, three)")
    install_status: str = Field(..., description="Current operational status (e.g., Decommissioned, Active, Suspended)")
    dt_code: Optional[str] = Field(default=None, description="Associated Distribution Transformer code")

class MeterListResponse(BaseModel):
    total: int = Field(..., description="Total count of meters matching query across all pages")
    page: int = Field(..., description="Current page number")
    page_size: int = Field(..., description="Number of items per page")
    total_pages: int = Field(..., description="Total calculated pages")
    query: Optional[str] = Field(default="", description="Search filter query applied")
    data: List[MeterSummary] = Field(..., description="List of meters")

class GeoLocation(BaseModel):
    latitude: Optional[float] = Field(default=None, description="Normalized GPS Latitude")
    longitude: Optional[float] = Field(default=None, description="Normalized GPS Longitude")

class HierarchyLevel(BaseModel):
    name: Optional[str] = Field(default=None, description="Name of the administrative or technical entity")
    code: Optional[str] = Field(default=None, description="Unique code of the entity")

class MeterHierarchy(BaseModel):
    zone: Optional[HierarchyLevel] = Field(default=None, description="Zone level entity")
    circle: Optional[HierarchyLevel] = Field(default=None, description="Circle level entity")
    division: Optional[HierarchyLevel] = Field(default=None, description="Division level entity")
    subdivision: Optional[HierarchyLevel] = Field(default=None, description="Subdivision level entity")
    substation: Optional[HierarchyLevel] = Field(default=None, description="Substation level entity")
    feeder: Optional[HierarchyLevel] = Field(default=None, description="Feeder level entity")
    dt: Optional[HierarchyLevel] = Field(default=None, description="Distribution Transformer entity")
    breadcrumbs: List[str] = Field(default_factory=list, description="Ordered breadcrumb path from Zone to DT")

class MeterDetailResponse(BaseModel):
    meter_id: str = Field(..., description="Unique meter identifier")
    serial_number: str = Field(..., description="Physical serial number")
    make: str = Field(..., description="Manufacturer")
    phase_type: str = Field(..., description="Phase type")
    install_status: str = Field(..., description="Current operational status")
    install_type: Optional[str] = Field(default=None, description="Installation type (e.g., Whole Current, LT-CT)")
    build: Optional[str] = Field(default=None, description="Hardware build specification")
    dt_code: Optional[str] = Field(default=None, description="Distribution Transformer code")
    geo: Optional[GeoLocation] = Field(default=None, description="Geographical GPS coordinates")
    hierarchy: Optional[MeterHierarchy] = Field(default=None, description="Grid hierarchy mapping")
    nameplate_attributes: Dict[str, str] = Field(
        default_factory=dict,
        description="Parsed key-value specifications extracted from HTML nameplate / class data"
    )

# ==================== Consumption Models ====================

class ConsumptionReading(BaseModel):
    timestamp: str = Field(..., description="Reading timestamp in standardized format")
    kwh: Optional[float] = Field(default=None, description="Active energy in kilowatt-hours (kWh)")
    kvah: Optional[float] = Field(default=None, description="Apparent energy in kilovolt-ampere-hours (kVAh)")
    volt_r: Optional[float] = Field(default=None, description="R-phase voltage in Volts (V)")

class ConsumptionSummary(BaseModel):
    total_readings: int = Field(..., description="Count of readings in history")
    latest_reading: Optional[ConsumptionReading] = Field(default=None, description="Most recent timestamp reading")
    min_kwh: Optional[float] = Field(default=None, description="Minimum recorded active energy")
    max_kwh: Optional[float] = Field(default=None, description="Maximum recorded active energy")
    min_volt_r: Optional[float] = Field(default=None, description="Minimum recorded voltage")
    max_volt_r: Optional[float] = Field(default=None, description="Maximum recorded voltage")

class MeterConsumptionResponse(BaseModel):
    meter_id: str = Field(..., description="Target meter identifier")
    summary: ConsumptionSummary = Field(..., description="Aggregated analytics across the dataset")
    readings: List[ConsumptionReading] = Field(..., description="Time-series consumption data points")

# ==================== Hierarchy / Transformers Models ====================

class TransformerItem(BaseModel):
    code: str = Field(..., description="DT code identifier (e.g., DT-001)")
    name: str = Field(..., description="DT human-readable name")
    feeder_code: str = Field(..., description="Parent feeder code")
    capacity_kva: float = Field(..., description="Rated capacity in kVA")

class HierarchyTreeResponse(BaseModel):
    total_transformers: int = Field(..., description="Total distribution transformers indexed")
    transformers: List[TransformerItem] = Field(..., description="List of distribution transformers")
    tree: Dict[str, Any] = Field(..., description="Hierarchical tree grouped by Zone -> Feeder -> DT")

# ==================== Generic / Health Models ====================

class HealthResponse(BaseModel):
    status: str = Field(..., description="Service status (e.g. healthy)")
    portal_connected: bool = Field(..., description="Whether downstream Urja portal is responsive")
    authenticated: bool = Field(..., description="Whether valid session cookies are active")
    version: str = Field(..., description="API service version")
