# chatbotgpb/urls.py
from django.urls import path
from . import views

app_name = 'chat'  # Explicitly define the namespace

urlpatterns = [
    # Changed name from 'chat_index' to 'index' to match template tag {% url 'chat:index' %}
    path('', views.index, name='index'),
    path('api/chat/', views.api_chat, name='api_chat'),  # Keep this if needed, but wasn't in original template
    path('upload/', views.upload_page, name='upload_page'),  # Keep this if needed
    # Name 'handle_upload' matches template tag {% url 'chat:handle_upload' %}
    path('submit/', views.handle_upload, name='handle_upload')
]
