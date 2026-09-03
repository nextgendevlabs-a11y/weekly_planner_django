from django.core.management import call_command
from django.conf import settings

from weekly_planner import settings as project_settings


def test_django_project_configuration_passes_system_checks():
    call_command("check")


def test_default_database_does_not_require_postgresql(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)

    config = project_settings.database_config()

    assert config["ENGINE"] == "django.db.backends.sqlite3"
    assert config["NAME"] == project_settings.BASE_DIR / "db.sqlite3"


def test_database_url_can_configure_postgresql_without_connecting(monkeypatch):
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgres://planner:secret@db.example.test:5432/weekly_planner?sslmode=require",
    )

    config = project_settings.database_config()

    assert config == {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": "weekly_planner",
        "USER": "planner",
        "PASSWORD": "secret",
        "HOST": "db.example.test",
        "PORT": "5432",
        "OPTIONS": {"sslmode": "require"},
    }


def test_templates_are_project_and_app_compatible():
    template_config = settings.TEMPLATES[0]

    assert project_settings.BASE_DIR / "templates" in template_config["DIRS"]
    assert template_config["APP_DIRS"] is True
    assert (
        "django.template.context_processors.request"
        in template_config["OPTIONS"]["context_processors"]
    )
