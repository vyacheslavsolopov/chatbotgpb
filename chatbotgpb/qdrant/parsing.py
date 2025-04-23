import os
import uuid
from typing import List

from PyPDF2 import PdfReader
from docx import Document
from sentence_transformers import SentenceTransformer
from qdrant_client import QdrantClient, models

# ————— НАСТРОЙКИ —————
QDRANT_HOST = "195.161.62.198"  # хост Qdrant
QDRANT_GRPC_PORT = 6334  # gRPC-порт Qdrant
COLLECTION_NAME = "pdf_documents"  # имя коллекции для индексирования
EMBED_MODEL_NAME = "all-MiniLM-L6-v2"  # модель эмбеддингов SentenceTransformer
CHUNK_SIZE = 200  # размер текстового чанка в символах
BATCH_SIZE = 256  # размер батча для upsert-запросов
DOC_FOLDER = "documents"  # папка с PDF и DOCX файлами
# ————————————————————

embedder = SentenceTransformer(EMBED_MODEL_NAME)
client = QdrantClient(
    host=QDRANT_HOST,
    grpc_port=QDRANT_GRPC_PORT,
    prefer_grpc=True,
    timeout=30
)
if client.collection_exists(collection_name=COLLECTION_NAME):
    client.delete_collection(collection_name=COLLECTION_NAME)
client.create_collection(
    collection_name=COLLECTION_NAME,
    vectors_config=models.VectorParams(
        size=embedder.get_sentence_embedding_dimension(),
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
    doc = docx.Document(path)
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
    chunks = []
    for i in range(0, len(text), size):
        seg = text[i:i + size].strip()
        if seg:
            chunks.append(seg)
    return chunks


def get_embeddings(chunks: List[str]) -> List[List[float]]:
    """
    Получает эмбеддинги для списка текстовых чанков.
    """
    embs = embedder.encode(chunks, convert_to_numpy=True)
    return embs.tolist()


def upload_document_to_qdrant(path: str) -> None:
    """
    Полная обработка одного документа: парсинг, чанкирование,
    получение эмбеддингов и загрузка в коллекцию Qdrant.
    """
    text = extract_text(path)
    chunks = chunk_text(text)
    embeddings = get_embeddings(chunks)

    points = []
    source = os.path.basename(path)
    for chunk, emb in zip(chunks, embeddings):
        pt = models.PointStruct(
            id=str(uuid.uuid4()),
            vector=emb,
            payload={"text": chunk, "source": source}
        )
        points.append(pt)
        if len(points) >= BATCH_SIZE:
            client.upsert(collection_name=COLLECTION_NAME, points=points)
            points.clear()
    if points:
        client.upsert(collection_name=COLLECTION_NAME, points=points)


if __name__ == "__main__":
    # Индексируем все документы из папки DOC_FOLDER
    for fname in os.listdir(DOC_FOLDER):
        path = os.path.join(DOC_FOLDER, fname)
        if fname.lower().endswith(('.pdf', '.docx')):
            print(f"Uploading {fname}...")
            upload_document_to_qdrant(path)
    print("All documents uploaded.")
