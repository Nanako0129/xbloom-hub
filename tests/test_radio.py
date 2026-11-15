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


def test_every_search_first_drops_what_the_proxy_still_holds(monkeypatch):
    import xbloom_hub.radio as radio_module
    from xbloom_hub.radio import ProxyRadio

    calls = []

    async def fake_drop(host, key, client_class=None):
        calls.append(host)
        return 1

    monkeypatch.setattr(radio_module, "drop_stale_links", fake_drop)

    class NoScanners:
        def async_current_scanners(self):
            return []

    import habluetooth
    monkeypatch.setattr(habluetooth, "get_manager", lambda: NoScanners())
    radio = ProxyRadio("proxy", "key")
    for _ in range(2):
        assert asyncio.run(radio.find_device(timeout=0)) is None
    assert calls == ["proxy", "proxy"]                  # before each search, not once at start
