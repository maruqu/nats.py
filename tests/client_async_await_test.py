import sys
import asyncio
import unittest

from nats.aio.client import Client as NATS
from nats.aio.errors import ErrTimeout, ErrSlowConsumer
from tests.utils import (async_test, SingleServerTestCase)


class ClientAsyncAwaitTest(SingleServerTestCase):
    @async_test
    async def test_async_await_subscribe_sync(self):
        nc = NATS()
        msgs = []

        async def subscription_handler(msg):
            if msg.subject == "tests.1":
                await asyncio.sleep(0.5, loop=self.loop)
            if msg.subject == "tests.3":
                await asyncio.sleep(0.2, loop=self.loop)
            msgs.append(msg)

        await nc.connect(io_loop=self.loop)
        sid = await nc.subscribe("tests.>", cb=subscription_handler)

        for i in range(0, 5):
            await nc.publish("tests.{}".format(i), b'bar')

        # Wait a bit for messages to be received.
        await asyncio.sleep(1, loop=self.loop)
        self.assertEqual(5, len(msgs))
        self.assertEqual("tests.1", msgs[1].subject)
        self.assertEqual("tests.3", msgs[3].subject)
        await nc.close()

    @async_test
    async def test_async_await_messages_delivery_order(self):
        nc = NATS()
        msgs = []
        errors = []

        async def error_handler(e):
            errors.push(e)

        await nc.connect(io_loop=self.loop, error_cb=error_handler)

        async def handler_foo(msg):
            msgs.append(msg)

            # Should not block other subscriptions from receiving messages.
            await asyncio.sleep(0.2, loop=self.loop)
            if msg.reply != "":
                await nc.publish(msg.reply, msg.data * 2)

        await nc.subscribe("foo", cb=handler_foo)

        async def handler_bar(msg):
            msgs.append(msg)
            if msg.reply != "":
                await nc.publish(msg.reply, b'')

        await nc.subscribe("bar", cb=handler_bar)

        await nc.publish("foo", b'1')
        await nc.publish("foo", b'2')
        await nc.publish("foo", b'3')

        # Will be processed before the others since no head of line
        # blocking among the subscriptions.
        await nc.publish("bar", b'4')

        response = await nc.request("foo", b'hello1', 1)
        self.assertEqual(response.data, b'hello1hello1')

        with self.assertRaises(ErrTimeout):
            await nc.request("foo", b'hello2', 0.1)

        await nc.publish("bar", b'5')
        response = await nc.request("foo", b'hello2', 1)
        self.assertEqual(response.data, b'hello2hello2')

        self.assertEqual(msgs[0].data, b'1')
        self.assertEqual(msgs[1].data, b'4')
        self.assertEqual(msgs[2].data, b'2')
        self.assertEqual(msgs[3].data, b'3')
        self.assertEqual(msgs[4].data, b'hello1')
        self.assertEqual(msgs[5].data, b'hello2')
        self.assertEqual(len(errors), 0)
        await nc.close()

    @async_test
    async def test_subscription_slow_consumer_pending_msg_limit(self):
        nc = NATS()
        msgs = []
        errors = []

        async def error_handler(e):
            if type(e) is ErrSlowConsumer:
                errors.append(e)

        await nc.connect(io_loop=self.loop, error_cb=error_handler)

        async def handler_foo(msg):
            await asyncio.sleep(0.2, loop=self.loop)

            msgs.append(msg)
            if msg.reply != "":
                await nc.publish(msg.reply, msg.data * 2)

        await nc.subscribe("foo", cb=handler_foo, pending_msgs_limit=5)

        async def handler_bar(msg):
            msgs.append(msg)
            if msg.reply != "":
                await nc.publish(msg.reply, msg.data * 3)

        await nc.subscribe("bar", cb=handler_bar)

        for i in range(10):
            await nc.publish("foo", '{}'.format(i).encode())

        # Will be processed before the others since no head of line
        # blocking among the subscriptions.
        await nc.publish("bar", b'14')
        response = await nc.request("bar", b'hi1', 2)
        self.assertEqual(response.data, b'hi1hi1hi1')

        self.assertEqual(len(msgs), 2)
        self.assertEqual(msgs[0].data, b'14')
        self.assertEqual(msgs[1].data, b'hi1')

        # Consumed messages but the rest were slow consumers.
        self.assertTrue(4 <= len(errors) <= 5)
        for e in errors:
            self.assertEqual(type(e), ErrSlowConsumer)
        self.assertEqual(errors[0].sid, 1)
        await nc.close()

    @async_test
    async def test_subscription_slow_consumer_pending_bytes_limit(self):
        nc = NATS()
        msgs = []
        errors = []

        async def error_handler(e):
            if type(e) is ErrSlowConsumer:
                errors.append(e)

        await nc.connect(io_loop=self.loop, error_cb=error_handler)

        async def handler_foo(msg):
            await asyncio.sleep(0.2, loop=self.loop)

            msgs.append(msg)
            if msg.reply != "":
                await nc.publish(msg.reply, msg.data * 2)

        await nc.subscribe("foo", cb=handler_foo, pending_bytes_limit=10)

        async def handler_bar(msg):
            msgs.append(msg)
            if msg.reply != "":
                await nc.publish(msg.reply, msg.data * 3)

        await nc.subscribe("bar", cb=handler_bar)

        for i in range(10):
            await nc.publish("foo", "AAA{}".format(i).encode())

        # Will be processed before the others since no head of line
        # blocking among the subscriptions.
        await nc.publish("bar", b'14')

        response = await nc.request("bar", b'hi1', 2)
        self.assertEqual(response.data, b'hi1hi1hi1')
        self.assertEqual(len(msgs), 2)
        self.assertEqual(msgs[0].data, b'14')
        self.assertEqual(msgs[1].data, b'hi1')

        # Consumed a few messages but the rest were slow consumers.
        self.assertTrue(7 <= len(errors) <= 8)
        for e in errors:
            self.assertEqual(type(e), ErrSlowConsumer)
        self.assertEqual(errors[0].sid, 1)

        # Try again a few seconds later and it should have recovered
        await asyncio.sleep(3, loop=self.loop)
        response = await nc.request("foo", b'B', 1)
        self.assertEqual(response.data, b'BB')
        await nc.close()


if __name__ == '__main__':
    runner = unittest.TextTestRunner(stream=sys.stdout)
    unittest.main(verbosity=2, exit=False, testRunner=runner)
