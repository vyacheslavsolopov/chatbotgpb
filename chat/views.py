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

        # Получаем кнопки для текущего контекста
        buttons = [{'text': 'text 1'}, {'text': 'text 2'}]

        # Если кнопок нет, возвращаем кнопки общего контекста
        if not buttons:
            buttons = list(SuggestionButton.objects.filter(context='general').values('text'))

        found = get_relevant_chunks(user_msg, top_k=30)

        prompt = f"Найдены документы по запросу пользователя: {' '.join(found)}."
        prompt = prompt.replace('\n', ' ')
        prompt = prompt + "\n Пользователь написал: \n" + user_msg

        send_to_queue(prompt)

        # Формируем ответное сообщение
        response_message = wait_for_one_message()

        return JsonResponse({
            'message': response_message,
            'suggestions': buttons
        })
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)
