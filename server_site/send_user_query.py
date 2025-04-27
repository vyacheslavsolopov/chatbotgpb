import pika


def send_to_queue(user_query):
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

        channel.queue_declare(queue='from_user')

        channel.basic_publish(exchange='', routing_key='from_user', body=user_query)
        print("сообщение добавлено в очередь")
        connection.close()
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
    print(send_to_queue('Привет! Как дела у Саши?'))
