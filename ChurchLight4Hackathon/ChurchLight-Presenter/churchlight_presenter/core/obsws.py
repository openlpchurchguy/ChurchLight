# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) ChurchLight Presenter contributors
"""A small client for OBS Studio's WebSocket server (obs-websocket 5, built into OBS 28 and newer).

Only what ChurchLight needs: connect (with the password if there is one), send a request, read its answer.
Standard library only. Frames follow RFC 6455; the JSON messages follow the obs-websocket 5 protocol.
The helpers at the top are shared with the tests' pretend OBS."""
import base64
import hashlib
import json
import os
import socket
import struct
import logging
import uuid

SUBPROTOCOL = "obswebsocket.json"
GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
MAX_MESSAGE = 4 * 1024 * 1024
RPC_VERSION = 1
AUTH_FAILED = 4009          # the WebSocket close code obs-websocket uses for a wrong password
log = logging.getLogger("churchlight_presenter.obs")


class ObsError(Exception):
    """Something went wrong talking to OBS; `str()` is written for a volunteer. `code` is the WebSocket close code
    when OBS closed the connection and said why (4009 means the password was wrong)."""
    def __init__(self, message, code=None, reason=""):
        super().__init__(message)
        self.code = code
        self.reason = reason


# -- frames --------------------------------------------------------------------------------------

def encode_frame(payload, opcode=0x1, mask=True):
    data = payload.encode("utf-8") if isinstance(payload, str) else bytes(payload)
    head = bytearray([0x80 | opcode])
    length = len(data)
    flag = 0x80 if mask else 0
    if length < 126:
        head.append(flag | length)
    elif length < 65536:
        head.append(flag | 126)
        head += struct.pack(">H", length)
    else:
        head.append(flag | 127)
        head += struct.pack(">Q", length)
    if mask:
        key = os.urandom(4)
        head += key
        data = bytes(b ^ key[i % 4] for i, b in enumerate(data))
    return bytes(head) + data


def _read_exact(sock, count):
    chunks = []
    while count:
        chunk = sock.recv(count)
        if not chunk:
            raise ObsError("OBS closed the connection")
        chunks.append(chunk)
        count -= len(chunk)
    return b"".join(chunks)


def read_frame(sock):
    """(fin, opcode, payload) of the next frame."""
    first, second = _read_exact(sock, 2)
    fin, opcode = bool(first & 0x80), first & 0x0F
    length = second & 0x7F
    if length == 126:
        length = struct.unpack(">H", _read_exact(sock, 2))[0]
    elif length == 127:
        length = struct.unpack(">Q", _read_exact(sock, 8))[0]
    if length > MAX_MESSAGE:
        raise ObsError("OBS sent a message that is too large")
    key = _read_exact(sock, 4) if second & 0x80 else None
    data = _read_exact(sock, length) if length else b""
    if key:
        data = bytes(b ^ key[i % 4] for i, b in enumerate(data))
    return fin, opcode, data


def read_message(sock, send_pong=None):
    """The next whole text message, joining fragments, answering pings. Raises ObsError when OBS closes."""
    parts, total = [], 0
    while True:
        fin, opcode, data = read_frame(sock)
        if opcode == 0x8:
            code = struct.unpack(">H", data[:2])[0] if len(data) >= 2 else None
            reason = data[2:].decode("utf-8", "replace").strip()
            raise ObsError("OBS closed the connection" + (f" ({code}: {reason})" if code and reason else f" ({code})" if code else ""), code, reason)
        if opcode == 0x9:
            if send_pong:
                send_pong(data)
            continue
        if opcode == 0xA:
            continue
        parts.append(data)
        total += len(data)
        if total > MAX_MESSAGE:
            raise ObsError("OBS sent a message that is too large")
        if fin:
            return b"".join(parts).decode("utf-8", "replace")


def auth_string(password, salt, challenge):
    """The answer obs-websocket 5 wants when a password is set."""
    secret = base64.b64encode(hashlib.sha256((password + salt).encode()).digest()).decode()
    return base64.b64encode(hashlib.sha256((secret + challenge).encode()).digest()).decode()


# -- the client ----------------------------------------------------------------------------------

