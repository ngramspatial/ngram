# Security

Please report vulnerabilities privately to the maintainers through the
disclosure channel provided with your source distribution. Do not publish an
unpatched vulnerability.

## Deployment boundaries

- Keep all credentials in environment variables or deployment secret stores.
- Use a unique `NGRAM_AR_ENTITY_BRIDGE_TOKEN` for every public Entity bridge.
- Do not place bridge credentials in URLs; ngram sends them as bearer headers.
- Treat `.ngram` exports as sensitive personal state. The v1 container format is
  integrity-checked but is not encrypted or cryptographically signed.
- Review tool permissions before enabling autonomous wake or public messaging.
