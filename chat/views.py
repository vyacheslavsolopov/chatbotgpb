import json
import time

from django.shortcuts import render
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from qdrant.search import get_relevant_chunks
from server_site.send_user_query import send_to_queue, wait_for_one_message
from .models import SuggestionButton


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
        prompt = prompt + "\n Пользователь написал: \n" + user_msg

        send_to_queue(prompt)

        # Формируем ответное сообщение
        response_message = wait_for_one_message()

        send_to_queue(
            response_message + "Предложи три вопроса, которые может задать пользователь далее. Ответ строго в формате списка и ничего больше: [\"Вопрос 1\", \"Вопрос 2\", \"Вопрос 3\"]")

        try:
            text_list = wait_for_one_message()[1:-2].split(', ')
            buttons = [{'text': t.strip()[1:-2]} for t in text_list]
        except Exception as e:
            print(e)
            buttons = []

        return JsonResponse({
            'message': response_message,
            'suggestions': buttons
        })
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)
