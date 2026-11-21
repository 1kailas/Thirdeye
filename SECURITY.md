# Security policy

## Reporting a vulnerability

Please report security issues privately through GitHub's **Report a vulnerability** button on the Security tab of this repository. Do not open a public issue for anything that exposes tokens, bypasses authentication or allows access to photos or recordings.

## Scope and design

- The phone API requires `Authorization: Bearer <PHONE_TOKEN>`, compared in constant time. The server refuses to bind a non-local address without a token of at least 24 characters.
- The browser UI, `/debug` and `/pair` only accept loopback clients with a localhost Host header.
- The Sarvam API key lives only in the server environment.
- The app talks plain HTTP, so it is intended for Tailscale or a trusted LAN only.

Known limitations: the pairing QR contains the access token, photos and recordings are kept locally in `debug/` and in the app's history until cleared, and there is no per-device token revocation yet.
