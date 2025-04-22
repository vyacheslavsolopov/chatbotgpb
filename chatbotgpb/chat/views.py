import json
import time

from django.shortcuts import render
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt


@csrf_exempt
def index(request):
    # Рендерим фронтенд-шаблон
    return render(request, 'chat/index.html')


@csrf_exempt
def api_chat(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'Only POST allowed'}, status=405)
    try:
        data = json.loads(request.body)
        user_msg = data.get('message', '').strip()
        print(user_msg)
        time.sleep(2)
        # Заглушка: просто возвращаем обратно текст
        stub_response = f'Заглушка ответа на: "{user_msg}"'
        return JsonResponse({'message': stub_response})
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)
