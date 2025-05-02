from django.urls import path
from . import views

urlpatterns = [
    path('', views.index, name='chat_index'),
    path('api/chat/', views.api_chat, name='api_chat'),
    path('upload/', views.upload_page, name='upload_page'),
    path('submit/', views.handle_upload, name='handle_upload')
]
