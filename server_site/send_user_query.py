import json
import time
import uuid

import pika

RABBITMQ_HOST = '195.161.62.198'
TASK_QUEUE_NAME = 'llm_task_queue'


class LlmRpcClient:
    def __init__(self):
        self.connection = None
        self.channel = None
        self.callback_queue = None
        self.response = None
        self.corr_id = None
        self._connect()

    def _connect(self):
        try:
            self.connection = pika.BlockingConnection(
                pika.ConnectionParameters(host=RABBITMQ_HOST)
            )
            self.channel = self.connection.channel()

            result = self.channel.queue_declare(queue='', exclusive=True)
            self.callback_queue = result.method.queue
            print(f" [*] Callback queue created: {self.callback_queue}")

            self.channel.basic_consume(
                queue=self.callback_queue,
                on_message_callback=self.on_response,
                auto_ack=True
            )
        except pika.exceptions.AMQPConnectionError as e:
            print(f" [!] Не удалось подключиться к RabbitMQ: {e}")

    def on_response(self, ch, method, props, body):
        if self.corr_id == props.correlation_id:
            print(f" [.] Получен ответ для {self.corr_id}")
            try:
                self.response = json.loads(body)
            except json.JSONDecodeError:
                self.response = {"error": "Invalid JSON response from LLM worker", "raw": body.decode()}
        else:
            print(
                f" [!] Получен ответ с неверным correlation_id: ожидался {self.corr_id}, получен {props.correlation_id}")

    def call(self, user_message, user_id="default_user", timeout_sec=30):
        if not self.connection or self.connection.is_closed:
            print(" [!] Соединение потеряно, пытаемся переподключиться...")
            try:
                self._connect()
            except Exception as e:
                print(f" [!] Не удалось переподключиться: {e}")
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
        except Exception as e:
            print(f" [!] Ошибка отправки сообщения: {e}")
            return {"error": "Failed to publish message"}

        print(" [.] Ожидание ответа...")
        start_time = time.time()
        while self.response is None:
            self.connection.process_data_events(time_limit=1)
            if time.time() - start_time > timeout_sec:
                print(f" [!] Таймаут ожидания ответа для {self.corr_id}")
                return {"error": f"Timeout waiting for LLM response after {timeout_sec} seconds"}

        return self.response

    def close(self):
        if self.connection and self.connection.is_open:
            print(" [*] Закрытие соединения с RabbitMQ")
            self.connection.close()


if __name__ == "__main__":
    client = LlmRpcClient()
    response = client.call('Привет, как дела?')
    print(f" [.] Ответ от LLM: {response['llm_response']}")
