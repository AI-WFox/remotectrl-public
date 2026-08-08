"""Safe, repository-local runtime configuration for backend tests."""

import os


os.environ.setdefault("REMOTECTRL_SECRET_KEY", "test-only-strong-secret-for-backend-tests")
os.environ.setdefault("REMOTECTRL_ADMIN_EMAIL", "qa-admin@example.invalid")
os.environ.setdefault("REMOTECTRL_ADMIN_PASSWORD", "test-only-strong-password")