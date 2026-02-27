# Publishing to Docker Hub

## Step 1: Create Docker Hub Account

1. Go to [hub.docker.com](https://hub.docker.com)
2. Sign up for free account
3. Verify email

## Step 2: Create Repository

1. Click "Create Repository"
2. Name: `plex-notification-portal`
3. Description: "Intelligent notification system for Plex media servers"
4. Visibility: Public
5. Click "Create"

## Step 3: Login to Docker Hub

```bash
docker login
# Enter username and password
```

## Step 4: Build and Tag Image

```bash
# Build the image
docker build -t yourusername/plex-notification-portal:latest .

# Tag with version
docker tag yourusername/plex-notification-portal:latest yourusername/plex-notification-portal:v1.0.0
```

## Step 5: Push to Docker Hub

```bash
# Push latest
docker push yourusername/plex-notification-portal:latest

# Push version
docker push yourusername/plex-notification-portal:v1.0.0
```

## Step 6: Update Docker Hub Description

1. Go to your repository on Docker Hub
2. Click "Description" tab
3. Add installation instructions:

```markdown
# Quick Start

```bash
# Create directory
mkdir plex-notification-portal
cd plex-notification-portal

# Download docker-compose
curl -O https://raw.githubusercontent.com/yourusername/plex-notification-portal/main/docker-compose.yml

# Download .env template
curl -O https://raw.githubusercontent.com/yourusername/plex-notification-portal/main/.env.example
mv .env.example .env

# Edit configuration
nano .env

# Start
docker-compose up -d
```

Access at: http://localhost:8000

See full docs: https://github.com/yourusername/plex-notification-portal
```

## Automated Builds with GitHub Actions

The repository includes GitHub Actions workflow that automatically builds and pushes to GitHub Container Registry (ghcr.io) on every push to main.

To enable Docker Hub auto-build instead:

1. Go to repository Settings → Secrets
2. Add secrets:
   - `DOCKERHUB_USERNAME`
   - `DOCKERHUB_TOKEN`
3. Workflow will auto-publish on git tags

## Multi-Platform Builds

To support ARM devices (Raspberry Pi):

```bash
# Setup buildx
docker buildx create --name multiarch --use

# Build for multiple platforms
docker buildx build \
  --platform linux/amd64,linux/arm64,linux/arm/v7 \
  -t yourusername/plex-notification-portal:latest \
  --push .
```

## Versioning

Use semantic versioning:
- v1.0.0 - Major release
- v1.1.0 - New features
- v1.0.1 - Bug fixes

Tag releases in git:
```bash
git tag -a v1.0.0 -m "Release v1.0.0"
git push origin v1.0.0
```

## Update docker-compose.yml

Change image reference:
```yaml
services:
  portal-api:
    image: yourusername/plex-notification-portal:latest
    # Remove 'build: .' line
```
