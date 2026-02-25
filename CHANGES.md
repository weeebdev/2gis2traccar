# Changelog

## 2025-02-25: Token refresh support

- Added 2GIS auth refresh via `https://2gis.kz/_/auth/refresh`
- Access token comes from refresh only (not from env or URL)
- Env vars: `TWOGIS_REFRESH_TOKEN`, `TWOGIS_AUTH_REFRESH_URL`, `TWOGIS_TOKEN_FILE`
- Refresh interval derived from cookie `Max-Age`/`Expires` (refresh at 80% of lifetime)
- When `TWOGIS_REFRESH_TOKEN` is set: refresh before connect, periodic refresh by cookie expiry, persist tokens to file
- `TWOGIS_WS_URL` can omit token when using refresh; token is injected before connecting
