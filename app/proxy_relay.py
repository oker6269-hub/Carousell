# -*- coding: utf-8 -*-
"""本地代理转发：让 Chrome 能用「带账号密码」的上游代理。

Chrome 的 `--proxy-server` 不支持 `user:pass@host:port`（实测报
ERR_NO_SUPPORTED_PROXIES），带 HTTP 认证的代理必须由外部补 `Proxy-Authorization`。

做法：在本机 127.0.0.1 起一个无认证的小代理，收到请求后转发到上游代理，
并补上 Basic 认证头。Chrome 只需要 `--proxy-server=http://127.0.0.1:<port>`。

只支持 http/https 上游（socks5 带认证需要另写握手，遇到会提示改用 http）。
"""
import base64
import select
import socket
import threading
from urllib.parse import unquote, urlsplit

BUFSIZE = 65536


class ProxyRelay:
    """把上游代理包装成本地免认证代理。"""

    def __init__(self, upstream: str, log=print):
        u = urlsplit(upstream)
        self.upstream = upstream
        self.log = log
        self.scheme = (u.scheme or "http").lower()
        self.host = u.hostname or ""
        self.port = u.port or (443 if self.scheme == "https" else 80)
        self.auth = None
        if u.username:
            raw = f"{unquote(u.username)}:{unquote(u.password or '')}"
            self.auth = base64.b64encode(raw.encode("utf-8")).decode("ascii")
        self._sock = None
        self._thread = None
        self._stop = threading.Event()
        self.local_url = ""

    # ---------- 生命周期 ----------

    def start(self) -> str:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(128)
        port = self._sock.getsockname()[1]
        self._thread = threading.Thread(target=self._serve, name="proxy-relay", daemon=True)
        self._thread.start()
        self.local_url = f"http://127.0.0.1:{port}"
        return self.local_url

    def stop(self):
        self._stop.set()
        try:
            if self._sock:
                self._sock.close()
        except OSError:
            pass
        self._sock = None

    # ---------- 服务 ----------

    def _serve(self):
        while not self._stop.is_set():
            try:
                client, _ = self._sock.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(client,), daemon=True).start()

    def _handle(self, client: socket.socket):
        upstream = None
        try:
            head = b""
            while b"\r\n\r\n" not in head:
                chunk = client.recv(BUFSIZE)
                if not chunk:
                    return
                head += chunk
                if len(head) > 128 * 1024:
                    return
            end = head.index(b"\r\n\r\n") + 4
            header, rest = head[:end], head[end:]
            method, target = self._request_line(header)
            if not method:
                return
            upstream = socket.create_connection((self.host, self.port), timeout=20)
            if method == "CONNECT":
                if not self._tunnel(client, upstream, target, header, rest):
                    return
            else:
                upstream.sendall(self._with_auth(header) + rest)
            self._pump(client, upstream)
        except Exception:
            pass
        finally:
            for s in (client, upstream):
                try:
                    if s:
                        s.close()
                except OSError:
                    pass

    @staticmethod
    def _request_line(header: bytes):
        try:
            line = header.split(b"\r\n", 1)[0].decode("latin-1")
            method, target, _ = line.split(" ", 2)
            return method.upper(), target
        except Exception:
            return "", ""

    def _with_auth(self, header: bytes) -> bytes:
        """在请求头里补 Proxy-Authorization（已有则不动）。"""
        if not self.auth:
            return header
        if b"proxy-authorization:" in header.lower():
            return header
        first_end = header.index(b"\r\n") + 2
        auth_line = f"Proxy-Authorization: Basic {self.auth}\r\n".encode()
        return header[:first_end] + auth_line + header[first_end:]

    def _tunnel(self, client, upstream, target: str, header: bytes, rest: bytes) -> bool:
        req = f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n"
        if self.auth:
            req += f"Proxy-Authorization: Basic {self.auth}\r\n"
        req += "\r\n"
        upstream.sendall(req.encode("latin-1"))
        resp = b""
        while b"\r\n\r\n" not in resp:
            chunk = upstream.recv(4096)
            if not chunk:
                break
            resp += chunk
        if b" 200" not in resp.split(b"\r\n", 1)[0]:
            client.sendall(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n")
            return False
        client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        if rest:
            upstream.sendall(rest)
        return True

    @staticmethod
    def _pump(a: socket.socket, b: socket.socket):
        socks = [a, b]
        while True:
            try:
                ready, _, err = select.select(socks, [], socks, 60)
            except (OSError, ValueError):
                return
            if err:
                return
            if not ready:
                continue
            for s in ready:
                try:
                    data = s.recv(BUFSIZE)
                except OSError:
                    return
                if not data:
                    return
                other = b if s is a else a
                try:
                    other.sendall(data)
                except OSError:
                    return


def ensure_local_proxy(url: str, log=print):
    """需要认证的 http(s) 上游 → 起本地转发；不需要的返回 None。"""
    if not url:
        return url, None
    u = urlsplit(url)
    if not u.username:
        return url, None
    if (u.scheme or "").lower().startswith("socks"):
        log("[IP] socks5 带账号密码的代理暂不支持，请把该条改成 http://host:port:user:pass")
        return url, None
    relay = ProxyRelay(url, log=log)
    local = relay.start()
    return local, relay
