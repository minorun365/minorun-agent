"""AgentCore の基盤がコンテナ内（127.0.0.1）から 8080 番へ TLS の握手を1回送ってくる。

新しい会話のたびに最初の1回だけ届き、uvicorn は HTTP として読めないので
「WARNING: Invalid HTTP request received.」を出して 400 を返す（基盤はその後ふつうの HTTP で呼び直す）。
2026-10-25 に届いたバイト列を記録して確かめた（先頭が 0x16 0x03 0x01 の ClientHello）。

その接続だけを黙って閉じる。外から届いた不正な要求や、TLS 以外の壊れた要求は従来どおり警告を出す。
"""

import ipaddress

import uvicorn.protocols.http.h11_impl as h11_impl

_TLS_HANDSHAKE = b"\x16\x03"
_original = h11_impl.H11Protocol.data_received


def _is_loopback(transport):
    peer = transport.get_extra_info("peername") if transport else None
    try:
        return ipaddress.ip_address(peer[0]).is_loopback
    except (TypeError, ValueError, IndexError):
        return False


def _data_received(self, data):
    if data.startswith(_TLS_HANDSHAKE) and not self.conn.trailing_data[0] and _is_loopback(self.transport):
        self.transport.close()
        return
    _original(self, data)


h11_impl.H11Protocol.data_received = _data_received
