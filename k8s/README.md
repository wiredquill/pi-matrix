# Matrix Update Server Kubernetes Deployment

This directory contains the Kubernetes deployment for the Matrix Update Server, which provides OTA (Over-The-Air) updates for CircuitPython matrix displays.

## Overview

The update server:
- Serves `version.txt` and `code.py` files from this GitHub repository
- Caches GitHub content for 5 minutes to reduce API calls
- Provides health checks and status endpoints
- Scales horizontally with multiple replicas
- Exposes via LoadBalancer for external access

## Architecture

```
Internet → LoadBalancer → matrix-update-server pods → GitHub API
                      ↓
CircuitPython devices fetch updates via HTTP
```

## Endpoints

- `GET /health` - Health check
- `GET /matrix/version.txt` - Current version from GitHub
- `GET /matrix/code.py` - Current code.py from GitHub  
- `GET /matrix/status` - Server status and cache info
- `GET /` - API documentation

## Fleet Deployment

This service is deployed via Fleet in the `wq-fleet` repository.

### Prerequisites

1. Fleet installed in your Kubernetes cluster
2. GitHub Container Registry access
3. LoadBalancer service support (e.g., MetalLB, cloud provider)

### Deployment Steps

1. **Copy fleet manifests to wq-fleet repo:**
   ```bash
   # In wq-fleet repository
   mkdir -p matrix-update-server
   cp /path/to/pi-matrix/k8s/fleet/* matrix-update-server/
   ```

2. **Commit and push to wq-fleet:**
   ```bash
   git add matrix-update-server/
   git commit -m "Add matrix-update-server deployment"
   git push origin main
   ```

3. **Fleet will automatically deploy to targeted clusters**

### Configuration

Environment variables in `deployment.yaml`:

- `GITHUB_REPO`: GitHub repository (default: `wiredquill/pi-matrix`)
- `GITHUB_BRANCH`: Branch to fetch from (default: `main`)
- `CACHE_DURATION`: Cache time in seconds (default: `300`)
- `PORT`: Server port (default: `8000`)

### Monitoring

Check deployment status:
```bash
kubectl get pods -n matrix-system
kubectl get svc -n matrix-system
kubectl logs -n matrix-system -l app=matrix-update-server
```

Get LoadBalancer IP:
```bash
kubectl get svc matrix-update-server -n matrix-system
```

### CircuitPython Configuration

Update your `secrets.py`:
```python
'update_server': 'http://<LOADBALANCER-IP>',  # Use the LoadBalancer IP
```

## Development

### Local Testing

1. **Build and run locally:**
   ```bash
   cd k8s/matrix-update-server
   docker build -t matrix-update-server .
   docker run -p 8000:8000 matrix-update-server
   ```

2. **Test endpoints:**
   ```bash
   curl http://localhost:8000/health
   curl http://localhost:8000/matrix/version.txt
   curl http://localhost:8000/matrix/status
   ```

### CI/CD

The GitHub Actions workflow automatically:
- Builds Docker image on changes to server code
- Pushes to GitHub Container Registry
- Tags with branch, SHA, and 'latest' for main branch

## Security

- Runs as non-root user (uid 1000)
- Read-only root filesystem
- Dropped capabilities
- Resource limits applied
- No secrets required (uses public GitHub API)

## Scaling

- Horizontal scaling: Increase `replicas` in deployment
- Vertical scaling: Adjust `resources` limits
- Cache duration can be tuned via `CACHE_DURATION` env var