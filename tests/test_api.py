import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from app.main import app

@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

@pytest.mark.asyncio
async def test_health_check(client: AsyncClient):
    resp = await client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "healthy"
    assert data["portal_connected"] is True
    assert "version" in data

@pytest.mark.asyncio
async def test_auth_status(client: AsyncClient):
    resp = await client.get("/api/v1/auth/status")
    assert resp.status_code == 200
    data = resp.json()
    assert "authenticated" in data

@pytest.mark.asyncio
async def test_list_meters(client: AsyncClient):
    resp = await client.get("/api/v1/meters?page=1")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] > 0
    assert len(data["data"]) > 0
    first_meter = data["data"][0]
    assert "meter_id" in first_meter
    assert "serial_number" in first_meter
    assert "make" in first_meter
    assert "phase_type" in first_meter

@pytest.mark.asyncio
async def test_search_meters(client: AsyncClient):
    resp = await client.get("/api/v1/meters?q=J100000")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] >= 1
    assert any(m["meter_id"] == "J100000" for m in data["data"])

@pytest.mark.asyncio
async def test_meter_details(client: AsyncClient):
    resp = await client.get("/api/v1/meters/J100000")
    assert resp.status_code == 200
    data = resp.json()
    assert data["meter_id"] == "J100000"
    assert data["serial_number"] == "SE33962"
    assert data["make"] == "HPL"
    assert data["geo"] is not None
    assert data["geo"]["latitude"] is not None
    assert data["geo"]["longitude"] is not None
    assert "nameplate_attributes" in data

@pytest.mark.asyncio
async def test_meter_consumption(client: AsyncClient):
    resp = await client.get("/api/v1/meters/J100000/consumption")
    assert resp.status_code == 200
    data = resp.json()
    assert data["meter_id"] == "J100000"
    assert "summary" in data
    assert data["summary"]["total_readings"] > 0
    assert len(data["readings"]) > 0
    first_reading = data["readings"][0]
    assert "timestamp" in first_reading
    assert isinstance(first_reading["kwh"], (float, int))

@pytest.mark.asyncio
async def test_hierarchy(client: AsyncClient):
    resp = await client.get("/api/v1/hierarchy")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_transformers"] > 0
    assert len(data["transformers"]) > 0
    assert "tree" in data
