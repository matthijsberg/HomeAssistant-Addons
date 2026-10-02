import http.server
import socketserver
import requests
import os
import sys

PORT = 8099
OLLAMA_URL = "http://localhost:11434"

class ProxyHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/api/") or self.path.startswith("/v1/"):
            self.proxy_request("GET")
        elif self.path == "/" or self.path == "":
            self.path = "/index.html"
            return http.server.SimpleHTTPRequestHandler.do_GET(self)
        else:
            return http.server.SimpleHTTPRequestHandler.do_GET(self)

    def do_POST(self):
        if self.path.startswith("/api/") or self.path.startswith("/v1/"):
            self.proxy_request("POST")
        else:
            self.send_error(404)

    def do_DELETE(self):
        if self.path.startswith("/api/") or self.path.startswith("/v1/"):
            self.proxy_request("DELETE")
        else:
            self.send_error(404)

    def end_headers(self):
        self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
        self.send_header('Pragma', 'no-cache')
        self.send_header('Expires', '0')
        super().end_headers()

    def proxy_request(self, method):
        url = f"{OLLAMA_URL}{self.path}"
        try:
            headers = {k: v for k, v in self.headers.items() if k.lower() != 'host'}
            
            body = None
            if method in ["POST", "DELETE", "PUT", "PATCH"]:
                content_length = int(self.headers.get('Content-Length', 0))
                if content_length > 0:
                    body = self.rfile.read(content_length)
            
            resp = requests.request(method, url, data=body, headers=headers, stream=True)

            self.send_response(resp.status_code)
            for key, value in resp.headers.items():
                if key.lower() not in ['content-encoding', 'transfer-encoding', 'content-length', 'connection']:
                    self.send_header(key, value)
            self.end_headers()

            for chunk in resp.iter_content(chunk_size=4096):
                self.wfile.write(chunk)
                self.wfile.flush()

        except Exception as e:
            print(f"Proxy error: {e}", file=sys.stderr)
            self.send_error(500, str(e))

if __name__ == "__main__":
    # Serve files from the directory where this script is located
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    # Allow reuse address to avoid "Address already in use" on restart
    socketserver.ThreadingTCPServer.allow_reuse_address = True
    with socketserver.ThreadingTCPServer(("", PORT), ProxyHandler) as httpd:
        print(f"Serving UI on port {PORT}")
        httpd.serve_forever()
