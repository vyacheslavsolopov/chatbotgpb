from fastapi import FastAPI
from pydantic import BaseModel
from typing import List, Union
from sentence_transformers import SentenceTransformer
import asyncio

model = SentenceTransformer("all-MiniLM-L6-v2", device="cpu")

app = FastAPI(title="Embedding API")


class EmbedRequest(BaseModel):
    texts: Union[str, List[str]]


class EmbedResponse(BaseModel):
    embeddings: List[List[float]]


@app.post("/embed", response_model=EmbedResponse)
async def get_embeddings(request: EmbedRequest):
    texts = [request.texts] if isinstance(request.texts, str) else request.texts

    embeddings = await asyncio.to_thread(model.encode, texts, convert_to_numpy=True)

    return {"embeddings": embeddings.tolist()}
