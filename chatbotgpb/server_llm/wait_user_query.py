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

LLM_API_URL = "http://127.0.0.1:8080/completion"
MAX_TOKENS_TO_GENERATE = 512


def query_llm(prompt_text: str) -> str | None:
    """Sends a prompt to the local LLM and returns the response."""

    # Format the prompt exactly as needed by your local LLM
    formatted_prompt = f"<|im_start|>user\n{prompt_text}\n<|im_end|>\n<|im_start|>assistant\n"

    headers = {"Content-Type": "application/json"}
    data = {
        "prompt": formatted_prompt,
        "n_predict": MAX_TOKENS_TO_GENERATE,
        # Add other parameters your LLM API might support, e.g.:
        # "temperature": 0.7,
        # "stop": ["<|im_end|>", "user:"]
    }

    logger.info(
        f"Sending prompt to LLM: {formatted_prompt[:100]}...")  # Log truncated prompt

    try:
        response = requests.post(LLM_API_URL, headers=headers, json=data,
                                 timeout=120)  # Increased timeout
        response.raise_for_status()  # Raise an exception for bad status codes (4xx or 5xx)

        result = response.json()
        logger.info(f"LLM Raw Response: {result}")

        # --- Adjust based on your ACTUAL LLM API response structure ---
        # Common structures:
        # 1. Directly in 'content':
        if 'content' in result:
            llm_response = result['content']
            # 2. Or maybe another key like 'text' or 'completion':
        # elif 'text' in result:
        #     llm_response = result['text']
        # 2. Or maybe another key like 'text' or 'completion':
        # elif 'text' in result:
        #     llm_response = result['text']
        # 3. Handle potential nested structures if necessary
        else:
            logger.error(f"LLM response key 'content' not found in: {result}")
            return "Error: Could not parse LLM response."
        # --- End adjustment section ---

        # Basic cleaning (optional) - remove leading/trailing whitespace
        return llm_response.strip()

    except requests.exceptions.RequestException as e:
        logger.error(f"Error connecting to LLM API: {e}")
        return f"Sorry, I couldn't connect to the language model: {e}"
    except json.JSONDecodeError as e:
        logger.error(f"Error decoding LLM JSON response: {e} - Response text: {response.text}")
        return "Sorry, I received an invalid response from the language model."
    except Exception as e:
        logger.error(f"An unexpected error occurred during LLM query: {e}")
        return "An unexpected error occurred while processing your request."


def send_to_queue(user_query):
    global connection
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

        channel.basic_publish(exchange='', routing_key='to_user', body=user_query)
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


def callback(ch, method, properties, body):
    try:
        llm_response = query_llm(body.decode('utf-8'))
        send_to_queue(llm_response)
    except Exception as e:
        logger.error(f"Error during LLM query: {e}")


def take_from_queue():
    global connection
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
        print('ожидание сообщений')
        channel.basic_consume(queue='from_user', on_message_callback=callback, auto_ack=True)
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
