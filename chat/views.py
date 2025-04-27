import json
import time

from django.shortcuts import render
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
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

        # Определяем контекст разговора
        context = ['Тест 1', 'Тест 2', 'Тест 3']

        # Получаем кнопки для текущего контекста
        buttons = list(SuggestionButton.objects.filter(context=context).values('text'))

        # Если кнопок нет, возвращаем кнопки общего контекста
        if not buttons:
            buttons = list(SuggestionButton.objects.filter(context='general').values('text'))

        # Формируем ответное сообщение
        response_message = ''

        time.sleep(2)

        return JsonResponse({
            'message': response_message,
            'suggestions': buttons
        })
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)
