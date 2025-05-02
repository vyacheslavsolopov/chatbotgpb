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
VLLM_MODEL_NAME = "t-tech/T-lite-it-1.0"

MAX_TOKENS_TO_GENERATE = 1024

RABBITMQ_HOST = '195.161.62.198'
TASK_QUEUE_NAME = 'llm_task_queue'

MSG_TYPE_CHUNK = "chunk"
MSG_TYPE_END = "end"
MSG_TYPE_ERROR = "error"


def query_llm_single(prompt_text: str) -> str | None:
    """Sends a prompt to the local LLM (OpenAI format) and returns the complete response."""
    headers = {"Content-Type": "application/json"}
    formatted_prompt = f"<|im_start|>user\n{prompt_text}\n<|im_end|>\n<|im_start|>assistant\n"

    data = {
        "prompt": formatted_prompt,
        "max_tokens": MAX_TOKENS_TO_GENERATE,
    }

    logger.info(f"Sending single request to LLM (OpenAI format): {prompt_text[:100]}...")
    try:
        response = requests.post(LLM_API_URL, headers=headers, json=data, timeout=120)
        response.raise_for_status()
        result = response.json()
        logger.debug(f"LLM Raw Single Response (OpenAI format): {result}")

        llm_response = result['text'][0].split("assistant")[1]

        return llm_response.strip()

    except Exception as e:
        logger.error(f"Unexpected error during single LLM query: {e}", exc_info=True)
        return "Unexpected error during LLM query."


