import asyncio
import json
import logging
import uuid
from contextlib import suppress

import aio_pika
from aio_pika.exceptions import AMQPConnectionError, ChannelClosed, ConnectionClosed

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
log = logging.getLogger(__name__)

RABBITMQ_HOST = '195.161.62.198'
TASK_QUEUE_NAME = 'llm_task_queue'
DEFAULT_TIMEOUT = 60
HEARTBEAT_INTERVAL = 60

MSG_TYPE_CHUNK = "chunk"
MSG_TYPE_END = "end"
MSG_TYPE_ERROR = "error"

STREAM_END_MARKER = object()
STREAM_ERROR_MARKER = object()


class AsyncLlmRpcClient:
    def __init__(self, loop: asyncio.AbstractEventLoop = None):
        self.loop = asyncio.get_running_loop()
        self.connection = None
        self.channel = None
        self.callback_queue = None
        self._consumer_task = None
        self._response_futures = {}
        self._stream_queues = {}
        self._connection_lock = asyncio.Lock()

    async def _connect(self):
        """Establishes connection, channel, callback queue, and starts consumer."""
        async with self._connection_lock:
            if self.is_connected():
                log.info(" [*] Already connected.")
                return True

            log.info(" [*] Attempting to close existing resources before connecting...")
            await self._safe_close()

            log.info(" [*] Attempting to connect to RabbitMQ at %s...", RABBITMQ_HOST)
            try:
                self.connection = await aio_pika.connect_robust(
                    host=RABBITMQ_HOST,
                    heartbeat=HEARTBEAT_INTERVAL,
                    timeout=15,
                    loop=self.loop
                )
                self.connection.close_callbacks.add(self._handle_connection_close)
                self.connection.reconnect_callbacks.add(self._handle_reconnect)

                self.channel = await self.connection.channel()
                self.channel.close_callbacks.add(self._handle_channel_close)
                log.info(" [*] Channel opened.")

                self.callback_queue = await self.channel.declare_queue(
                    exclusive=True, auto_delete=True
                )
                log.info(f" [*] Callback queue declared: {self.callback_queue.name}")

                self._consumer_task = self.loop.create_task(self._consume_responses())
                log.info(" [*] Connection and setup successful. Consumer task started.")
                return True

            except (AMQPConnectionError, asyncio.TimeoutError, OSError) as e:
                log.error(f" [!] Failed to connect to RabbitMQ: {e}")
                await self._safe_close()
                return False
            except Exception as e:
                log.exception(f" [!] Unexpected error during connection: {e}")
                await self._safe_close()
                return False

    def _handle_connection_close(self, sender, exc=None):
        log.warning(f" [!] RabbitMQ connection closed. Sender: {sender}, Exception: {exc}")
        self.connection = None
        self.channel = None
        self.callback_queue = None
        if self._consumer_task and not self._consumer_task.done():
            self._consumer_task.cancel()
        self._fail_pending_requests("Connection closed")

    def _handle_channel_close(self, sender, exc=None):
        log.warning(f" [!] RabbitMQ channel closed. Sender: {sender}, Exception: {exc}")
        self.channel = None

    def _handle_reconnect(self, sender):
        log.info(f" [!] RabbitMQ connection re-established by connect_robust. Sender: {sender}")
        self.channel = None
        self.callback_queue = None
        if self._consumer_task and not self._consumer_task.done():
            self._consumer_task.cancel()
            self._consumer_task = None

    def _fail_pending_requests(self, reason):
        """Fail any pending futures or queues due to connection loss."""
        log.warning(f"Failing pending requests due to: {reason}")
        exception = ConnectionError(reason)
        for future in self._response_futures.values():
            if not future.done():
                future.set_exception(exception)
        for queue in self._stream_queues.values():
            try:
                queue.put_nowait(STREAM_ERROR_MARKER)
                queue.put_nowait(reason)
            except asyncio.QueueFull:
                log.error("Could not signal stream queue about connection error - queue full.")

        self._response_futures.clear()
        self._stream_queues.clear()

    async def _consume_responses(self):
        """Background task to consume messages from the callback queue."""
        if not self.callback_queue:
            log.error("[!] Consumer task started without a callback queue.")
            return

        log.info(f" [*] Consumer task starting for queue: {self.callback_queue.name}")
        try:
            async for message in self.callback_queue:
                async with message.process(ignore_processed=True):
                    corr_id = message.correlation_id
                    if not corr_id:
                        log.warning(f" [!] Received message without correlation_id. Body: {message.body[:100]}")
                        continue

                    if corr_id in self._stream_queues:
                        stream_queue = self._stream_queues[corr_id]
                        try:
                            data = json.loads(message.body)
                            msg_type = data.get("type")
                            content = data.get("content")

                            if msg_type == MSG_TYPE_CHUNK:
                                if content is not None:
                                    await stream_queue.put(content)
                                else:
                                    log.warning(f" [!] Stream chunk for {corr_id} has null content.")
                            elif msg_type == MSG_TYPE_END:
                                log.info(f" [.] Stream end marker received for {corr_id}")
                                await stream_queue.put(STREAM_END_MARKER)
                            elif msg_type == MSG_TYPE_ERROR:
                                log.error(f" [!] Stream error message received for {corr_id}: {content}")
                                await stream_queue.put(STREAM_ERROR_MARKER)
                                await stream_queue.put(content or "Unknown worker error")
                            else:
                                log.warning(f" [!] Unknown stream message type for {corr_id}: {msg_type}")
                                await stream_queue.put(STREAM_ERROR_MARKER)
                                await stream_queue.put(f"Unknown stream message type: {msg_type}")

                        except json.JSONDecodeError:
                            log.error(f" [!] JSON decode error for stream {corr_id}: {message.body[:100]}")
                            await stream_queue.put(STREAM_ERROR_MARKER)
                            await stream_queue.put(f"Invalid JSON in stream: {message.body.decode(errors='ignore')}")
                        except Exception as e:
                            log.exception(f" [!] Error processing stream message for {corr_id}: {e}")
                            with suppress(asyncio.QueueFull):
                                await stream_queue.put(STREAM_ERROR_MARKER)
                                await stream_queue.put(f"Client error processing stream: {e}")

                    elif corr_id in self._response_futures:
                        future = self._response_futures.get(corr_id)
                        if future and not future.done():
                            try:
                                response_data = json.loads(message.body)
                                future.set_result(response_data)
                            except json.JSONDecodeError:
                                log.error(f" [!] JSON decode error for call {corr_id}: {message.body[:100]}")
                                future.set_exception(
                                    ValueError(f"Invalid JSON response: {message.body.decode(errors='ignore')}"))
                            except Exception as e:
                                log.exception(f" [!] Error processing call response for {corr_id}: {e}")
                                future.set_exception(e)
                        else:
                            log.warning(f" [!] Received response for completed or unknown future {corr_id}.")

                    else:
                        log.warning(f" [!] Received response for unknown correlation_id: {corr_id}. Ignored.")

        except asyncio.CancelledError:
            log.info(" [*] Consumer task cancelled.")
        except (ChannelClosed, ConnectionClosed) as e:
            log.warning(f"[!] Consumer task stopped due to closed channel/connection: {e}")
        except Exception as e:
            log.exception(f" [!] Consumer task crashed: {e}")
        finally:
            log.info(" [*] Consumer task finished.")

    async def _ensure_connection(self):
        """Checks connection and attempts to connect if necessary."""
        if not self.is_connected():
            log.info("Connection not active. Attempting to connect...")
            if not await self._connect():
                raise ConnectionError("Failed to establish RabbitMQ connection.")
        elif not self.channel or self.channel.is_closed:
            log.warning("Channel is closed or None, attempting to recreate...")
            try:
                self.channel = await self.connection.channel()
                self.channel.add_close_callback(self._handle_channel_close)
                log.info("Channel recreated successfully.")

                if not self.callback_queue or self.callback_queue.name is None:
                    log.warning("Callback queue seems lost, re-declaring...")
                    self.callback_queue = await self.channel.declare_queue(exclusive=True, auto_delete=True)
                    if self._consumer_task and not self._consumer_task.done():
                        self._consumer_task.cancel()
                    self._consumer_task = self.loop.create_task(self._consume_responses())
                    log.info(f"Callback queue re-declared ({self.callback_queue.name}), consumer restarted.")

            except Exception as e:
                log.exception(f"Failed to recreate channel: {e}")
                await self._safe_close()
                raise ConnectionError(f"Failed to recreate RabbitMQ channel: {e}") from e

    def is_connected(self):
        """Check if connection and channel appear active."""
        return (
                self.connection is not None and not self.connection.is_closed and
                self.channel is not None and not self.channel.is_closed and
                self.callback_queue is not None and self.callback_queue.name is not None and
                self._consumer_task is not None and not self._consumer_task.done()
        )

    async def _publish_message(self, corr_id, request_body, reply_to_queue):
        """Publishes a message to the task queue."""
        await self._ensure_connection()

        message = aio_pika.Message(
            body=request_body.encode(),
            correlation_id=corr_id,
            reply_to=reply_to_queue,
            content_type='application/json',
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT
        )
        try:
            await self.channel.default_exchange.publish(
                message,
                routing_key=TASK_QUEUE_NAME
            )
            log.info(f" [x] Message published (CorrID: {corr_id})")
            return True
        except Exception as e:
            log.exception(f" [!] Unexpected error publishing message (CorrID: {corr_id}): {e}")
            return False

    async def call(self, user_message, user_id="default_user", timeout_sec=DEFAULT_TIMEOUT):
        """Sends a request and waits for a single JSON response."""
        await self._ensure_connection()
        corr_id = str(uuid.uuid4())
        future = self.loop.create_future()
        self._response_futures[corr_id] = future

        request_body = json.dumps({
            'user_id': user_id,
            'message': user_message,
        })

        log.info(f" [x] Sending request (call) '{user_message[:30]}...' (ID: {corr_id})")
        published = await self._publish_message(corr_id, request_body, self.callback_queue.name)

        if not published:
            if corr_id in self._response_futures: del self._response_futures[corr_id]
            future.cancel()
            return {"error": "Failed to publish message (call)"}

        try:
            log.info(f" [.] Waiting for response (call) for {corr_id} (Timeout: {timeout_sec}s)")
            result = await asyncio.wait_for(future, timeout=timeout_sec)
            log.info(f" [.] Response received for {corr_id}")
            return result
        except asyncio.TimeoutError:
            log.error(f" [!] Timeout waiting for response (call) for {corr_id}")
            return {"error": f"Timeout waiting for LLM response (call) after {timeout_sec} seconds"}
        except asyncio.CancelledError:
            log.warning(f" [!] Call request {corr_id} was cancelled.")
            return {"error": "Call request cancelled"}
        except Exception as e:
            log.exception(f" [!] Error waiting for future {corr_id}: {e}")
            return {"error": f"Error waiting for response: {e}"}
        finally:
            if corr_id in self._response_futures:
                del self._response_futures[corr_id]

    async def stream(self, user_message, user_id="default_user", timeout_sec=DEFAULT_TIMEOUT):
        """
        Sends a request and yields response chunks asynchronously.

        Yields:
            str: Chunks of the response content.
        """
        await self._ensure_connection()
        corr_id = str(uuid.uuid4())
        queue = asyncio.Queue()
        self._stream_queues[corr_id] = queue

        request_body = json.dumps({
            'user_id': user_id,
            'message': user_message,
            'stream': True
        })

        log.info(f" [x] Sending request (stream) '{user_message[:30]}...' (ID: {corr_id})")
        published = await self._publish_message(corr_id, request_body, self.callback_queue.name)

        if not published:
            if corr_id in self._stream_queues: del self._stream_queues[corr_id]
            raise ConnectionError("Failed to publish stream message")

        log.info(f" [.] Waiting for stream data for {corr_id} (Inactivity Timeout: {timeout_sec}s)")
        try:
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=timeout_sec)

                    if item is STREAM_END_MARKER:
                        log.info(f" [.] Stream {corr_id} ended normally.")
                        break
                    elif item is STREAM_ERROR_MARKER:
                        error_content = "Unknown stream error"
                        try:
                            error_content = await asyncio.wait_for(queue.get(), timeout=1)
                            queue.task_done()
                        except asyncio.TimeoutError:
                            log.warning(f"Timeout getting error content for stream {corr_id}")
                        except asyncio.QueueEmpty:
                            log.warning(f"No error content found in queue for stream {corr_id} after marker")
                        log.error(f" [!] Stream {corr_id} ended with error: {error_content}")
                        raise RuntimeError(f"Stream error from worker or processing: {error_content}")
                    else:
                        yield item
                        queue.task_done()

                except asyncio.TimeoutError:
                    log.error(f" [!] Stream {corr_id} timed out due to inactivity after {timeout_sec} seconds.")
                    raise TimeoutError(f"Stream timed out due to inactivity after {timeout_sec} seconds")
                except asyncio.CancelledError:
                    log.warning(f" [!] Stream {corr_id} was cancelled during wait.")
                    raise
                except Exception as e:
                    log.exception(f"[!] Unexpected error getting item from stream queue {corr_id}: {e}")
                    raise RuntimeError(f"Internal error processing stream queue: {e}") from e

        finally:
            if corr_id in self._stream_queues:
                log.info(f" [.] Cleaning up queue for stream {corr_id}")
                while not queue.empty():
                    with suppress(asyncio.QueueEmpty):
                        queue.get_nowait()
                        queue.task_done()
                del self._stream_queues[corr_id]

    async def _safe_close(self):
        """Safely closes consumer task, channel, and connection."""
        log.info(" [*] Initiating safe close...")
        if self._consumer_task and not self._consumer_task.done():
            log.info(" [*] Cancelling consumer task...")
            self._consumer_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._consumer_task
            log.info(" [*] Consumer task cancelled.")
            self._consumer_task = None

        if self.channel and not self.channel.is_closed:
            log.info(" [*] Closing channel...")
            with suppress(Exception):
                await self.channel.close()
            log.info(" [*] Channel closed.")
            self.channel = None

        if self.connection and not self.connection.is_closed:
            log.info(" [*] Closing connection...")
            with suppress(Exception):
                await self.connection.close()
            log.info(" [*] Connection closed.")
            self.connection = None

        self.callback_queue = None
        self._fail_pending_requests("Client closed")
        log.info(" [*] Safe close finished.")

    async def close(self):
        """Public method to explicitly close the client connection."""
        log.info(" [*] Closing connection via close() method.")
        await self._safe_close()
