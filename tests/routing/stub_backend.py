"""Echo backend for proxy-mode tests.

It answers with the request path and Authorization header.

With a certificate chain and key it serves TLS, as a real repository does.
"""

import json
import ssl
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Echo(BaseHTTPRequestHandler):
    """Answers every GET with its path and Authorization header as JSON."""

    def do_GET(self) -> None:
        """Echo the request path and Authorization header."""
        body = json.dumps(
            {'path': self.path, 'authorization': self.headers.get('Authorization')}
        ).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:
        """Keep the access log quiet."""


server = ThreadingHTTPServer(('0.0.0.0', int(sys.argv[1])), Echo)
if len(sys.argv) > 2:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(sys.argv[2], sys.argv[3])
    server.socket = context.wrap_socket(server.socket, server_side=True)
server.serve_forever()
