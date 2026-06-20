# BingeAlert v2.2.8 - Starlette security rollup

A dependency security release for the Starlette alerts reported after v2.2.7.

This release does not include a database migration. Restart the container after
upgrading so the updated dependency set is loaded.

---

## Security

### Starlette Dependabot alerts

Upgraded the FastAPI/Starlette/Pydantic stack to versions that resolve the new
Starlette advisories reported against `requirements.txt`:

- `fastapi` `0.115.6 -> 0.138.0`
- `starlette` `0.41.3 -> 1.3.1`
- `pydantic` `2.5.3 -> 2.13.4`
- `pydantic-settings` `2.1.0 -> 2.14.2`

The Starlette upgrade includes fixes for:

- `FileResponse` range-header denial-of-service handling
- Windows UNC path handling in `StaticFiles`
- form parser limit enforcement for `application/x-www-form-urlencoded`
- malformed or missing Host header URL construction issues
- arbitrary `HTTPEndpoint` method dispatch
- multipart form large-file denial-of-service protections

---

## Upgrade

```bash
cd /path/to/your/bingealert
sed -i -E 's|bingealert:2\.[0-9]+\.[0-9]+|bingealert:2.2.8|' docker-compose.yml
docker compose pull
docker compose up -d --force-recreate
```

After upgrading, the dashboard footer should read `v2.2.8`.
