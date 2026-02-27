#!/bin/bash

echo "================================================"
echo "Plex Notification Portal - Setup Script"
echo "================================================"
echo ""

# Check if Docker is installed
if ! command -v docker &> /dev/null; then
    echo "❌ Docker is not installed. Please install Docker first."
    exit 1
fi

# Check if Docker Compose is installed
if ! command -v docker-compose &> /dev/null; then
    echo "❌ Docker Compose is not installed. Please install Docker Compose first."
    exit 1
fi

echo "✅ Docker and Docker Compose are installed"
echo ""

# Check if .env exists
if [ ! -f .env ]; then
    echo "📝 Creating .env file from template..."
    cp .env.example .env
    echo "✅ .env file created"
    echo ""
    echo "⚠️  IMPORTANT: Please edit .env file with your configuration before continuing!"
    echo "   Required settings:"
    echo "   - JELLYSEERR_URL and JELLYSEERR_API_KEY"
    echo "   - SONARR_URL and SONARR_API_KEY"
    echo "   - RADARR_URL and RADARR_API_KEY"
    echo "   - SMTP settings (SMTP_HOST, SMTP_USER, SMTP_PASSWORD)"
    echo "   - DB_PASSWORD (choose a secure password)"
    echo ""
    read -p "Press Enter after you've configured .env to continue..."
else
    echo "✅ .env file already exists"
fi

echo ""
echo "🚀 Starting Plex Notification Portal..."
echo ""

# Build and start containers
docker-compose up -d --build

# Wait for services to be healthy
echo "⏳ Waiting for services to start..."
sleep 10

# Check if API is responding
if curl -s http://localhost:8000/health > /dev/null; then
    echo "✅ API is running!"
else
    echo "⚠️  API might not be ready yet. Check logs with: docker-compose logs -f api"
fi

echo ""
echo "================================================"
echo "✅ Setup Complete!"
echo "================================================"
echo ""
echo "Services:"
echo "  - Dashboard:       http://localhost:8000"
echo "  - API Docs:        http://localhost:8000/docs"
echo "  - Health Check:    http://localhost:8000/health"
echo ""
echo "Next Steps:"
echo "  1. Configure webhooks in Sonarr/Radarr"
echo "     Sonarr: http://localhost:8000/webhooks/sonarr"
echo "     Radarr: http://localhost:8000/webhooks/radarr"
echo ""
echo "  2. Manual sync (if needed):"
echo "     curl -X POST http://localhost:8000/admin/sync/users"
echo "     curl -X POST http://localhost:8000/admin/sync/requests"
echo ""
echo "  3. View logs:"
echo "     docker-compose logs -f api"
echo ""
echo "  4. Check stats:"
echo "     curl http://localhost:8000/admin/stats"
echo ""
echo "For more information, see README.md"
echo "================================================"
