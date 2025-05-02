# 🧠 Gazprombank Virtual Assistant

Веб-приложение на Django с асинхронной поддержкой через ASGI, позволяющее пользователям:

* общаться с виртуальным помощником
* загружать документы (PDF, DOCX, TXT)

---

## 🚀 Быстрый запуск

### 🔧 Установите зависимости

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### ⚙️ Запуск локального сервера

```bash
python manage.py migrate
python manage.py runserver
```

### 🚦 Продакшн-сервер через Gunicorn + Uvicorn

```bash
gunicorn chatbotgpb.asgi:application -k uvicorn.workers.UvicornWorker -b 127.0.0.1:8000
```

---

## 🗂 Структура проекта

* `chatbotgpb/` — основной Django-проект.
* `chat/` — приложение с логикой чата и загрузки файлов.
* `templates/`:

    * `index.html` — страница виртуального помощника.
    * `upload.html` — интерфейс загрузки документов.
* `static/` — стили и скрипты.
* `emb_server.py`, `parsing.py`, `search.py` — backend-обработка документов и генерация эмбеддингов.
* `views.py` — API для загрузки файлов и чата.
* `wait_user_query.py` — утилита для асинхронной генерации ответов или ожидания ввода.

---

## 📁 Функциональность

* **Чат-бот** с подсказками и генерацией NDJSON-ответов.
* **Загрузка документов** с UI drag-and-drop.
* Обработка PDF/DOCX/TXT и отправка в `emb_server` для парсинга.
* Обработка ошибок и уведомления на клиенте.

---

## 📌 Зависимости

Основные:

* `Django`
* `gunicorn`
* `uvicorn`
* `docx`, `PyPDF2` (для парсинга документов)
* `requests`, `uuid`, `aiofiles` (если используются в async-потоках)

---

## 🌐 URL-адреса

| URL               | Назначение                   |
|-------------------|------------------------------|
| `/`               | Виртуальный помощник         |
| `/upload/`        | Загрузка документов          |
| `/upload/submit/` | Обработка формы загрузки     |
| `/api/chat/`      | Эндпоинт для общения с ботом |

---

## 🛡 Безопасность

* CSRF-токен добавлен в шаблоны и JavaScript.
* Валидация типа и размера файлов (до 100MB).
* Защита от XSS через экранирование сообщений и markdown.
