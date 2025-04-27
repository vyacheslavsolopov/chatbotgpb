#!/usr/bin/env python
import json

import pika, sys, os
import requests
import logging

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)


def callback(ch, method, properties, body):
    print(f" [x] Received {body.decode('utf-8')}")


def take_from_queue():
    try:
        connection = pika.BlockingConnection(
            pika.ConnectionParameters(host='195.161.62.198',
                                      port=5672,
                                      virtual_host='/',
                                      credentials=pika.PlainCredentials('guest', 'guest'),
                                      connection_attempts=5,
                                      retry_delay=5,
                                      socket_timeout=10
                                      ))
        channel = connection.channel()
        print('соединение установлено')

        channel.queue_declare(queue='to_user')
        print('ожидание сообщений')
        channel.basic_consume(queue='to_user', on_message_callback=callback, auto_ack=True)
        channel.start_consuming()
    except pika.exceptions.AMQPConnectionError as e:
        print(f"Ошибка подключения: {e}")
    except KeyboardInterrupt:
        print("\n Работа завершена")
        connection.close()
    except Exception as e:
        print(f"Ошибка: {e}")
        if 'connection' in locals():
            connection.close()


if __name__ == '__main__':
    take_from_queue()
