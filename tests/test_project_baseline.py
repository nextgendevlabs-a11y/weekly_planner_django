from django.core.management import call_command


def test_django_project_configuration_passes_system_checks():
    call_command("check")
