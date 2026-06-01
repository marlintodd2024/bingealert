# BingeAlert v2.2.6 - plain SMTP support + support link

A small configuration and polish release for SMTP compatibility and project
support links.

This release does not include a database migration.

---

## Added

### Plain SMTP connection mode

Email settings now support an explicit SMTP connection security mode:

- `starttls` for the common port 587 flow
- `ssl` for implicit TLS on port 465
- `none` for plain SMTP relays on ports such as 25 or 26

The first-run setup wizard and Admin Settings page both expose the new
Connection Security selector. Existing installations default to `starttls`, so
current Gmail, Outlook, SMTP2GO, and similar configurations continue to behave
as before.

For environment-based configuration:

```env
SMTP_PORT=25
SMTP_SECURITY=none
```

For `/data/config.json`, the matching key is:

```json
{
  "smtp_port": 25,
  "smtp_security": "none"
}
```

The SMTP test endpoint now uses the selected security mode too, so testing a
plain relay no longer forces STARTTLS.

### Buy Me a Coffee link

The admin dashboard, login page, and first-run setup page now include a
Buy Me a Coffee link in the footer:

```text
https://buymeacoffee.com/marlintodd
```

Each page also shows a small once-per-session toast:

```text
Enjoying BingeAlert? Buy Me a Coffee!
```

The toast links to the same support page and can be dismissed.

---

## Upgrade

```bash
cd /path/to/your/bingealert
sed -i -E 's|bingealert:2\.[0-9]+\.[0-9]+|bingealert:2.2.6|' docker-compose.yml
docker compose pull
docker compose up -d --force-recreate
```

After upgrading, open Settings -> Email & Notifications if you need to switch a
relay from STARTTLS to plain SMTP.
