from django.urls import path
from . import views

urlpatterns = [
    path('', views.index, name='chat_index'),
    path('api/chat/', views.api_chat, name='api_chat'),
]