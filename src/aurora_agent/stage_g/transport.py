"""Bounded HTTPS without redirects, proxy inheritance, or implicit retries."""
from dataclasses import dataclass
import http.client
import ssl
from urllib.parse import urlsplit

from .contract import canonical
from .errors import ProtocolError, UncertainExecution

@dataclass(frozen=True)
class Response:
    status: int
    headers: dict
    body: bytes

class HTTPS:
    def __init__(self, base_url, *, ca_file=None, cert_file=None, key_file=None, timeout=10):
        p = urlsplit(base_url)
        if (p.scheme != "https" or not p.hostname or p.username or p.password or
                p.query or p.fragment or "\\" in base_url or "%" in p.path or
                any(c.isspace() for c in base_url) or ".." in p.path.split("/")):
            raise ProtocolError("HTTPS_ENDPOINT_REQUIRED")
        self.host, self.port = p.hostname, p.port or 443
        self.prefix = p.path.rstrip("/")
        if not 0 < timeout <= 60:
            raise ProtocolError("TIMEOUT_INVALID")
        self.timeout = timeout
        self.context = ssl.create_default_context(cafile=ca_file)
        self.context.minimum_version = ssl.TLSVersion.TLSv1_3 if cert_file else ssl.TLSVersion.TLSv1_2
        if bool(cert_file) != bool(key_file):
            raise ProtocolError("CLIENT_CERTIFICATE_PAIR_REQUIRED")
        if cert_file:
            self.context.load_cert_chain(cert_file, key_file)

    def request(self, method, path, *, payload=None, headers=None, max_bytes=1048576):
        if not path.startswith("/") or path.startswith("//") or any(c in path for c in "\r\n\\"):
            raise ProtocolError("REQUEST_PATH_INVALID")
        body = None if payload is None else canonical(payload)
        if body is not None and len(body) > 1048576:
            raise ProtocolError("REQUEST_TOO_LARGE")
        h = {"Accept": "application/json", "Accept-Encoding": "identity", **(headers or {})}
        if body is not None:
            h["Content-Type"] = "application/json"
        connection = http.client.HTTPSConnection(self.host, self.port, timeout=self.timeout, context=self.context)
        try:
            connection.request(method, self.prefix + path, body=body, headers=h)
            response = connection.getresponse()
            if 300 <= response.status < 400:
                raise ProtocolError("REDIRECT_REFUSED", status=response.status)
            if response.getheader("Content-Encoding", "identity").lower() != "identity":
                raise ProtocolError("ENCODING_UNSUPPORTED")
            lengths = response.headers.get_all("Content-Length", [])
            if lengths and (len(lengths) != 1 or not lengths[0].isdigit() or int(lengths[0]) > max_bytes):
                raise ProtocolError("RESPONSE_TOO_LARGE")
            raw = response.read(max_bytes + 1)
            if len(raw) > max_bytes:
                raise ProtocolError("RESPONSE_TOO_LARGE")
            return Response(response.status, {k.lower(): v for k, v in response.getheaders()}, raw)
        except (OSError, http.client.HTTPException) as exc:
            # Even an apparent connect failure is not grounds for write replay.
            raise UncertainExecution("TRANSPORT_RESULT_UNAVAILABLE") from exc
        finally:
            connection.close()
