"""Bounded, real HTTP POST transport: verified TLS, no redirects or env proxies."""
from __future__ import annotations

import http.client
import socket
import ssl
import threading
import time
from urllib.parse import urlsplit

from .model_types import ModelError


RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}


def post_json(endpoint: str, body: bytes, headers: dict[str, str], *, timeout: float,
              max_bytes: int) -> tuple[bytes, dict]:
    url = urlsplit(endpoint)  # Endpoint was validated before credentials are used.
    connection_type = http.client.HTTPSConnection if url.scheme == 'https' else http.client.HTTPConnection
    kwargs = {'timeout': timeout}
    if url.scheme == 'https':
        kwargs['context'] = ssl.create_default_context()
    connection = connection_type(url.hostname, url.port, **kwargs)
    expired = threading.Event()
    sockets = []
    def abort():
        expired.set()
        for sock in sockets + ([connection.sock] if connection.sock else []):
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
    timer = threading.Timer(timeout, abort)
    timer.daemon = True
    response = None
    started = time.monotonic()
    timer.start()
    try:
        connection.connect()
        if expired.is_set():
            raise TimeoutError
        sockets.append(connection.sock)
        connection.request('POST', url.path or '/', body=body, headers=headers)
        response = connection.getresponse()
        if expired.is_set():
            raise TimeoutError
        status = response.status
        if not 200 <= status < 300:
            code = 'AUTHENTICATION' if status in {401, 403} else 'HTTP_ERROR'
            if 300 <= status < 400:
                code = 'REDIRECT_REJECTED'
            raise ModelError(code, f'Model endpoint returned HTTP {status}', retryable=status in RETRYABLE_STATUS)
        media = response.getheader('Content-Type', '').split(';')[0].strip().lower()
        if media != 'application/json' and not (media.startswith('application/') and media.endswith('+json')):
            raise ModelError('INVALID_RESPONSE', 'Model endpoint did not return JSON content')
        if response.getheader('Content-Encoding', 'identity').lower() not in {'identity', ''}:
            raise ModelError('INVALID_RESPONSE', 'Compressed model responses are not supported')
        declared = response.getheader('Content-Length')
        if declared is not None:
            try:
                if int(declared) < 0 or int(declared) > max_bytes:
                    raise ValueError
            except ValueError as exc:
                raise ModelError('INVALID_RESPONSE', 'Invalid or oversized model response length') from exc
        data = bytearray()
        while True:
            if expired.is_set():
                raise TimeoutError
            chunk = response.read1(min(65536, max_bytes + 1 - len(data)))
            data.extend(chunk)
            if len(data) > max_bytes:
                raise ModelError('INVALID_RESPONSE', 'Model response exceeds the byte limit')
            if not chunk:
                break
        if expired.is_set():
            raise TimeoutError
        if declared is not None and len(data) != int(declared):
            raise ModelError('INVALID_RESPONSE', 'Incomplete model response body')
        return bytes(data), {'http_status': status, 'duration_seconds': round(time.monotonic() - started, 6)}
    except ModelError:
        raise
    except (TimeoutError, socket.timeout) as exc:
        raise ModelError('TIMEOUT', 'Model request timed out', retryable=True) from exc
    except ssl.SSLError as exc:
        raise ModelError('TLS_ERROR', 'Model endpoint TLS verification or handshake failed') from exc
    except (OSError, http.client.HTTPException) as exc:
        if expired.is_set():
            raise ModelError('TIMEOUT', 'Model request timed out', retryable=True) from exc
        raise ModelError('NETWORK_ERROR', 'Model request could not be completed', retryable=True) from exc
    except (ValueError, UnicodeError) as exc:
        raise ModelError('CONFIGURATION', 'Endpoint or authentication cannot be encoded for HTTP') from exc
    finally:
        timer.cancel()
        if response is not None:
            response.close()
        connection.close()
