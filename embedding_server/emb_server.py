from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Union
from sentence_transformers import SentenceTransformer
import asyncio

# Load model once at startup
model = SentenceTransformer("all-MiniLM-L6-v2", device="cpu")

# Define FastAPI app
app = FastAPI(title="Embedding API")


# Define request model
class EmbedRequest(BaseModel):
    texts: Union[str, List[str]]


# Define response model
class EmbedResponse(BaseModel):
    embeddings: List[List[float]]


@app.post("/embed", response_model=EmbedResponse)
async def get_embeddings(request: EmbedRequest):
    # Support both single string and list of strings
    texts = [request.texts] if isinstance(request.texts, str) else request.texts

    # Run in thread pool to avoid blocking the event loop
    embeddings = await asyncio.to_thread(model.encode, texts, convert_to_numpy=True)

    return {"embeddings": embeddings.tolist()}
