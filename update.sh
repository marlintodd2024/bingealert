#!/bin/bash
# Update script for Plex Notification Portal

echo "🔄 Updating Plex Notification Portal..."
echo ""

# Stop and remove old containers
echo "📦 Stopping old containers..."
docker-compose down

# Remove old images (optional - uncomment to save space)
# docker rmi plex-notifier-portal-api 2>/dev/null || true

# Pull latest and rebuild
echo "🔨 Building new version..."
docker-compose build --no-cache

# Start services
echo "🚀 Starting services..."
docker-compose up -d

# Wait for services to be ready
echo "⏳ Waiting for services to start..."
sleep 5

# Show status
echo ""
echo "✅ Update complete!"
echo ""
docker-compose ps

echo ""
echo "📋 View logs with: docker-compose logs -f"
echo "🌐 Access portal at: http://localhost:8000"
