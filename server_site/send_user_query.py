import pika


def send_to_queue(user_query):
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
    channel.queue_declare(queue='from_user_fixed', durable=True)
    channel.basic_publish(exchange='', routing_key='from_user_fixed', body=user_query)
    print('Message sent to queue')
    connection.close()


def wait_for_one_message():
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

    return received_message_body


if __name__ == '__main__':
    # print(send_to_queue('Привет! Как дела у Саши?'))
    print(wait_for_one_message())
