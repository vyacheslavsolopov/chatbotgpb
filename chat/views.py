import json
import time

from django.shortcuts import render
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from qdrant.search import get_relevant_chunks
from server_site.send_user_query import LlmRpcClient
from .models import SuggestionButton

llm_client = LlmRpcClient()


@csrf_exempt
def index(request):
    # Получаем кнопки для начального контекста
    initial_buttons = list(SuggestionButton.objects.filter(context='initial').values('text'))
    return render(request, 'chat/index.html', {'initial_buttons': initial_buttons})


@csrf_exempt
def api_chat(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'Only POST allowed'}, status=405)
    try:
        data = json.loads(request.body)
        user_msg = data.get('message', '').strip()

        found = get_relevant_chunks(user_msg, top_k=30)

        prompt = f"Найдены документы по запросу пользователя: {' '.join(found)}."
        prompt = prompt.replace('\n', ' ')
        prompt = prompt + "\n Пользователь написал: \n" + user_msg + "\n по умолчанию отвечай про газпромбанк, если не указаны конкретные источники, разделяй ответ на блоки, чтобы удобнее читалось, и используй конкретные цифры для описания комиссии и других аспектов."

        response_message = llm_client.call(prompt).get('llm_response', '')

        prompt_buttons = response_message + ("Предложи три вопроса, которые может задать пользователь далее. "
                                             "Вопросы отдай строго в json формате, чтобы сработала команда json.load(). "
                                             "[{'question': ''}, {'question': ''}, {'question': ''}].")
        text_list = llm_client.call(prompt_buttons).get('llm_response', '')

        try:
            text_list = text_list.strip('```').strip('json')
            buttons = [{'text': t['question'].strip().capitalize()} for t in json.loads(text_list)]
        except Exception as e:
            print(e)
            buttons = []

        return JsonResponse({
            'message': response_message,
            'suggestions': buttons
        })
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)
