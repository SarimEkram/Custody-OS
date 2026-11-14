"""Text embeddings for Vector Search.

EMBEDDINGS_PROVIDER=atlas_auto (default, verified on our cluster): Atlas Automated Embedding embeds
`summary` itself, so the app sends text and never calls a model. `voyage`: we call the
MongoDB-hosted Voyage endpoint (ai.mongodb.com) and store `embedding` arrays.
"""

from __future__ import annotations

import httpx

from agent.config import settings

AUTO = settings.embeddings_provider == "atlas_auto"


async def embed(texts: list[str], input_type: str = "document") -> list[list[float]]:
    async with httpx.AsyncClient(timeout=20) as http:
        r = await http.post(
            f"{settings.voyage_base_url}/embeddings",
            headers={"Authorization": f"Bearer {settings.voyage_api_key}"},
            json={"input": texts, "model": settings.voyage_model, "input_type": input_type},
        )
        r.raise_for_status()
        return [row["embedding"] for row in r.json()["data"]]


async def vector_stage(index: str, text: str, *, limit: int = 20, num_candidates: int = 200,
                       filters: dict | None = None) -> dict:
    """Build a $vectorSearch stage for a text query under either provider."""
    stage: dict = {"index": index, "limit": limit, "numCandidates": num_candidates}
    if AUTO:
        stage |= {"path": "summary", "query": {"text": text}}
    else:
        stage |= {"path": "embedding", "queryVector": (await embed([text], "query"))[0]}
    if filters:
        stage["filter"] = filters
    return {"$vectorSearch": stage}
