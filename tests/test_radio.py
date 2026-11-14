"""The hub drops links a previous hub left on the proxy (radio.drop_stale_links)."""
import asyncio

from xbloom_hub.radio import drop_stale_links


class FakeClient:
    instances = []

    def __init__(self, host, port, password, noise_psk=None, allocated=(11, 22), fail=None):
        self.allocated, self.fail = list(allocated), fail
        self.dropped, self.closed = [], False
        FakeClient.instances.append(self)

    async def connect(self, login=False):
        if self.fail:
            raise self.fail

    def subscribe_bluetooth_connections_free(self, callback):
        asyncio.get_running_loop().call_soon(callback, 5 - len(self.allocated), 5, self.allocated)
        return lambda: None

    async def bluetooth_device_disconnect(self, address):
        self.dropped.append(address)

    async def disconnect(self):
        self.closed = True


def test_every_link_the_proxy_holds_is_dropped():
    assert asyncio.run(drop_stale_links("proxy", "key", FakeClient)) == 2
    client = FakeClient.instances[-1]
    assert client.dropped == [11, 22] and client.closed


def test_a_proxy_that_cannot_be_asked_does_not_stop_the_hub():
    def failing(*args, **kwargs):
        return FakeClient(*args, **kwargs, fail=ConnectionError("no proxy"))
    assert asyncio.run(drop_stale_links("proxy", "key", failing)) == 0
    assert FakeClient.instances[-1].closed
