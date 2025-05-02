# parsing.py

import os
from typing import List, Dict

import httpx
import numpy as np
from sentence_transformers import SentenceTransformer
from qdrant_client import QdrantClient

# ————— НАСТРОЙКИ —————
QDRANT_HOST = "195.161.62.198"
QDRANT_REST_PORT = 6334
COLLECTION_NAME = "pdf_documents"
EMBED_MODEL_NAME = "all-MiniLM-L6-v2"
# ————————————————————

# инициализация
API_URL = "http://localhost:8800/embed"  # Adjust if running on a different host/port


async def get_embeddings(texts):
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
    query_emb = await get_embeddings([query])
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


if __name__ == "__main__":
    q = input("Введите запрос: ")
    found = get_relevant_chunks(q, top_k=10)
    for e in found:
        print(e)
