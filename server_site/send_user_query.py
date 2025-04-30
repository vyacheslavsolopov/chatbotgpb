import json
import socket
import time
import uuid
import pika
from pika.exceptions import AMQPConnectionError, StreamLostError, ChannelError
from collections import deque  # Needed for buffering chunks

RABBITMQ_HOST = '195.161.62.198'
TASK_QUEUE_NAME = 'llm_task_queue'
DEFAULT_TIMEOUT = 60  # Increased timeout for potentially long streams
HEARTBEAT_INTERVAL = 60

# Constants for message types (shared between client and worker)
MSG_TYPE_CHUNK = "chunk"
MSG_TYPE_END = "end"
MSG_TYPE_ERROR = "error"  # Optional: For worker-side errors during streaming

# Special marker for the buffer to indicate end or error
STREAM_END_MARKER = object()
STREAM_ERROR_MARKER = object()


class LlmRpcClient:
    def __init__(self):
        self.connection = None
        self.channel = None
        self.callback_queue = None
        self._response_holder = {}  # Holds single responses for call()
        self._stream_buffers = {}  # Holds deque buffers for stream() keyed by corr_id
        self._connect()

    def _connect(self):
        self._safe_close()
        print(" [*] Попытка подключения к RabbitMQ...")
        try:
            # Add socket_timeout to handle potential network hangs during connection
            self.connection = pika.BlockingConnection(
                pika.ConnectionParameters(
                    host=RABBITMQ_HOST,
                    heartbeat=HEARTBEAT_INTERVAL,
                    blocked_connection_timeout=30,  # Timeout for connection blocks
                    # socket_timeout=10 # Timeout for socket operations
                )
            )
            self.channel = self.connection.channel()

            # Increase consumer timeout (experimental, might not be needed with explicit checks)
            # self.channel.set_consumer_timeout(DEFAULT_TIMEOUT + 10) # Doesn't exist in pika

            result = self.channel.queue_declare(queue='', exclusive=True, auto_delete=True)
            self.callback_queue = result.method.queue
            print(f" [*] Callback queue создана: {self.callback_queue}")

            self.channel.basic_consume(
                queue=self.callback_queue,
                on_message_callback=self.on_response,
                auto_ack=True  # Keep auto_ack for simplicity, but manual ack is safer
            )
            print(" [*] Подключение и настройка завершены.")
            return True

        except AMQPConnectionError as e:
            print(f" [!] Не удалось подключиться к RabbitMQ: {e}")
            self._safe_close()
            return False
        except socket.timeout:
            print(f" [!] Таймаут сокета при подключении к RabbitMQ.")
            self._safe_close()
            return False
        except Exception as e:
            print(f" [!] Неожиданная ошибка при подключении: {e}")
            self._safe_close()
            return False

    def _safe_close(self):
        """Безопасно закрывает канал и соединение."""
        # Clear buffers on close
        self._response_holder.clear()
        # Signal any waiting streams that connection is lost? Difficult in sync code.
        self._stream_buffers.clear()
        try:
            if self.channel and self.channel.is_open:
                print(" [*] Закрытие канала...")
                self.channel.close()
                print(" [*] Канал закрыт.")
        except Exception as e:
            print(f" [!] Ошибка при закрытии канала: {e}")
        try:
            if self.connection and self.connection.is_open:
                print(" [*] Закрытие соединения...")
                self.connection.close()
                print(" [*] Соединение закрыто.")
        except Exception as e:
            print(f" [!] Ошибка при закрытии соединения: {e}")
        finally:
            self.channel = None
            self.connection = None
            self.callback_queue = None

    def on_response(self, ch, method, props, body):
        """Handles incoming messages for both call() and stream()."""
        corr_id = props.correlation_id
        if not corr_id:
            print(f" [!] Получен ответ без correlation_id. Игнорируется. Body: {body[:100]}")
            return

        # Check if this correlation ID is waiting for a stream
        if corr_id in self._stream_buffers:
            buffer = self._stream_buffers[corr_id]
            try:
                message_data = json.loads(body)
                msg_type = message_data.get("type")
                content = message_data.get("content")

                if msg_type == MSG_TYPE_CHUNK:
                    # print(f" [.] Stream chunk received for {corr_id}") # Debug
                    if content is not None:
                        buffer.append(content)
                    else:
                        print(f" [!] Stream chunk for {corr_id} has null content.")
                elif msg_type == MSG_TYPE_END:
                    print(f" [.] Stream end marker received for {corr_id}")
                    buffer.append(STREAM_END_MARKER)
                elif msg_type == MSG_TYPE_ERROR:
                    print(f" [!] Stream error message received for {corr_id}: {content}")
                    buffer.append(STREAM_ERROR_MARKER)
                    buffer.append(content)  # Append error content after marker
                else:
                    print(f" [!] Неизвестный тип сообщения в потоке для {corr_id}: {msg_type}. Body: {body[:100]}")
                    # Treat as error? Or ignore? Let's signal error.
                    buffer.append(STREAM_ERROR_MARKER)
                    buffer.append(f"Unknown stream message type: {msg_type}")

            except json.JSONDecodeError:
                print(f" [!] Ошибка декодирования JSON в потоковом ответе для {corr_id}: {body[:100]}")
                buffer = self._stream_buffers.get(corr_id)
                if buffer is not None:
                    buffer.append(STREAM_ERROR_MARKER)
                    buffer.append(f"Invalid JSON received in stream: {body.decode('utf-8', errors='ignore')}")
            except Exception as e:
                print(f" [!] Неожиданная ошибка в on_response (stream) для {corr_id}: {e}")
                buffer = self._stream_buffers.get(corr_id)
                if buffer is not None:
                    buffer.append(STREAM_ERROR_MARKER)
                    buffer.append(f"Client-side error processing stream message: {e}")

        # Check if this correlation ID is waiting for a single response (call)
        elif corr_id in self._response_holder:
            print(f" [.] Получен ответ (call) для {corr_id}")
            try:
                self._response_holder[corr_id] = json.loads(body)
            except json.JSONDecodeError:
                print(f" [!] Ошибка декодирования JSON ответа (call) для {corr_id}: {body[:100]}")
                self._response_holder[corr_id] = {"error": "Invalid JSON response from LLM worker",
                                                  "raw": body.decode('utf-8', errors='ignore')}
            except Exception as e:
                print(f" [!] Неожиданная ошибка в on_response (call) для {corr_id}: {e}")
                self._response_holder[corr_id] = {"error": f"Client-side error processing call response: {e}"}
        else:
            # This can happen if response arrives after timeout or client reset
            print(f" [!] Получен ответ для неизвестного или устаревшего correlation_id: {corr_id}. Игнорируется.")

    def _publish_message(self, corr_id, request_body):
        """Internal helper to publish message with reconnection logic."""
        try:
            self.channel.basic_publish(
                exchange='',
                routing_key=TASK_QUEUE_NAME,
                properties=pika.BasicProperties(
                    reply_to=self.callback_queue,
                    correlation_id=corr_id,
                    content_type='application/json',
                    # delivery_mode=2, # Make message persistent (optional)
                ),
                body=request_body
            )
            return True
        except (
                StreamLostError, AMQPConnectionError, ConnectionResetError, socket.error, ChannelError,
                AttributeError) as e:
            print(f" [!] Ошибка отправки сообщения (потеря соединения/канала): {e}")
            print(" [!] Попытка переподключения...")
            if self._connect():
                print(" [!] Переподключение успешно. Повторная попытка отправки...")
                try:
                    # Need to ensure channel is re-established correctly after _connect
                    if not self.channel or not self.channel.is_open:
                        print("[!] Канал не доступен после переподключения.")
                        return False
                    self.channel.basic_publish(
                        exchange='',
                        routing_key=TASK_QUEUE_NAME,
                        properties=pika.BasicProperties(
                            reply_to=self.callback_queue,
                            correlation_id=corr_id,
                            content_type='application/json',
                        ),
                        body=request_body
                    )
                    print(" [x] Повторная отправка успешна.")
                    return True
                except Exception as e_retry:
                    print(f" [!] Ошибка при повторной отправке сообщения: {e_retry}")
                    self._safe_close()
                    return False
            else:
                print(" [!] Не удалось переподключиться после ошибки отправки.")
                return False
        except Exception as e:
            print(f" [!] Неожиданная ошибка отправки сообщения: {e}")
            self._safe_close()  # Close connection on unexpected errors
            return False

    # --- Original call method (slightly modified response handling) ---
    def call(self, user_message, user_id="default_user", timeout_sec=DEFAULT_TIMEOUT):
        if not self.connection or self.connection.is_closed or \
                not self.channel or self.channel.is_closed:
            print(" [!] Соединение/канал потеряны перед отправкой (call), пытаемся переподключиться...")
            if not self._connect():
                print(" [!] Не удалось переподключиться перед отправкой (call).")
                return {"error": "Failed to connect to message queue"}

        corr_id = str(uuid.uuid4())
        # Put a placeholder to indicate we are waiting for this ID
        self._response_holder[corr_id] = None

        request_body = json.dumps({
            'user_id': user_id,
            'message': user_message,
            # 'stream': False # Explicitly indicate non-streaming if needed by worker
        })

        print(f" [x] Отправка запроса (call) '{user_message[:30]}...' (ID: {corr_id})")
        if not self._publish_message(corr_id, request_body):
            del self._response_holder[corr_id]  # Clean up placeholder
            return {"error": "Failed to publish message (call)"}

        print(f" [.] Ожидание ответа (call) для {corr_id}...")
        start_time = time.time()
        response = None
        while response is None:
            try:
                # Process events for a short time, then check buffer/timeout
                self.connection.process_data_events(time_limit=0.1)  # Non-blocking check
                response = self._response_holder.get(corr_id)  # Check if response arrived

                if response is not None:  # Response received
                    break

                if time.time() - start_time > timeout_sec:
                    print(f" [!] Таймаут ожидания ответа (call) для {corr_id}")
                    response = {"error": f"Timeout waiting for LLM response (call) after {timeout_sec} seconds"}
                    break

                # Add a small sleep to prevent busy-waiting if process_data_events returns immediately
                # time.sleep(0.05)

            except (StreamLostError, AMQPConnectionError, ConnectionResetError, socket.error, ChannelError) as e:
                print(f" [!] Ошибка во время ожидания ответа (call) (потеря соединения/канала): {e}")
                response = {"error": f"Connection lost while waiting for call response: {e}. Please retry."}
                self._safe_close()  # Attempt to close cleanly
                break  # Exit loop on connection error
            except Exception as e:
                print(f" [!] Неожиданная ошибка во время ожидания ответа (call): {e}")
                response = {"error": f"Unexpected error while waiting for call response: {e}"}
                self._safe_close()
                break  # Exit loop on unexpected error

        # Clean up the holder for this correlation ID
        if corr_id in self._response_holder:
            del self._response_holder[corr_id]

        # Attempt reconnection if connection was lost during wait
        if "Connection lost" in response.get("error", "") and (not self.connection or self.connection.is_closed):
            print(" [!] Попытка переподключения после потери соединения во время ожидания (call)...")
            self._connect()  # Try to reconnect for future calls

        return response

    # --- New stream method ---
    def stream(self, user_message, user_id="default_user", timeout_sec=DEFAULT_TIMEOUT):
        """
        Sends a request and yields response chunks as they arrive.

        Yields:
            str: Chunks of the response content.

        Raises:
            TimeoutError: If the stream times out waiting for the next chunk or end.
            ConnectionError: If the connection fails during streaming.
            RuntimeError: If the worker sends an error message or invalid data.
        """
        if not self.connection or self.connection.is_closed or \
                not self.channel or self.channel.is_closed:
            print(" [!] Соединение/канал потеряны перед отправкой (stream), пытаемся переподключиться...")
            if not self._connect():
                print(" [!] Не удалось переподключиться перед отправкой (stream).")
                raise ConnectionError("Failed to connect to message queue before streaming")

        corr_id = str(uuid.uuid4())

        self._stream_buffers[corr_id] = deque()

        request_body = json.dumps({
            'user_id': user_id,
            'message': user_message,
            'stream': True
        })

        print(f" [x] Отправка запроса (stream) '{user_message[:30]}...' (ID: {corr_id})")
        if not self._publish_message(corr_id, request_body):
            del self._stream_buffers[corr_id]  # Clean up buffer
            raise ConnectionError("Failed to publish stream message")

        print(f" [.] Ожидание потока (stream) для {corr_id}...")
        start_time = time.time()
        last_data_time = start_time
        stream_ended = False

        try:
            while not stream_ended:
                buffer = self._stream_buffers.get(corr_id)
                if buffer is None:
                    # This shouldn't happen if publish was successful, but safety check
                    raise RuntimeError(f"Stream buffer for {corr_id} disappeared")

                # Process buffered messages first
                while buffer:
                    item = buffer.popleft()
                    last_data_time = time.time()  # Reset timeout timer on receiving data

                    if item is STREAM_END_MARKER:
                        print(f" [.] Stream {corr_id} ended normally.")
                        stream_ended = True
                        break  # Exit inner loop (processing buffer)
                    elif item is STREAM_ERROR_MARKER:
                        error_content = "Unknown stream error"
                        if buffer:  # Error content should be next item
                            error_content = buffer.popleft()
                        print(f" [!] Stream {corr_id} ended with error: {error_content}")
                        stream_ended = True
                        raise RuntimeError(f"Stream error from worker or processing: {error_content}")
                    else:
                        # print(f" [.] Yielding chunk for {corr_id}") # Debug
                        yield item  # Yield the actual content chunk

                if stream_ended:
                    break  # Exit outer loop (waiting for events)

                # If buffer is empty, wait for new messages
                try:
                    # Process events for a short time, then check buffer/timeout
                    # Use a small time_limit to be responsive
                    self.connection.process_data_events(time_limit=0.1)
                except (StreamLostError, AMQPConnectionError, ConnectionResetError, socket.error, ChannelError) as e:
                    print(f" [!] Ошибка во время ожидания потока (stream) (потеря соединения/канала): {e}")
                    self._safe_close()  # Attempt to close cleanly
                    raise ConnectionError(f"Connection lost while waiting for stream data: {e}") from e
                except Exception as e:
                    print(f" [!] Неожиданная ошибка во время ожидания потока (stream): {e}")
                    self._safe_close()
                    raise RuntimeError(f"Unexpected error while waiting for stream data: {e}") from e

                # Check for overall timeout or inactivity timeout
                current_time = time.time()
                # Timeout if overall time exceeds limit OR if no data received for timeout_sec period
                if current_time - start_time > timeout_sec * 1.5:  # Overall longer timeout for stream
                    print(f" [!] Общий таймаут потока (stream) для {corr_id}")
                    raise TimeoutError(f"Stream timed out after {timeout_sec * 1.5} seconds (overall)")
                if current_time - last_data_time > timeout_sec:
                    print(f" [!] Таймаут неактивности потока (stream) для {corr_id}")
                    raise TimeoutError(f"Stream timed out due to inactivity after {timeout_sec} seconds")

                # Optional: small sleep if process_data_events is non-blocking and buffer was empty
                # time.sleep(0.01)

        finally:
            # Clean up the buffer for this correlation ID when generator exits
            # (either normally or due to exception)
            if corr_id in self._stream_buffers:
                print(f" [.] Очистка буфера для потока {corr_id}")
                del self._stream_buffers[corr_id]
            # Try to reconnect if connection was lost
            if not self.connection or self.connection.is_closed:
                print(" [!] Попытка переподключения после завершения/ошибки потока...")
                self._connect()

    def close(self):
        print(" [*] Закрытие соединения с RabbitMQ по запросу.")
        self._safe_close()


# Example Usage (requires a worker that supports streaming)
if __name__ == "__main__":
    client = LlmRpcClient()

    prompt = "Напиши список из 5 фруктов, каждый на новой строке."
    full_stream_response = ""
    for i, chunk in enumerate(client.stream(prompt, timeout_sec=20)):
        print(f" [.] Получен чанк {i}: '{chunk.strip()}'")
        full_stream_response = chunk
    print("\n [.] Полный собранный ответ (stream):")
    print(full_stream_response)

    client.close()
