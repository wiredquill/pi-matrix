#!/usr/bin/env python3
"""
Simple test update server for Matrix displays
Serves version.txt and code.py from local files
"""

from http.server import HTTPServer, BaseHTTPRequestHandler
import os

class UpdateHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/matrix/version.txt':
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain')
            self.end_headers()
            
            # Read version from file or return test version
            try:
                with open('VERSION', 'r') as f:
                    version = f.read().strip()
            except FileNotFoundError:
                version = "1.0.1"  # Test version higher than current
            
            self.wfile.write(version.encode())
            print(f"Served version: {version}")
            
        elif self.path == '/matrix/code.py':
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain')
            self.send_header('Content-Disposition', 'attachment; filename=code.py')
            self.end_headers()
            
            # Serve current code.py
            try:
                with open('code/code.py', 'r') as f:
                    code = f.read()
                self.wfile.write(code.encode())
                print(f"Served code.py ({len(code)} bytes)")
            except FileNotFoundError:
                self.send_error(404, "code.py not found")
                
        elif self.path == '/health' or self.path == '/':
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            response = '{"status": "healthy", "server": "test-update-server"}'
            self.wfile.write(response.encode())
            
        else:
            self.send_error(404, "Not found")

if __name__ == '__main__':
    port = 8000
    server = HTTPServer(('0.0.0.0', port), UpdateHandler)
    print(f"Test update server running on http://0.0.0.0:{port}")
    print("Endpoints:")
    print("  GET /health")
    print("  GET /matrix/version.txt") 
    print("  GET /matrix/code.py")
    print("\nPress Ctrl+C to stop")
    
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down server...")
        server.shutdown()