import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = 'change-me-to-a-secure-key'
DEBUG = True
ALLOWED_HOSTS = ['*']

INSTALLED_APPS = [
    'django.contrib.staticfiles',  # статические файлы
    'chat',                      # наше приложение
]

MIDDLEWARE = [
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
]

ROOT_URLCONF = 'chatbotgpb.urls'

TEMPLATES = [{
    'BACKEND': 'django.template.backends.django.DjangoTemplates',
    'DIRS': [BASE_DIR / 'chat' / 'templates'],
    'APP_DIRS': True,
}]

STATIC_URL = '/static/'
STATICFILES_DIRS = [BASE_DIR / 'chat' / 'static']
