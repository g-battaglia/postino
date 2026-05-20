"""Test-only Django settings.

Creates a temporary valid config.toml and sets POSTINO_CONFIG *before*
importing the real postino.settings module. This keeps postino.settings
strictly fail-fast while allowing the test suite to run without a
committed config.toml.
"""

import os
import tempfile
from pathlib import Path

_TEST_CONFIG_TOML = """
[server]
secret_key = "test-secret-key-not-for-production-use-at-all"
debug = true
allowed_hosts = ["localhost", "127.0.0.1", "testserver"]
timezone = "UTC"
base_url = "http://testserver"

[database]
url = "sqlite:///test_db.sqlite3"

[email]
provider = "console"
from_name = "Test"
from_email = "test@example.com"
reply_to = ""

[email.resend]
api_key = ""
webhook_signing_secret = ""

[email.smtp]
host = ""
port = 587
username = ""
password = ""
use_tls = true

[email.ses]
aws_access_key_id = ""
aws_secret_access_key = ""
aws_region = "eu-west-1"

[email.mailgun]
api_key = ""
domain = ""
webhook_signing_key = ""

[security]
unsubscribe_secret = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"

[gdpr]
require_double_optin = true
unsubscribed_retention_days = 90
email_log_retention_days = 730
enable_open_tracking = false
enable_click_tracking = false
physical_address = ""

[branding]
app_name = "Postino Test"
primary_color = "#6366f1"
logo_url = ""

[sentry]
dsn = ""
traces_sample_rate = 0.0
"""

# Set up POSTINO_CONFIG before postino.settings is imported.
if "POSTINO_CONFIG" not in os.environ:
    _tmpdir = tempfile.mkdtemp(prefix="postino_test_")
    _cfg_path = Path(_tmpdir) / "config.toml"
    _cfg_path.write_text(_TEST_CONFIG_TOML)
    os.environ["POSTINO_CONFIG"] = str(_cfg_path)

# Import everything from the real settings module.
from cli.settings import *  # noqa: E402, F401, F403
