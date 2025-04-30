import json

from django.shortcuts import render
from django.http import StreamingHttpResponse, JsonResponse  # Изменено
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from qdrant.search import get_relevant_chunks
from server_site.send_user_query import AsyncLlmRpcClient

llm_client = AsyncLlmRpcClient()


@csrf_exempt
async def index(request):
    return render(request, 'chat/index.html', {'initial_buttons': [
        {'text': 'Проверить баланс'}, {'text': 'Перевод средств'}
    ]})


async def stream_llm_response(user_msg):
    try:
        found = get_relevant_chunks(user_msg, top_k=30)
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
            cleaned_json = cleaned_json.replace("'", '"')
            parsed_buttons = json.loads(cleaned_json)
            buttons = [{'text': item['question'].strip()} for item in parsed_buttons]
        except Exception as e:
            print(f"Другая ошибка при обработке кнопок: {e}")

        yield json.dumps({'type': 'suggestions', 'content': buttons}) + '\n'

    except Exception as e:
        print(f"Ошибка в stream_llm_response: {e}")
        yield json.dumps({'type': 'error', 'content': 'Произошла ошибка на сервере.'}) + '\n'


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
