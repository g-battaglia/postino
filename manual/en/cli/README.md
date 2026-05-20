# Postino CLI Manual

For developers and system administrators who prefer the terminal, Postino includes a powerful Command Line Interface (`postino <command>`).

Every core operation you can perform in the web dashboard can also be done via the terminal. This is perfect for writing automation scripts or integrating Postino with your main backend.

## Common Commands

- `postino version`: Check the current installed version.
- `postino config validate`: Verify that your `config.toml` is correct.

### Subscriber Management
- `postino subscribers list --json`: View all your subscribers (outputs in JSON format for easy parsing).
- `postino subscribers health --below 30`: Find all users with a health score below 30.
- `postino sync run`: Synchronize your external application's user base with Postino.

### Campaigns & Analytics
- `postino campaigns list`: See your active and past campaigns.
- `postino analytics overview --days 30 --json`: Get your audience growth and engagement stats over the last 30 days.

### GDPR Audits
- `postino gdpr audit user@example.com`: Instantly pull up the full, append-only consent and unsubscribe audit log for a specific user.

## Background Tasks
You will also use Django management commands to run background jobs via Cron:
```bash
python manage.py check_scheduled_campaigns
python manage.py evaluate_sequences
python manage.py compute_health_scores
python manage.py sync_source
```
