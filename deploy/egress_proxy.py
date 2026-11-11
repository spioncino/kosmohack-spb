#!/usr/bin/env python3
"""HTTPS-прокси (метод CONNECT) для контейнеров evarisk на командном сервере.

С сервера DONKI (kauai.ccmc.gsfc.nasa.gov) не отвечает по IPv4, а контейнеры Docker ходят наружу только по IPv4;
хост умеет IPv6. Прокси слушает адрес моста docker0, пускает только подсеть Docker и соединяется с целью с хоста:
сначала IPv6, через 0.3 с параллельно IPv4 (happy eyeballs). Запросы по HTTP без TLS не проксируются.
"""

import asyncio
import ipaddress

LISTEN_HOST = "172.17.0.1"
LISTEN_PORT = 3128
ALLOWED = ipaddress.ip_network("172.16.0.0/12")
CONNECT_TIMEOUT_S = 30
HEADER_TIMEOUT_S = 15


async def pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    except (ConnectionError, OSError):
        pass
    finally:
        writer.close()


async def handle(client_r: asyncio.StreamReader, client_w: asyncio.StreamWriter) -> None:
    peer = client_w.get_extra_info("peername")[0]
    if ipaddress.ip_address(peer) not in ALLOWED:
        client_w.close()
        return
    try:
        head = await asyncio.wait_for(client_r.readuntil(b"\r\n\r\n"), HEADER_TIMEOUT_S)
        method, target, _ = head.split(b"\r\n", 1)[0].decode("ascii").split(" ", 2)
        if method != "CONNECT":
            client_w.write(b"HTTP/1.1 405 Method Not Allowed\r\n\r\n")
            await client_w.drain()
            client_w.close()
            return
        host, port = target.rsplit(":", 1)
        upstream_r, upstream_w = await asyncio.wait_for(
            asyncio.open_connection(host.strip("[]"), int(port), happy_eyeballs_delay=0.3), CONNECT_TIMEOUT_S
        )
    except (TimeoutError, ValueError, OSError, asyncio.IncompleteReadError, asyncio.LimitOverrunError):
        try:
            client_w.write(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
            await client_w.drain()
        except (ConnectionError, OSError):
            pass
        client_w.close()
        return
    client_w.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
    await client_w.drain()
    await asyncio.gather(pipe(client_r, upstream_w), pipe(upstream_r, client_w))


async def main() -> None:
    server = await asyncio.start_server(handle, LISTEN_HOST, LISTEN_PORT)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