class ObsClient:
    def __init__(self, host="127.0.0.1", port=4455, password="", timeout=6.0):
        self.host, self.port, self.password, self.timeout = host, port, password or "", timeout
        self.sock = None
        self.version = ""

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *exc):
        self.close()

    def connect(self):
        try:
            self._connect_once()
        except ObsError as err:
            # A pasted password often carries a space or a line break at the end. If OBS refused it and trimming changes it, try once more trimmed.
            if err.code == AUTH_FAILED and self.password != self.password.strip():
                log.info("OBS refused the password; trying again without spaces or line breaks around it")
                self.password = self.password.strip()
                self._connect_once()
            elif err.code == AUTH_FAILED:
                raise ObsError(f"{err} ChurchLight sent a password of {len(self.password)} characters. If the password was copied "
                               "from OBS after pressing Apply, close OBS completely, open it again and try once more.", err.code, err.reason) from err
            else:
                raise

    def _connect_once(self):
        try:
            self.sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        except OSError as err:
            raise ObsError("OBS did not answer. Is OBS open, with the WebSocket server turned on "
                           f"(Tools > WebSocket Server Settings)? Port {self.port}.") from err
        self.sock.settimeout(self.timeout)
        try:
            self._handshake()
            self._identify()
        except ObsError:
            self.close()
            raise
        except (OSError, ValueError) as err:
            self.close()
            raise ObsError(f"Could not talk to OBS: {err}") from err

    def close(self):
        sock, self.sock = self.sock, None
        if sock is not None:
            try:
                sock.sendall(encode_frame(b"", 0x8))
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass

    def _handshake(self):
        key = base64.b64encode(os.urandom(16)).decode()
        request = (f"GET / HTTP/1.1\r\nHost: {self.host}:{self.port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                   f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n"
                   f"Sec-WebSocket-Protocol: {SUBPROTOCOL}\r\n\r\n")
        self.sock.sendall(request.encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = self.sock.recv(1)
            if not chunk or len(head) > 8192:
                raise ObsError("That address answered, but it is not OBS's WebSocket server. Check the port.")
            head += chunk
        lines = head.decode("latin-1").split("\r\n")
        if " 101 " not in lines[0]:
            raise ObsError("That address answered, but it is not OBS's WebSocket server. Check the port.")
        accept = base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()
        headers = {l.split(":", 1)[0].strip().lower(): l.split(":", 1)[1].strip() for l in lines[1:] if ":" in l}
        if headers.get("sec-websocket-accept") != accept:
            raise ObsError("That address is not OBS's WebSocket server. Check the port.")

    def _send(self, message):
        self.sock.sendall(encode_frame(json.dumps(message)))

    def _read(self):
        try:
            text = read_message(self.sock, lambda data: self.sock.sendall(encode_frame(data, 0xA)))
        except socket.timeout as err:
            raise ObsError("OBS took too long to answer") from err
        return json.loads(text)

    def _identify(self):
        hello = self._read()
        if hello.get("op") != 0:
            raise ObsError("OBS did not say hello. Is that port OBS's WebSocket server?")
        data = hello.get("d") or {}
        self.version = data.get("obsWebSocketVersion", "")
        log.info("OBS WebSocket %s said hello (password asked for: %s)", self.version or "?", bool(data.get("authentication")))
        identify = {"rpcVersion": RPC_VERSION, "eventSubscriptions": 0}
        auth = data.get("authentication")
        if auth:
            if not self.password:
                raise ObsError("OBS asks for a password. Type the password from OBS's WebSocket Server Settings.")
            identify["authentication"] = auth_string(self.password, auth["salt"], auth["challenge"])
        self._send({"op": 1, "d": identify})
        try:
            reply = self._read()
        except ObsError as err:
            log.warning("OBS did not accept the connection: %s", err)
            if err.code == AUTH_FAILED:
                said = f' OBS WebSocket {self.version} said: "{err.reason}"' if err.reason else f" (OBS WebSocket {self.version})"
                raise ObsError("OBS refused the password." + said, err.code, err.reason) from err
            if err.code:
                raise ObsError(f"OBS closed the connection and said: {err}. This is not a wrong-password message from OBS.", err.code) from err
            if auth:
                raise ObsError(f"OBS asked for a password but then did not answer ({err}). If the password is right, close and reopen OBS and try again.") from err
            raise
        if reply.get("op") != 2:
            raise ObsError("OBS refused the connection.")

    def request(self, request_type, data=None):
        """Send one request and return its responseData (a dict, maybe empty). Raises ObsError with OBS's reason."""
        if self.sock is None:
            raise ObsError("Not connected to OBS")
        request_id = uuid.uuid4().hex
        self._send({"op": 6, "d": {"requestType": request_type, "requestId": request_id, "requestData": data or {}}})
        while True:
            message = self._read()
            if message.get("op") != 7 or (message.get("d") or {}).get("requestId") != request_id:
                continue                                   # an event or an old answer
            reply = message["d"]
            status = reply.get("requestStatus") or {}
            if not status.get("result"):
                raise ObsError(f"OBS could not do {request_type}: {status.get('comment') or 'code ' + str(status.get('code'))}")
            return reply.get("responseData") or {}
