# LinkForge

A small self-hosted music link-page CMS built with FastAPI, SQLite and server-rendered HTML. No frontend build or separate database service. Public pages use the charcoal/crimson glass-panel design; admins can change all colours, imagery and links.

## Included

- Any number of pages at `/r/<url-stub>`; create, edit, publish/unpublish and delete.
- Name, artist, multiline description, cover and independent full-page background.
- Background focal position, dark overlay, panel tint/opacity/blur, text and accent colours.
- Any number of ordered links, editable labels/URLs/button text, visibility and highlights.
- 37 service/custom presets, local icons, and PNG/JPEG/WebP custom icon uploads.
- Live mobile preview; previews never load tracking tags.
- Per-page Meta Pixel, Google Ads conversion and optional GA4 configuration.
- Consent UI: no marketing scripts or requests until acceptance; reject and revoke supported.
- OIDC login with exact `link_admin` role membership. All authorized users can manage all pages.
- CSRF protection, validated URLs/colours, safe raster uploads, server-side sessions and edit-conflict detection.
- Dockerfile, Compose, optional automatic HTTPS via Caddy, pinned dependency lock and tests.

## Quick server setup

Requirements: Docker Engine with Compose v2, a domain pointing to your server, and an existing OpenID Connect identity provider.

```sh
cp .env.example .env
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
```

Edit `.env`: paste the generated value into `SESSION_SECRET`, set `BASE_URL`, and enter the provider details. Keep `.env` private (`chmod 600 .env`). The application runs at the domain root, not a subpath.

Register a **confidential OIDC web client**, using authorization code flow, PKCE S256, client-secret-basic authentication, and this **exact redirect URI**:

```text
https://music.example.com/auth/callback
```

Replace the domain with your actual `BASE_URL`. Do not register wildcard redirect URIs. Enable the standard `openid profile email` scopes; add any provider-specific scope needed to emit roles. The provider must support discovery and signed ID tokens. The default role source is the verified ID token.

Configure the provider to emit a role claim containing the exact string `link_admin`, for example:

```json
{"roles": ["link_admin"]}
```

Set the corresponding claim path in `.env`:

| Provider claim shape | `OIDC_ROLE_CLAIM` |
|---|---|
| `{"roles":["link_admin"]}` | `roles` |
| `{"groups":["link_admin"]}` | `groups` |
| Keycloak realm role in `realm_access.roles` | `realm_access.roles` |
| Keycloak client role in `resource_access.linkforge.roles` | `resource_access.linkforge.roles` |

For Authentik, expose a `roles` array via a scope/property mapping, or expose an appropriately named group and set `OIDC_ROLE_CLAIM=groups`. Membership is exact: `/link_admin`, `super_link_admin`, or `Link_Admin` do not match. Map the value to `link_admin` in the provider.

If roles are only in the provider's UserInfo response, use `OIDC_ROLE_SOURCE=userinfo`. The application still validates the ID token, fetches UserInfo with the authenticated access token, and requires matching `sub` values. It deliberately does not decode an unverified access token to obtain roles.

### Option A: existing reverse proxy

```sh
docker compose up -d --build
docker compose logs -f app
```

The app binds to `127.0.0.1:8000` on the host. Forward your HTTPS virtual host to that address and preserve the original `Host` header. Set your proxy upload limit to at least 12 MiB. Example Nginx location inside your existing TLS server block:

```nginx
client_max_body_size 12m;
location / {
    proxy_pass http://127.0.0.1:8000;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
}
```

If the proxy is itself in Docker, attach it to the app network and use `app:8000`; the proxy container's localhost is not the server host. `BASE_URL` supplies the callback and canonical URLs, so arbitrary forwarded headers are not trusted by the application. Avoid logging `/auth/callback` query strings in your proxy, as they contain transient authorization codes. The supplied app disables access logs.

### Option B: standalone Caddy HTTPS

Set `DOMAIN=music.example.com` and `BASE_URL=https://music.example.com` in `.env`. Open inbound TCP 80/443 (and optionally UDP 443). Ensure no existing web server occupies these ports.

