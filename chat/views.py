import asyncio
import json

from django.shortcuts import render, redirect
from django.http import StreamingHttpResponse, JsonResponse  # Изменено
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt, csrf_protect
from django.views.decorators.http import require_POST, require_GET

from .qdrant.search import get_relevant_chunks
from .server_site.send_user_query import AsyncLlmRpcClient

llm_client = AsyncLlmRpcClient()


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
    return render(request, 'chat/upload.html')


@require_POST
def handle_upload(request):
    try:
        # Retrieve data from the POST request
        doc_type = request.POST.get('document_type')  # Get selected type
        uploaded_file = request.FILES.get('file-upload')  # Get the uploaded file

        if not uploaded_file:
            return JsonResponse({'error': 'Файл не был загружен.'}, status=400)
        if not doc_type:
            return JsonResponse({'error': 'Тип документа не выбран.'}, status=400)

        # --- !! Placeholder: Process the uploaded file here !! ---
        # Example: Save the file, update database, call another service, etc.
        print(f"Received file: {uploaded_file.name}")
        print(f"Document type: {doc_type}")
        print(f"File size: {uploaded_file.size}")
        print(f"Content type: {uploaded_file.content_type}")

        # Example: Saving the file (Make sure MEDIA_ROOT is configured in settings.py)
        # file_path = os.path.join(settings.MEDIA_ROOT, 'uploads', uploaded_file.name)
        # os.makedirs(os.path.dirname(file_path), exist_ok=True) # Create directory if needed
        # with open(file_path, 'wb+') as destination:
        #     for chunk in uploaded_file.chunks():
        #         destination.write(chunk)
        # print(f"File saved to: {file_path}")
        # --- End Placeholder ---

        # Respond to the frontend. Since the JS expects a redirect on success,
        # we redirect back to the chat index page.
        # Assumes your chat index URL is named 'index' in your urls.py
        # Adjust 'chat:index' if your app_name or url name is different
        return redirect(reverse('chat:index'))
    except Exception as e:
        print(f"Ошибка при обработке загрузки файла: {e}")
        # Return an error response that the JS might handle (optional)
        return JsonResponse({'error': 'Внутренняя ошибка сервера при обработке файла.'}, status=500)


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
