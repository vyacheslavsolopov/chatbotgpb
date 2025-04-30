import json
import socket
import time
import uuid

import pika
from pika.exceptions import AMQPConnectionError, StreamLostError, ChannelError

RABBITMQ_HOST = '195.161.62.198'
TASK_QUEUE_NAME = 'llm_task_queue'
DEFAULT_TIMEOUT = 30
HEARTBEAT_INTERVAL = 60


class LlmRpcClient:
    def __init__(self):
        self.connection = None
        self.channel = None
        self.callback_queue = None
        self.response = None
        self.corr_id = None
        self._connect()

    def _connect(self):
        self._safe_close()
        print(" [*] Попытка подключения к RabbitMQ...")
        try:
            self.connection = pika.BlockingConnection(
                pika.ConnectionParameters(
                    host=RABBITMQ_HOST,
                    heartbeat=HEARTBEAT_INTERVAL
                )
            )
            self.channel = self.connection.channel()

            result = self.channel.queue_declare(queue='', exclusive=True)
            self.callback_queue = result.method.queue
            print(f" [*] Callback queue создана: {self.callback_queue}")

            self.channel.basic_consume(
                queue=self.callback_queue,
                on_message_callback=self.on_response,
                auto_ack=True
            )
            print(" [*] Подключение и настройка завершены.")
            return True

        except AMQPConnectionError as e:
            print(f" [!] Не удалось подключиться к RabbitMQ: {e}")
            self._safe_close()
            return False
        except Exception as e:
            print(f" [!] Неожиданная ошибка при подключении: {e}")
            self._safe_close()
            return False

    def _safe_close(self):
        """Безопасно закрывает канал и соединение."""
        try:
            if self.channel and self.channel.is_open:
                self.channel.close()
                print(" [*] Канал закрыт.")
        except Exception as e:
            print(f" [!] Ошибка при закрытии канала: {e}")
        try:
            if self.connection and self.connection.is_open:
                self.connection.close()
                print(" [*] Соединение закрыто.")
        except Exception as e:
            print(f" [!] Ошибка при закрытии соединения: {e}")
        finally:
            self.channel = None
            self.connection = None
            self.callback_queue = None

    def on_response(self, ch, method, props, body):
        if self.corr_id == props.correlation_id:
            print(f" [.] Получен ответ для {self.corr_id}")
            try:
                self.response = json.loads(body)
            except json.JSONDecodeError:
                print(f" [!] Ошибка декодирования JSON ответа: {body}")
                self.response = {"error": "Invalid JSON response from LLM worker",
                                 "raw": body.decode('utf-8', errors='ignore')}
        else:
            print(
                f" [!] Получен ответ с неверным correlation_id: ожидался {self.corr_id}, получен {props.correlation_id}")

    def call(self, user_message, user_id="default_user", timeout_sec=DEFAULT_TIMEOUT):
        if not self.connection or self.connection.is_closed or \
                not self.channel or self.channel.is_closed:
            print(" [!] Соединение/канал потеряны перед отправкой, пытаемся переподключиться...")
            if not self._connect():
                print(" [!] Не удалось переподключиться перед отправкой.")
                return {"error": "Failed to connect to message queue"}

        self.response = None
        self.corr_id = str(uuid.uuid4())

        request_body = json.dumps({
            'user_id': user_id,
            'message': user_message
        })

        print(f" [x] Отправка запроса '{user_message[:30]}...' (ID: {self.corr_id})")
        try:
            self.channel.basic_publish(
                exchange='',
                routing_key=TASK_QUEUE_NAME,
                properties=pika.BasicProperties(
                    reply_to=self.callback_queue,
                    correlation_id=self.corr_id,
                    content_type='application/json',
                ),
                body=request_body
            )
        except (StreamLostError, AMQPConnectionError, ConnectionResetError, socket.error, ChannelError) as e:
            print(f" [!] Ошибка отправки сообщения (потеря соединения/канала): {e}")
            print(" [!] Попытка переподключения...")
            if self._connect():
                print(" [!] Переподключение успешно. Повторная попытка отправки...")
                try:
                    self.channel.basic_publish(
                        exchange='',
                        routing_key=TASK_QUEUE_NAME,
                        properties=pika.BasicProperties(
                            reply_to=self.callback_queue,
                            correlation_id=self.corr_id,
                            content_type='application/json',
                        ),
                        body=request_body
                    )
                    print(" [x] Повторная отправка успешна.")
                except Exception as e_retry:
                    print(f" [!] Ошибка при повторной отправке сообщения: {e_retry}")
                    self._safe_close()
                    return {"error": f"Failed to publish message after reconnection attempt: {e_retry}"}
            else:
                print(" [!] Не удалось переподключиться после ошибки отправки.")
                return {"error": f"Failed to publish message, connection lost and reconnection failed: {e}"}
        except Exception as e:
            print(f" [!] Неожиданная ошибка отправки сообщения: {e}")
            self._safe_close()
            return {"error": f"Unexpected error during message publishing: {e}"}

        print(" [.] Ожидание ответа...")
        start_time = time.time()
        while self.response is None:
            try:
                self.connection.process_data_events(time_limit=1)
            except (StreamLostError, AMQPConnectionError, ConnectionResetError, socket.error, ChannelError) as e:
                print(f" [!] Ошибка во время ожидания ответа (потеря соединения/канала): {e}")
                print(" [!] Попытка переподключения во время ожидания...")
                self._safe_close()
                if not self._connect():
                    print(" [!] Не удалось переподключиться во время ожидания.")
                    return {"error": f"Connection lost while waiting for response, reconnection failed: {e}"}
                print(
                    " [!] Переподключение успешно, но ответ на исходный запрос ({self.corr_id}) скорее всего потерян.")
                return {
                    "error": f"Connection lost while waiting for response. Reconnected, but original request {self.corr_id} likely lost."}
            except Exception as e:
                print(f" [!] Неожиданная ошибка во время ожидания ответа: {e}")
                self._safe_close()
                return {"error": f"Unexpected error while waiting for response: {e}"}

            if time.time() - start_time > timeout_sec:
                print(f" [!] Таймаут ожидания ответа для {self.corr_id}")
                return {"error": f"Timeout waiting for LLM response after {timeout_sec} seconds"}

        return self.response

    def close(self):
        print(" [*] Закрытие соединения с RabbitMQ по запросу.")
        self._safe_close()


if __name__ == "__main__":
    client = LlmRpcClient()
    response = client.call('Привет, как дела?')
    print(f" [.] Ответ от LLM: {response['llm_response']}")
