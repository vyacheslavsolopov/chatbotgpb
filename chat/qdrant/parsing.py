import os
import uuid
from typing import List
from PyPDF2 import PdfReader
from docx import Document
from qdrant_client import QdrantClient, models

from chat.qdrant.search import encode_async_embeddings

# ————— НАСТРОЙКИ —————
QDRANT_HOST = "195.161.62.198"
QDRANT_REST_PORT = 6334
COLLECTION_NAME = "pdf_documents"
EMBED_MODEL_NAME = "all-MiniLM-L6-v2"
CHUNK_SIZE = 200
BATCH_SIZE = 256
DOC_FOLDER = "documents"
# ————————————————————

client = QdrantClient(
    url=f"http://{QDRANT_HOST}:{QDRANT_REST_PORT}",
    prefer_grpc=False,
    timeout=30
)

if client.collection_exists(collection_name=COLLECTION_NAME):
    client.delete_collection(collection_name=COLLECTION_NAME)
client.create_collection(
    collection_name=COLLECTION_NAME,
    vectors_config=models.VectorParams(
        size=384,
        distance=models.Distance.COSINE
    )
)


def extract_text_from_pdf(path: str) -> str:
    """
    Извлекает текст из PDF-файла.
    """
    reader = PdfReader(path)
    pages = []
    for page in reader.pages:
        txt = page.extract_text() or ""
        pages.append(txt)
    return "\n".join(pages)


def extract_text_from_docx(path: str) -> str:
    """
    Извлекает текст из DOCX-файла.
    """
    doc = Document(path)
    paragraphs = [p.text for p in doc.paragraphs]
    return "\n".join(paragraphs)


def extract_text(path: str) -> str:
    """
    Универсальный парсер: PDF или DOCX.
    """
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        return extract_text_from_pdf(path)
    if ext == ".docx":
        return extract_text_from_docx(path)
    raise ValueError(f"Unsupported file type: {ext}")


def chunk_text(text: str, size: int = CHUNK_SIZE) -> List[str]:
    """
    Разбивает текст на чанки по размеру.
    """
    return [text[i:i + size].strip() for i in range(0, len(text), size) if text[i:i + size].strip()]


async def upload_document_to_qdrant(path: str) -> None:
    """
    Полная обработка одного документа: парсинг, чанкирование,
    получение эмбеддингов и загрузка в коллекцию Qdrant.
    """
    text = extract_text(path)
    chunks = chunk_text(text)
    embeddings = await encode_async_embeddings(chunks)

    points: List[models.PointStruct] = []
    source = os.path.basename(path)
    for chunk, emb in zip(chunks, embeddings):
        points.append(models.PointStruct(id=str(uuid.uuid4()), vector=emb, payload={"text": chunk, "source": source}))
        if len(points) >= BATCH_SIZE:
            client.upsert(collection_name=COLLECTION_NAME, points=points)
            points.clear()
    if points:
        client.upsert(collection_name=COLLECTION_NAME, points=points)


def get_collection_stats() -> int:
    """
    Проверка заполненности коллекции: возвращает общее число точек.
    """
    stats = client.count(collection_name=COLLECTION_NAME)
    print(f"Total points in '{COLLECTION_NAME}': {stats.count}")
    return stats.count
