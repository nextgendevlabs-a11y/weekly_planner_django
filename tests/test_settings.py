import importlib

import pytest
from django.core.exceptions import ImproperlyConfigured

from weekly_planner import settings as project_settings


ENV_SETTING_NAMES = [
    "DATABASE_URL",
    "DJANGO_ALLOWED_HOSTS",
    "DJANGO_DATA_UPLOAD_MAX_MEMORY_SIZE",
    "DJANGO_DEBUG",
    "DJANGO_FILE_UPLOAD_MAX_MEMORY_SIZE",
    "DJANGO_SECRET_KEY",
]


@pytest.fixture(autouse=True)
def restore_project_settings(monkeypatch):
    yield
    for name in ENV_SETTING_NAMES:
        monkeypatch.delenv(name, raising=False)
    importlib.reload(project_settings)


def reload_project_settings(monkeypatch, **env):
    for name in ENV_SETTING_NAMES:
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    return importlib.reload(project_settings)


def test_default_settings_support_local_development_without_postgresql(monkeypatch):
    settings_module = reload_project_settings(monkeypatch)

    assert settings_module.SECRET_KEY == "development-insecure-secret-key"
    assert settings_module.DEBUG is True
    assert settings_module.ALLOWED_HOSTS == ["localhost", "127.0.0.1"]
    assert settings_module.DATABASES["default"] == {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": settings_module.BASE_DIR / "db.sqlite3",
    }


def test_environment_variables_configure_core_settings(monkeypatch):
    settings_module = reload_project_settings(
        monkeypatch,
        DJANGO_SECRET_KEY="configured-secret",
        DJANGO_DEBUG="false",
        DJANGO_ALLOWED_HOSTS="example.test, .example.test",
    )

    assert settings_module.SECRET_KEY == "configured-secret"
    assert settings_module.DEBUG is False
    assert settings_module.ALLOWED_HOSTS == ["example.test", ".example.test"]


def test_database_url_can_configure_postgresql_with_query_options(monkeypatch):
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://planner:secret@db.example.test:5432/weekly_planner"
        "?sslmode=require&connect_timeout=10",
    )

    config = project_settings.database_config()

    assert config == {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": "weekly_planner",
        "USER": "planner",
        "PASSWORD": "secret",
        "HOST": "db.example.test",
        "PORT": "5432",
        "OPTIONS": {"sslmode": "require", "connect_timeout": "10"},
    }


def test_database_url_can_configure_sqlite(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///local-dev.sqlite3")

    config = project_settings.database_config()

    assert config == {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": "local-dev.sqlite3",
    }


def test_database_url_rejects_unsupported_schemes(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "mysql://planner:secret@db/weekly_planner")

    with pytest.raises(ImproperlyConfigured, match="Unsupported DATABASE_URL scheme"):
        project_settings.database_config()


def test_static_and_media_settings_are_stable_local_paths():
    assert project_settings.STATIC_URL == "/static/"
    assert project_settings.STATIC_ROOT == project_settings.BASE_DIR / "staticfiles"
    assert project_settings.MEDIA_URL == "/media/"
    assert project_settings.MEDIA_ROOT == project_settings.BASE_DIR / "media"


def test_upload_limits_have_safe_local_defaults(monkeypatch):
    settings_module = reload_project_settings(monkeypatch)

    assert settings_module.DATA_UPLOAD_MAX_MEMORY_SIZE == 2_621_440
    assert settings_module.FILE_UPLOAD_MAX_MEMORY_SIZE == 2_621_440


def test_upload_limits_are_configurable(monkeypatch):
    settings_module = reload_project_settings(
        monkeypatch,
        DJANGO_DATA_UPLOAD_MAX_MEMORY_SIZE="1048576",
        DJANGO_FILE_UPLOAD_MAX_MEMORY_SIZE="2097152",
    )

    assert settings_module.DATA_UPLOAD_MAX_MEMORY_SIZE == 1_048_576
    assert settings_module.FILE_UPLOAD_MAX_MEMORY_SIZE == 2_097_152


@pytest.mark.parametrize(
    ("setting_name", "setting_value"),
    [
        ("DJANGO_DATA_UPLOAD_MAX_MEMORY_SIZE", "0"),
        ("DJANGO_FILE_UPLOAD_MAX_MEMORY_SIZE", "-1"),
        ("DJANGO_FILE_UPLOAD_MAX_MEMORY_SIZE", "not-a-number"),
    ],
)
def test_upload_limits_reject_invalid_values(monkeypatch, setting_name, setting_value):
    for name in ENV_SETTING_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(setting_name, setting_value)

    with pytest.raises(ImproperlyConfigured, match=f"{setting_name} must be"):
        importlib.reload(project_settings)
