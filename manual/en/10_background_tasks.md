# 10. Background Tasks & Cron Jobs

Postino deliberately avoids complex, memory-heavy task queues like Redis or Celery. Instead, it relies on simple **Cron Jobs** (scheduled tasks built into your server's operating system).

## How It Works
You set up simple instructions on your server to run Postino's background commands at regular intervals (e.g., every 5 minutes, or every night at midnight).

## Key Background Tasks
Here are the main commands that run in the background:
- `python manage.py check_scheduled_campaigns`: Checks if any campaigns are scheduled for right now and sends them.
- `python manage.py evaluate_sequences`: Checks if any subscribers are due for the next step in their automated drip sequence.
- `python manage.py compute_health_scores`: Updates the engagement scores for your audience.
- `python manage.py sync_source`: Pulls in new users from your external database or application.

This simple architecture makes Postino incredibly robust and easy to host on a tiny, inexpensive server.