def stream_llm_response(ch, method, props, prompt_text: str):
    """
    Sends a prompt to the LLM (OpenAI format) requesting a stream and sends chunks back
    via RabbitMQ.
    """
    correlation_id = props.correlation_id
    reply_to_queue = props.reply_to
    delivery_tag = method.delivery_tag

    if not reply_to_queue:
        logger.warning(f"No reply_to queue for stream request {correlation_id}. Aborting.")
        try:
            ch.basic_ack(delivery_tag=delivery_tag)
            logger.info(f"Message {correlation_id} acknowledged (no reply queue).")
        except Exception as ack_e:
            logger.error(f"Failed to ACK message {correlation_id} (no reply queue): {ack_e}")
        return

    formatted_prompt = f"<|im_start|>user\n{prompt_text}\n<|im_end|>\n<|im_start|>assistant\n"

    headers = {"Content-Type": "application/json"}
    data = {
        "prompt": formatted_prompt,
        "max_tokens": MAX_TOKENS_TO_GENERATE,
        "stream": True
    }

    logger.info(f"Sending stream request to LLM (OpenAI format) (ID: {correlation_id}): {prompt_text[:100]}...")

    success = True
    stream_client = None
    try:
        response = requests.post(LLM_API_URL, headers=headers, json=data, stream=True, timeout=180)

        event_count = 0
        for chunk in response.iter_lines():
            event_count += 1
            logger.debug(f"--- Stream Event {event_count} for {correlation_id} ---")
            logger.debug(f"Event Data Raw: {chunk[:500]}")

            json_resp = json.loads(chunk)
            llm_response = json_resp['text'][0].split("assistant")[1].strip()

            if chunk.strip() == '[DONE]':
                logger.info(f"Received [DONE] marker for stream {correlation_id}")
                break

            if not chunk:
                logger.debug("Skipping empty event data.")
                continue

            try:
                payload = json.dumps({"type": MSG_TYPE_CHUNK, "content": llm_response})

                ch.basic_publish(
                    exchange='',
                    routing_key=reply_to_queue,
                    properties=pika.BasicProperties(correlation_id=correlation_id,
                                                    content_type='application/json'),
                    body=payload
                )
                logger.debug(f"Published chunk for {correlation_id}")

            except json.JSONDecodeError:
                logger.warning(f"Non-JSON data in stream for {correlation_id}: {chunk[:100]}")
                continue
            except Exception as e:
                logger.error(f"Error processing stream chunk JSON for {correlation_id}: {e} - Data: {chunk[:100]}",
                             exc_info=True)
                continue

    except requests.exceptions.Timeout:
        logger.error(f"Timeout connecting to LLM API (stream) for {correlation_id}: {LLM_API_URL}")
        payload = json.dumps({"type": MSG_TYPE_ERROR, "content": "Timeout connecting to LLM."})
        try:
            ch.basic_publish(exchange='', routing_key=reply_to_queue,
                             properties=pika.BasicProperties(correlation_id=correlation_id), body=payload)
        except Exception as pub_e:
            logger.error(f"Failed to publish timeout error for {correlation_id}: {pub_e}")
        success = False
    except requests.exceptions.RequestException as e:
        logger.error(f"Error connecting to LLM API (stream) for {correlation_id}: {e}")
        payload = json.dumps({"type": MSG_TYPE_ERROR, "content": f"Error connecting to LLM: {e}"})
        try:
            ch.basic_publish(exchange='', routing_key=reply_to_queue,
                             properties=pika.BasicProperties(correlation_id=correlation_id), body=payload)
        except Exception as pub_e:
            logger.error(f"Failed to publish connection error for {correlation_id}: {pub_e}")
        success = False
    except Exception as e:
        logger.error(f"Unexpected error during LLM stream for {correlation_id}: {e}", exc_info=True)
        payload = json.dumps({"type": MSG_TYPE_ERROR, "content": f"Worker error during stream: {e}"})
        try:
            ch.basic_publish(exchange='', routing_key=reply_to_queue,
                             properties=pika.BasicProperties(correlation_id=correlation_id), body=payload)
        except Exception as pub_e:
            logger.error(f"Failed to publish unexpected error for {correlation_id}: {pub_e}")
        success = False
    finally:
        if stream_client:
            try:
                stream_client.close()
                logger.debug(f"SSEClient connection closed for {correlation_id}")
            except Exception as close_e:
                logger.warning(f"Error closing SSEClient connection for {correlation_id}: {close_e}")

        if success:
            try:
                logger.info(f"Sending END marker for stream {correlation_id}")
                payload = json.dumps({"type": MSG_TYPE_END})
                ch.basic_publish(
                    exchange='',
                    routing_key=reply_to_queue,
                    properties=pika.BasicProperties(correlation_id=correlation_id, content_type='application/json'),
                    body=payload
                )
            except Exception as e:
                logger.error(f"Failed to send END marker for {correlation_id} to {reply_to_queue}: {e}")
        else:
            logger.warning(
                f"Stream {correlation_id} finished with errors or did not start properly, not sending END marker.")

        try:
            ch.basic_ack(delivery_tag=delivery_tag)
            logger.info(f"Original stream message {correlation_id} acknowledged.")
        except Exception as e:
            logger.error(f"Failed to ACK stream message {correlation_id}: {e}")