```sh
docker compose -f compose.yaml -f compose.https.yaml up -d --build
```

Caddy obtains and renews the TLS certificate. Visit `https://music.example.com/admin`, sign in, and create a page. Set `Published` when ready.

### Local development

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.lock
```

Set the variables from `.env` in your shell (or use your environment manager). For localhost, set `BASE_URL=http://localhost:8000`, register `http://localhost:8000/auth/callback` with the identity provider, and use:

```sh
uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000 --reload
```

The app does not auto-load `.env` outside Compose. There is no development login bypass. A missing OIDC configuration leaves public pages usable but prevents admin login.

## Page editing

1. Click **Create page**.
2. Set title, artist, URL stub and description; upload cover artwork.
3. In **Appearance**, upload a background and adjust colours, opacity and blur.
4. In **Links**, select a preset, add the release's actual destination URL, and customize its label/icon. Repeat as needed. The arrows reorder links.
5. Add your operator's privacy and imprint text, and optionally tracking IDs.
6. Save a draft, or select **Published** and save.

Changing the URL stub changes the public URL immediately; old stubs return 404 (no implicit aliases). Remember to update ads that use the old URL. Deleting a page removes its URL, but uploaded files remain to avoid deleting images shared by other pages. Uploaded files have unguessable names but are publicly accessible; do not upload confidential material. Unpublishing is not a private file-storage mechanism.

Uploads accept PNG, JPEG and WebP up to 10 MiB / 25 megapixels. Images are converted to WebP, resized to a maximum 2560 pixels and stripped of metadata. SVG/HTML uploads are intentionally not accepted. Use a transparent PNG/WebP for custom logos. Built-in SVG icons are trusted assets bundled with the app.

## Tracking and ad optimization

These settings are per page:

- **Meta:** Pixel ID. The page fires `PageView` after consent and the custom `StreamingLinkClick` event on the first streaming-link click per release per browser-tab session. Create a matching custom conversion in Meta Events Manager and select it for the campaign.
- **Google Ads:** conversion ID (`AW-...`) **and conversion label**. Create a website click conversion action in Google Ads; copy its ID/label into the page. Choose an appropriate counting setting (typically one for this intent signal).
- **GA4 (optional):** measurement ID (`G-...`). Sends `streaming_link_click` for each measured link click, with `release_id`, `release_slug`, `service` and `link_index`.

Meta and Google Ads use a sessionStorage marker to avoid repeated conversion dispatches for the same page in a tab session. This is best-effort browser deduplication, not cross-device person-level deduplication. Different tabs/devices, cleared storage, or blocked storage affect counting. GA4 click events intentionally remain separate from the deduplicated advertising conversion.

Normal same-tab clicks get a bounded 180 ms dispatch window after consent, then navigate regardless of tracker availability. Without consent or with JavaScript disabled, links navigate directly. Delivery is best-effort: ad blockers, network failures and in-app browsers can prevent measurement. A click is intent to listen, not proof of a stream.

The consent choice is stored for 180 days, scoped to the tracking-provider IDs. Rejection does not load tags. Revoking consent blocks future events, removes accessible first-party tracking cookies and reloads the page to unload scripts; it cannot erase historical data already sent to a provider. Privacy and imprint text must describe your actual deployment. Google uses basic consent-mode behavior. No server-side Conversions API, user fingerprinting, local click database, or tracking on admin/preview pages is implemented.

Before spending on ads, use Meta Test Events and Google's tag diagnostics with your real account IDs. Confirm acceptance/rejection behavior and link-click conversion delivery on your domain. Configure the matching conversion goals in the ad accounts; this application does not create campaigns or conversions via their APIs.

## Sessions and access

The application uses Authlib for authorization code + PKCE, state, nonce, signature, issuer, audience and expiry validation. Successful login also requires exact role membership. The browser receives only an opaque session reference in a signed, HttpOnly, SameSite=Lax cookie; OAuth tokens are not retained. HTTPS enables the Secure cookie flag. Mutations require a session-bound CSRF header.

