# parsing.py

import os
from typing import List, Dict
from sentence_transformers import SentenceTransformer
from qdrant_client import QdrantClient

# ————— НАСТРОЙКИ —————
QDRANT_HOST     = "195.161.62.198"
QDRANT_REST_PORT= 6334
COLLECTION_NAME = "pdf_documents"
EMBED_MODEL_NAME= "all-MiniLM-L6-v2"
# ————————————————————

# инициализация
embedder = SentenceTransformer(EMBED_MODEL_NAME)
client   = QdrantClient(
    url=f"http://{QDRANT_HOST}:{QDRANT_REST_PORT}",
    prefer_grpc=False,
    timeout=30
)

def get_relevant_chunks(query: str, top_k: int = 5):
    """
    Ищет в Qdrant наиболее релевантные фрагменты текста по запросу.
    Возвращает список словарей с полями:
      - text:  текст фрагмента
      - source: имя файла-источника
      - score:  косинусная близость
    """
    query_emb = embedder.encode(query, convert_to_numpy=False)
    hits = client.search(
        collection_name=COLLECTION_NAME,
        query_vector=query_emb,
        limit=top_k,
        with_payload=True
    )
    results = []
    for hit in hits:
        payload = hit.payload
        results.append(payload.get("text", ""),)
    return results

if __name__ == "__main__":
    q = input("Введите запрос: ")
    found = get_relevant_chunks(q, top_k=10)
    for e in found:
        print(e)
