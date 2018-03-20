import socket
import time

import pytest

from etcd3 import Client
from .envs import protocol, host, port
from .etcd_go_cli import etcdctl, NO_ETCD_SERVICE


@pytest.fixture(scope='module')
def client():
    """
    init Etcd3Client, close its connection-pool when teardown
    """
    c = Client(host, port, protocol)
    yield c
    c.close()


@pytest.mark.skipif(NO_ETCD_SERVICE, reason="no etcd service available")
def test_watcher(client):
    max_retries = 3
    w = client.Watcher(all=True, progress_notify=True, prev_kv=True, max_retries=max_retries)
    foo_list = []
    fizz_list = []
    all_list = []
    w.onEvent(lambda e: e.key == b'foo', lambda e: foo_list.append(e))
    w.onEvent('fiz.', lambda e: fizz_list.append(e))
    w.onEvent(lambda e: all_list.append(e))

    assert len(w.callbacks) == 3

    w.runDaemon()
    # with pytest.raises(RuntimeError):
    #     w.runDaemon()
    #     w.run()

    time.sleep(0.2)
    assert w.watching
    assert w._thread.is_alive

    etcdctl('put foo bar')
    etcdctl('put foo bar')
    etcdctl('put fizz buzz')
    etcdctl('put fizz buzz')
    etcdctl('put fizz buzz')

    time.sleep(1)
    w.stop()

    time.sleep(0.5)
    assert not w.watching
    assert not w._thread.is_alive()

    assert len(foo_list) == 2
    assert len(fizz_list) == 3
    assert len(all_list) == 5

    etcdctl('put foo bar')
    etcdctl('put fizz buzz')

    foo_list = []
    fizz_list = []

    w.runDaemon()
    time.sleep(1)
    w.stop()

    assert len(foo_list) == 1
    assert len(fizz_list) == 1
    assert len(all_list) == 7

    w.clear_callbacks()
    assert len(w.callbacks) == 0

    times = 3
    with w:
        etcdctl('put foo bar')
        for e in w:
            if not times:
                break
            assert e.key == b'foo'
            assert e.value == b'bar'
            etcdctl('put foo bar')
            times -= 1
    assert not w.watching
    assert w._resp.raw.closed

    # test retry
    w.runDaemon()

    times = max_retries + 1
    while times:
        time.sleep(0.5)
        if not w._resp.raw.closed: # directly close the tcp connection
            s = socket.fromfd(w._resp.raw._fp.fileno(), socket.AF_INET, socket.SOCK_STREAM)
            s.shutdown(socket.SHUT_RDWR)
            s.close()
            times -= 1

    w._thread.join()
    assert not w.watching
    assert not w._thread.is_alive()
    assert w._resp.raw.closed
    assert len(w.errors) == max_retries
