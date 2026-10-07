"""No-outbound-network self-test (v1 design §1.4/§G9, task 1).

The autouse fixture in conftest guards four entry points. This test asserts
each raises for a public hostname and for a public IP literal *before any I/O
happens*, while loopback (socketpair, 127.0.0.1 bind/connect) still works.
"""
import asyncio
import socket

PUBLIC_HOST = "example.com"
PUBLIC_IP = "93.184.216.34"
MSG = "network access is disabled in tests"


def test_getaddrinfo_raises_for_public_host():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.2)
        try:
            socket.getaddrinfo(PUBLIC_HOST, 80)
            raise AssertionError("getaddrinfo did not raise")
        except RuntimeError as exc:
            assert MSG in str(exc)


def _raw_socket():
    return socket.socket(socket.AF_INET, socket.SOCK_STREAM)


def test_connect_raises_for_public_ip_literal():
    with _raw_socket() as s:
        s.settimeout(0.2)
        try:
            s.connect((PUBLIC_IP, 80))
            raise AssertionError("connect did not raise")
        except RuntimeError as exc:
            assert MSG in str(exc)


def test_connect_ex_raises_for_public_ip_literal():
    with _raw_socket() as s:
        s.settimeout(0.2)
        try:
            s.connect_ex((PUBLIC_IP, 80))
            raise AssertionError("connect_ex did not raise")
        except RuntimeError as exc:
            assert MSG in str(exc)


def test_create_connection_raises_for_public_ip_literal():
    async def _try():
        loop = asyncio.get_running_loop()
        return await loop.create_connection(asyncio.Protocol, PUBLIC_IP, 80)

    try:
        asyncio.run(_try())
        raise AssertionError("create_connection did not raise")
    except RuntimeError as exc:
        assert MSG in str(exc)


def test_loopback_still_works():
    # socketpair (asyncio wakeups on Windows use a loopback pair)
    left, right = socket.socketpair()
    left.send(b"x")
    assert right.recv(1) == b"x"
    left.close()
    right.close()

    # a 127.0.0.1 bind + connect through the guarded entry points
    listener = _raw_socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    client = _raw_socket()
    client.settimeout(2)
    client.connect(("127.0.0.1", port))  # must not raise
    accepted, _ = listener.accept()
    accepted.close()
    client.close()
    listener.close()


def test_getaddrinfo_resolves_loopback_names():
    # "localhost" is allowed and resolvable without network I/O
    infos = socket.getaddrinfo("localhost", 0, type=socket.SOCK_STREAM)
    assert infos
