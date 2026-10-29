import asyncio

import httpx

from campus_inbox.main import app


def test_health_endpoint() -> None:
    async def request() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok", "service": "campus-inbox"}

    asyncio.run(request())
