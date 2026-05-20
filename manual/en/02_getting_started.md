# 2. Getting Started

Setting up Postino is designed to be straightforward.

## The Configuration File
Everything in Postino—from your database connection to your email provider and GDPR settings—is controlled by a single configuration file named `config.toml`. 

When you first install Postino, you will copy the provided `config.example.toml` and rename it to `config.toml`, then fill in your specific details:
- **Server secret keys**
- **Database URL**
- **Email provider details** (e.g., your Resend API key)
- **Privacy rules** (e.g., requiring double opt-in)

## Launching Postino
Once your configuration is ready, you run simple commands to initialize the database and start the server:
```bash
python manage.py migrate          # Sets up the database
python manage.py createsuperuser  # Creates your admin account
python manage.py runserver        # Starts the application
```