def on_request(ch, method, props, body):
    """ Callback function called when receiving a message from TASK_QUEUE_NAME """
    correlation_id = props.correlation_id
    reply_to_queue = props.reply_to

    print(
        f"\n [x] Received request (ID: {correlation_id}) from '{reply_to_queue if reply_to_queue else 'No Reply Queue!'}'.")

    try:
        data = json.loads(body)
        user_message = data.get('message', '')
        is_stream_request = data.get('stream', False)

        if is_stream_request:
            logger.info(f"Processing STREAM request {correlation_id}...")
            stream_llm_response(ch, method, props, user_message)
        else:
            logger.info(f"Processing SINGLE request {correlation_id}...")
            response_text = query_llm_single(user_message)
            response_payload_data = {"llm_response": response_text}
            response_payload = json.dumps(response_payload_data)

            if not reply_to_queue:
                logger.warning(f"No reply_to queue for single request {correlation_id}. Result not sent.")
            else:
                try:
                    ch.basic_publish(
                        exchange='',
                        routing_key=reply_to_queue,
                        properties=pika.BasicProperties(correlation_id=correlation_id, content_type='application/json'),
                        body=response_payload
                    )
                    logger.info(f"Single response for {correlation_id} sent to {reply_to_queue}")
                except Exception as e:
                    logger.error(f"Error sending single response for {correlation_id} to {reply_to_queue}: {e}")

            try:
                ch.basic_ack(delivery_tag=method.delivery_tag)
                logger.info(f"Single message {correlation_id} acknowledged.")
            except Exception as e:
                logger.error(f"Failed to ACK single message {correlation_id}: {e}")

    except json.JSONDecodeError:
        logger.error(f" [!] Error decoding incoming JSON request for {correlation_id}")
        if reply_to_queue and correlation_id:
            error_payload = json.dumps({"type": MSG_TYPE_ERROR, "content": "Invalid JSON format received by worker"})
            try:
                ch.basic_publish(exchange='', routing_key=reply_to_queue,
                                 properties=pika.BasicProperties(correlation_id=correlation_id), body=error_payload)
            except Exception as pub_e:
                logger.error(f"Failed to send JSON decode error reply for {correlation_id}: {pub_e}")
        try:
            ch.basic_ack(delivery_tag=method.delivery_tag)
            logger.info(f"Invalid JSON message {correlation_id} acknowledged.")
        except Exception as ack_e:
            logger.error(f"Failed to ACK invalid JSON message {correlation_id}: {ack_e}")

    except Exception as e:
        logger.error(f" [!] Error during request processing callback for {correlation_id}: {e}", exc_info=True)
        if reply_to_queue and correlation_id:
            error_payload = json.dumps({"type": MSG_TYPE_ERROR, "content": f"LLM worker processing error: {e}"})
            try:
                ch.basic_publish(exchange='', routing_key=reply_to_queue,
                                 properties=pika.BasicProperties(correlation_id=correlation_id), body=error_payload)
            except Exception as pub_e:
                logger.error(f"Failed to send processing error reply for {correlation_id}: {pub_e}")
        try:
            ch.basic_ack(delivery_tag=method.delivery_tag)
            logger.info(f"Message {correlation_id} acknowledged after processing error.")
        except Exception as ack_e:
            logger.error(f"Failed to ACK message after processing error {correlation_id}: {ack_e}")


def start_worker():
    """Connects to RabbitMQ and starts consuming messages."""
    connection = None
    while True:
        try:
            logger.info(f"Connecting to RabbitMQ at {RABBITMQ_HOST}...")
            connection = pika.BlockingConnection(
                pika.ConnectionParameters(
                    host=RABBITMQ_HOST,
                    heartbeat=60,
                    blocked_connection_timeout=300
                )
            )
            logger.info("Connection successful.")
            channel = connection.channel()

            channel.queue_declare(queue=TASK_QUEUE_NAME, durable=True)
            logger.info(f" [*] Queue '{TASK_QUEUE_NAME}' declared/ready.")

            channel.basic_qos(prefetch_count=1)

            channel.basic_consume(queue=TASK_QUEUE_NAME, on_message_callback=on_request, auto_ack=False)

            logger.info(" [*] Waiting for messages. To exit press CTRL+C")
            channel.start_consuming()

        except pika.exceptions.AMQPConnectionError as e:
            logger.error(f" [!] AMQP Connection Error: {e}. Retrying in 5 seconds...")
            if connection and connection.is_open:
                try:
                    connection.close()
                except:
                    pass
            connection = None  # Reset connection object
            time.sleep(5)
        except KeyboardInterrupt:
            logger.info(" [*] Keyboard interrupt received. Shutting down...")
            if connection and connection.is_open:
                try:
                    connection.close()
                except:
                    pass
            break
        except Exception as e:
            logger.error(f" [!] Unexpected error in main worker loop: {e}. Restarting in 10 seconds...", exc_info=True)
            if connection and connection.is_open:
                try:
                    connection.close()
                except:
                    pass
            connection = None
            time.sleep(10)


if __name__ == '__main__':
    start_worker()