Sessions expire at the earlier of ID-token expiry or `SESSION_SECONDS` (default one hour). Role removal at the provider takes effect at next login/session expiry; there is no back-channel logout or continuous role polling. Signing out terminates this application's session, not the provider's SSO session. For immediate revocation of every application session:

```sh
docker compose exec app python -c "import sqlite3; c=sqlite3.connect('/data/linkforge.sqlite3'); c.execute('DELETE FROM sessions'); c.commit()"
```

`SESSION_SECRET` is validated on startup. Keep it stable across restarts; rotating it signs everyone out. The app is intended as one small, single-instance deployment on a local persistent volume. For multi-replica operation or high write volume, migrate persistence/session storage to PostgreSQL.

## Data, backups and upgrades

The named `linkforge-data` volume stores `/data/linkforge.sqlite3` and `/data/uploads/`. `docker compose down` preserves it; **`docker compose down -v` destroys it**.

For a consistent portable backup including artwork, stop the app while archiving the volume:

```sh
docker compose stop app
docker compose run --rm --no-deps -T --entrypoint tar app -C /data -czf - . > linkforge-backup.tgz
docker compose start app
```

Keep the archive and `.env` securely off-server. Restore into an empty volume with the app stopped:

```sh
docker compose stop app
docker compose run --rm --no-deps -T --entrypoint tar app -C /data -xzf - < linkforge-backup.tgz
docker compose start app
```

Do not restore over a running database. Include the optional `-f compose.https.yaml` flag consistently if using the Caddy stack. Uploads are not automatically garbage-collected. SQLite schema version 1 is initialized on first start; future schema changes will require explicit migrations.

Dependencies tested for this release are in `requirements.lock`; the Dockerfile installs this lock. `requirements.txt` describes intended direct-dependency ranges. Review and update the lock with tests when upgrading. Container base images are tag-based, so pin their digests if you require byte-for-byte reproducible builds.

## Testing

```sh
pip install -r requirements-dev.txt
python -m pytest -q
```

Tests cover authorization/CSRF, real signed OIDC tokens through a mocked provider exchange, signature/issuer/audience/nonce/expiry rejection, exact role checking, drafts/publication, URL changes/conflicts, optimistic concurrency, safe rendering, uploads, preview tracking exclusion, persistence and icon availability. No live provider credentials are included.

An optional real-browser smoke test lives in `tests/browser_smoke.py` (install Playwright and its Chromium browser, then run `python tests/browser_smoke.py`). It starts a temporary isolated server/database, tests the editor and consent/tracking behavior with advertising network requests intercepted, and saves local QA screenshots. It does not contact advertising accounts.

Validation in the creation environment: backend tests and Chromium browser smoke passed. Docker was unavailable, so the image was not built here. Actual provider and ad-account integration must be verified with your credentials on your domain.

## Service presets and icon credits

The catalogue follows DistroKid's published store list, with additional KKBOX (requested), Bandcamp, SoundCloud and YouTube presets. Some distribution destinations do not offer a consumer-facing release URL; use them only when you have a working link. A preset does not assert that your distributor delivered your release there. DistroKid does not currently list KKBOX on the referenced page.

Source: https://support.distrokid.com/hc/en-us/articles/360013649093-Which-Services-and-Stores-Does-DistroKid-Send-Music-To (checked 2026-10-16).

Most recognizable marks are bundled from Simple Icons (CC0). `app/static/icons/sources.json` records which are available. Services absent from the icon set use a clearly named letter badge; replace it with a custom uploaded logo if desired. Names/logos remain trademarks of their owners. The logo assets do not imply endorsement. See `THIRD_PARTY_NOTICES.md`.

Useful upstream docs:
- https://docs.authlib.org/en/latest/oauth2/client/web/
- https://developers.google.com/tag-platform/security/guides/consent?consentmode=basic
- https://support.google.com/google-ads/answer/7548399
