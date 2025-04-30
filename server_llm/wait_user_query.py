import json
import pika
import requests
import logging
import time

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

LLM_API_URL = "http://127.0.0.1:8080/generate"
MAX_TOKENS_TO_GENERATE = 1024

RABBITMQ_HOST = '195.161.62.198'
TASK_QUEUE_NAME = 'llm_task_queue'


def query_llm(prompt_text: str) -> str | None:
    """Sends a prompt to the local LLM and returns the response."""

    formatted_prompt = f"<|im_start|>user\n{prompt_text}\n<|im_end|>\n<|im_start|>assistant\n"

    headers = {"Content-Type": "application/json"}
    data = {
        "prompt": formatted_prompt,
        "max_tokens": MAX_TOKENS_TO_GENERATE
    }

    logger.info(
        f"Sending prompt to LLM: {formatted_prompt[:100]}...")

    try:
        response = requests.post(LLM_API_URL, headers=headers, json=data,
                                 timeout=120)
        response.raise_for_status()

        result = response.json()
        logger.info(f"LLM Raw Response: {result}")

        if 'content' in result:
            llm_response = result['content']
        elif 'text' in result:
            llm_response = result['text'][0].split("assistant")[1]
        else:
            logger.error(f"LLM response key 'content' not found in: {result}")
            return "Error: Could not parse LLM response."

        return llm_response.strip()

    except requests.exceptions.RequestException as e:
        logger.error(f"Error connecting to LLM API: {e}")
        return f"Sorry, I couldn't connect to the language model: {e}"
    except json.JSONDecodeError as e:
        logger.error(f"Error decoding LLM JSON response: {e}")
        return "Sorry, I received an invalid response from the language model."
    except Exception as e:
        logger.error(f"An unexpected error occurred during LLM query: {e}")
        return "An unexpected error occurred while processing your request."


def process_with_llm(message_body):
    try:
        data = json.loads(message_body)
        user_message = data.get('message', '')
        print(f" [.] Получено для обработки: '{user_message[:50]}...'")

        response_text = query_llm(user_message)

        response_payload = {
            "llm_response": response_text
        }
        return json.dumps(response_payload)

    except json.JSONDecodeError:
        print(" [!] Ошибка декодирования JSON")
        return json.dumps({"error": "Invalid JSON format received"})
    except Exception as e:
        print(f" [!] Ошибка во время обработки LLM: {e}")
        return json.dumps({"error": f"LLM processing error: {e}"})


def on_request(ch, method, props, body):
    """ Callback функция, вызываемая при получении сообщения из очереди TASK_QUEUE_NAME """
    correlation_id = props.correlation_id
    reply_to_queue = props.reply_to

    print(
        f"\n [x] Получен запрос (ID: {correlation_id}) от '{reply_to_queue if reply_to_queue else 'No Reply Queue!'}'. Обработка...")

    response = process_with_llm(body)

    if not reply_to_queue:
        print(" [!] Нет очереди для ответа (reply_to не указан). Результат не отправлен.")
    else:
        try:
            ch.basic_publish(
                exchange='',
                routing_key=reply_to_queue,
                properties=pika.BasicProperties(
                    correlation_id=correlation_id,
                    content_type='application/json'
                ),
                body=response
            )
            print(f" [.] Ответ для {correlation_id} отправлен в {reply_to_queue}")
        except Exception as e:
            print(f" [!] Ошибка отправки ответа для {correlation_id} в {reply_to_queue}: {e}")

    ch.basic_ack(delivery_tag=method.delivery_tag)
    print(f" [.] Сообщение {correlation_id} подтверждено (acknowledged).")


def start_worker():
    connection = None
    while True:
        try:
            connection = pika.BlockingConnection(pika.ConnectionParameters(host=RABBITMQ_HOST))
            channel = connection.channel()

            channel.queue_declare(queue=TASK_QUEUE_NAME, durable=True)
            print(f" [*] Очередь '{TASK_QUEUE_NAME}' готова к приему сообщений.")

            channel.basic_qos(prefetch_count=1)

            channel.basic_consume(queue=TASK_QUEUE_NAME, on_message_callback=on_request)

            print(" [*] Ожидание сообщений. Для выхода нажмите CTRL+C")
            channel.start_consuming()

        except pika.exceptions.AMQPConnectionError as e:
            print(f" [!] Потеряно соединение с RabbitMQ: {e}. Попытка переподключения через 5 секунд...")
            if connection and connection.is_open:
                connection.close()
            time.sleep(5)
        except KeyboardInterrupt:
            print(" [*] Получен сигнал остановки. Завершение работы...")
            if connection and connection.is_open:
                connection.close()
            break
        except Exception as e:
            print(f" [!] Непредвиденная ошибка: {e}. Перезапуск через 10 секунд...")
            if connection and connection.is_open:
                connection.close()
            time.sleep(10)


if __name__ == '__main__':
    start_worker()
