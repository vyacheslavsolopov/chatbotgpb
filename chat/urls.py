from django.urls import path
from . import views


app_name = 'chat'

urlpatterns = [
    path('', views.index, name='index'),
    path('api/chat/', views.api_chat, name='api_chat'),
    path('upload/', views.upload_page, name='upload_page'),
    path('submit/', views.handle_upload, name='handle_upload')
]
