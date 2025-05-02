import asyncio
import json
import os
import io
import uuid

from django.shortcuts import render, redirect
from django.http import StreamingHttpResponse, JsonResponse  # Изменено
from django.urls import reverse
# from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt, csrf_protect
from django.views.decorators.http import require_POST, require_GET

from .qdrant.search import get_relevant_chunks
from .server_site.send_user_query import AsyncLlmRpcClient
from .qdrant.parsing import (
    extract_text_from_pdf,
    extract_text_from_docx,
    chunk_text,
    get_embeddings,
    client as qdrant_client,
    COLLECTION_NAME,
    BATCH_SIZE,
)

llm_client = AsyncLlmRpcClient()


# try:
#     print(reverse('chat:index'))
#     print(reverse('chat:handle_upload'))
# except Exception as e:
#     print(f"Error reversing URL: {e}")


@csrf_exempt
async def index(request):
    initial_buttons = [
        {"text": "Как открыть счет?"},
        {"text": "Какие документы нужны для кредита?"},
        {"text": "Расскажи о вкладах"},
    ]
    return render(request, 'chat/index.html', {'initial_buttons': initial_buttons})


async def stream_llm_response(user_msg):
    try:
        found = await get_relevant_chunks(user_msg, top_k=30)
        context_prompt = f"Найдены документы по запросу пользователя: {' '.join(found)}."
        context_prompt = context_prompt.replace('\n', ' ')
        full_prompt = context_prompt + "\n Пользователь написал: \n" + user_msg + "\n по умолчанию отвечай про газпромбанк, если не указаны конкретные источники, разделяй ответ на блоки, чтобы удобнее читалось, и используй конкретные цифры для описания комиссии и других аспектов."
        complete_response_text = ""

        async for chunk in llm_client.stream(full_prompt, timeout_sec=20):
            complete_response_text = chunk
            yield json.dumps({'type': 'chunk', 'content': chunk}) + '\n'

        prompt_buttons = (complete_response_text +
                          " Предложи три вопроса, которые может задать пользователь далее. "
                          "Вопросы отдай строго в json формате, чтобы сработала команда json.load(). "
                          "[{'question': ''}, {'question': ''}, {'question': ''}].")

        buttons_response = await llm_client.call(prompt_buttons)
        buttons_response = buttons_response.get('llm_response', '')
        buttons = []
        try:
            cleaned_json = buttons_response.strip().lstrip('```json').rstrip('```').strip()
            cleaned_json = cleaned_json.strip().lstrip('```json').rstrip('```').strip()
            cleaned_json = cleaned_json.replace("'", '"')
            print(cleaned_json)
            parsed_buttons = json.loads(cleaned_json)
            buttons = [{'text': item['question'].strip()} for item in parsed_buttons]
        except Exception as e:
            print(f"Другая ошибка при обработке кнопок: {e}")

        yield json.dumps({'type': 'suggestions', 'content': buttons}) + '\n'

    except Exception as e:
        print(f"Ошибка в stream_llm_response: {e}")
        yield json.dumps({'type': 'error', 'content': 'Произошла ошибка на сервере.'}) + '\n'


@require_GET
def upload_page(request):
    return render(request, 'chat/upload.html',
                  {
                      'upload_submit_url': 'http://127.0.0.1:8000/submit/'
                  })


@require_POST
@csrf_exempt
def handle_upload(request):
    uploaded = request.FILES.get('file-upload')
    if not uploaded:
        return JsonResponse({'error': 'Файл не был загружен.'}, status=400)

    name = uploaded.name.lower()
    ext = os.path.splitext(name)[1]
    if ext not in ('.pdf', '.docx', '.txt'):
        return JsonResponse({'error': 'Недопустимый тип файла.'}, status=400)
    if uploaded.size > 100 * 1024 * 1024:
        return JsonResponse({'error': 'Размер файла превышает 100 MB.'}, status=400)

    # Читаем весь файл в память
    data = uploaded.read()
    # Извлекаем текст
    if ext == '.pdf':
        text = extract_text_from_pdf(io.BytesIO(data))
    elif ext == '.docx':
        text = extract_text_from_docx(io.BytesIO(data))
    else:  # .txt
        text = data.decode(errors='ignore')

    # Чанки и эмбеддинги
    chunks = chunk_text(text)
    embeddings = get_embeddings(chunks)

    # Готовим точки и заливаем в Qdrant
    points = []
    for chunk, emb in zip(chunks, embeddings):
        points.append({
            'id': str(uuid.uuid4()),
            'vector': emb,
            'payload': {'text': chunk, 'source': name}
        })
        if len(points) >= BATCH_SIZE:
            qdrant_client.upsert(collection_name=COLLECTION_NAME, points=points)
            points.clear()
    if points:
        qdrant_client.upsert(collection_name=COLLECTION_NAME, points=points)

    return redirect(reverse('chat:index'))


@csrf_exempt
@require_POST
async def api_chat(request):
    try:
        data = json.loads(request.body)
        user_msg = data.get('message', '').strip()
        if not user_msg:
            return JsonResponse({'error': 'Сообщение не может быть пустым'}, status=400)

        response = StreamingHttpResponse(
            stream_llm_response(user_msg),
            content_type='application/x-ndjson'
        )
        # response['X-Accel-Buffering'] = 'no' # Для Nginx, чтобы не буферизовал ответ
        return response

    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)
    except Exception as e:
        print(f"Ошибка в api_chat view: {e}")
        return JsonResponse({'error': 'Внутренняя ошибка сервера'}, status=500)
