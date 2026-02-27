# Docker Restart Setup

For the "🔄 Restart" button to work, the container needs access to the Docker socket.

## Add to your docker-compose.yml:

```yaml
services:
  plex-notification-portal:
    # ... other config ...
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro  # Add this line
      - ./data:/data
```

## Or if using docker run:

```bash
docker run -d \
  -v /var/run/docker.sock:/var/run/docker.sock:ro \
  # ... other options ...
  your-image
```

## Security Note:

Mounting the Docker socket gives the container access to Docker on the host. This is required for the restart functionality but does increase the container's privileges. The socket is mounted read-only (`:ro`) for safety, but the container can still send restart commands.

## Alternative:

If you don't want to mount the Docker socket, you can:
1. Manually restart the container after saving settings
2. Use a docker restart command from the host: `docker restart container-name`

The config will still save properly without the socket - you just need to restart manually.
