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


def wait_for_one_message():
    received_message_body = None
    connection = None

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

        channel.queue_declare(queue='to_user_fixed', durable=True)

        callback_data = {'body': None, 'channel': channel}

        def on_message_callback(ch, method, properties, body):
            nonlocal callback_data
            callback_data['body'] = body
            ch.basic_ack(delivery_tag=method.delivery_tag)
            ch.stop_consuming()

        channel.basic_consume(queue='to_user_fixed',
                              on_message_callback=on_message_callback,
                              auto_ack=False)

        channel.start_consuming()
        received_message_body = callback_data['body'].decode('utf-8')


    except pika.exceptions.AMQPConnectionError as e:
        print(f"Ошибка подключения к RabbitMQ: {e}")
        received_message_body = None  # Убедимся, что возвращаем None при ошибке
    except KeyboardInterrupt:
        print("Прервано пользователем (CTRL+C).")
        if connection and connection.is_open and channel and channel.is_open:
            channel.stop_consuming()  # Попробовать остановить, если еще работает
        received_message_body = None
    except Exception as e:
        print(f"Произошла непредвиденная ошибка: {e}")
        received_message_body = None
    finally:
        # Всегда закрываем соединение, если оно было открыто
        if connection and connection.is_open:
            print(" [*] Закрытие соединения.")
            connection.close()

    return received_message_body


if __name__ == '__main__':
    # print(send_to_queue('Привет! Как дела у Саши?'))
    print(wait_for_one_message())
