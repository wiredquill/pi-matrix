#!/usr/bin/env python3
"""
Matrix Update Server
Serves version.txt and code.py files for CircuitPython OTA updates
Fetches latest files from GitHub repository
"""

import os
import logging
import requests
from flask import Flask, Response, abort
from datetime import datetime, timedelta

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuration
GITHUB_REPO = os.environ.get('GITHUB_REPO', 'wiredquill/pi-matrix')
GITHUB_BRANCH = os.environ.get('GITHUB_BRANCH', 'main')
CACHE_DURATION = int(os.environ.get('CACHE_DURATION', '300'))  # 5 minutes default
VERSION_FILE = os.environ.get('VERSION_FILE', 'VERSION')

# Cache for GitHub content
cache = {}

def get_github_content(path):
    """Fetch content from GitHub with caching"""
    cache_key = f"{GITHUB_REPO}/{GITHUB_BRANCH}/{path}"
    now = datetime.now()
    
    # Check cache
    if cache_key in cache:
        cached_time, content = cache[cache_key]
        if now - cached_time < timedelta(seconds=CACHE_DURATION):
            logger.info(f"Serving {path} from cache")
            return content
    
    # Fetch from GitHub
    url = f"https://raw.githubusercontent.com/{GITHUB_REPO}/{GITHUB_BRANCH}/{path}"
    try:
        logger.info(f"Fetching {path} from GitHub: {url}")
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        
        content = response.text
        cache[cache_key] = (now, content)
        logger.info(f"Cached {path} ({len(content)} bytes)")
        return content
        
    except requests.RequestException as e:
        logger.error(f"Failed to fetch {path} from GitHub: {e}")
        
        # Return cached version if available
        if cache_key in cache:
            logger.warning(f"Returning stale cached version of {path}")
            return cache[cache_key][1]
        
        return None

@app.route('/health')
def health_check():
    """Health check endpoint"""
    return {'status': 'healthy', 'timestamp': datetime.now().isoformat()}

@app.route('/matrix/version.txt')
def get_version():
    """Return current version from GitHub"""
    content = get_github_content(VERSION_FILE)
    if content is None:
        abort(503, description="Unable to fetch version from GitHub")
    
    # Clean up version string
    version = content.strip()
    logger.info(f"Serving version: {version}")
    
    return Response(version, mimetype='text/plain')

@app.route('/matrix/code.py')
def get_code():
    """Return current code.py from GitHub"""
    content = get_github_content('code/code.py')
    if content is None:
        abort(503, description="Unable to fetch code.py from GitHub")
    
    logger.info(f"Serving code.py ({len(content)} bytes)")
    
    return Response(content, 
                   mimetype='text/plain',
                   headers={'Content-Disposition': 'attachment; filename=code.py'})

@app.route('/matrix/status')
def get_status():
    """Return server status and cache info"""
    status = {
        'server': 'matrix-update-server',
        'github_repo': GITHUB_REPO,
        'github_branch': GITHUB_BRANCH,
        'cache_duration': CACHE_DURATION,
        'cached_files': len(cache),
        'timestamp': datetime.now().isoformat()
    }
    
    # Add cache details
    cache_info = {}
    for key, (cached_time, content) in cache.items():
        age = (datetime.now() - cached_time).total_seconds()
        cache_info[key] = {
            'age_seconds': int(age),
            'size_bytes': len(content),
            'cached_at': cached_time.isoformat()
        }
    
    status['cache'] = cache_info
    return status

@app.route('/')
def index():
    """Root endpoint with usage information"""
    return {
        'service': 'Matrix Update Server',
        'endpoints': {
            '/health': 'Health check',
            '/matrix/version.txt': 'Current version',
            '/matrix/code.py': 'Current code',
            '/matrix/status': 'Server status'
        },
        'github_repo': GITHUB_REPO,
        'github_branch': GITHUB_BRANCH
    }

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8000))
    app.run(host='0.0.0.0', port=port, debug=False)