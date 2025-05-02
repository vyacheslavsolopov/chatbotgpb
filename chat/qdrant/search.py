import httpx
import numpy as np
from qdrant_client import QdrantClient

# ————— НАСТРОЙКИ —————
QDRANT_HOST = "195.161.62.198"
QDRANT_REST_PORT = 6334
COLLECTION_NAME = "pdf_documents"
EMBED_MODEL_NAME = "all-MiniLM-L6-v2"
# ————————————————————

API_URL = "http://localhost:8800/embed"


async def encode_async_embeddings(texts):
    async with httpx.AsyncClient() as client:
        payload = {"texts": texts}
        response = await client.post(API_URL, json=payload)
        response.raise_for_status()
        output = response.json()["embeddings"]
        return np.array(output)


client = QdrantClient(
    url=f"http://{QDRANT_HOST}:{QDRANT_REST_PORT}",
    prefer_grpc=False,
    timeout=30
)


async def get_relevant_chunks(query: str, top_k: int = 5):
    """
    Ищет в Qdrant наиболее релевантные фрагменты текста по запросу.
    Возвращает список словарей с полями:
      - text:  текст фрагмента
      - source: имя файла-источника
      - score:  косинусная близость
    """
    query_emb = await encode_async_embeddings([query])
    hits = client.search(
        collection_name=COLLECTION_NAME,
        query_vector=query_emb[0],
        limit=top_k,
        with_payload=True
    )
    results = []
    for hit in hits:
        payload = hit.payload
        results.append(payload.get("text", ""), )
    return results
