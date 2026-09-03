import importlib
from html.parser import HTMLParser

from django.contrib import messages
from django.contrib.auth.views import LoginView, LogoutView
from django.test import override_settings
from django.urls import path, reverse

from weekly_planner import urls as project_urls
from weekly_planner.views import HomeView, ProjectsView, SignUpView


def message_home_view(request):
    messages.success(request, "Cycle ready")
    return HomeView.as_view()(request)


urlpatterns = [
    path("", HomeView.as_view(), name="home"),
    path("accounts/signup/", SignUpView.as_view(), name="signup"),
    path(
        "accounts/login/",
        LoginView.as_view(template_name="registration/login.html"),
        name="login",
    ),
    path("accounts/logout/", LogoutView.as_view(), name="logout"),
    path("projects/", ProjectsView.as_view(), name="projects"),
    path("message-home/", message_home_view, name="message_home"),
]


class LinkParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hrefs = []

    def handle_starttag(self, tag, attrs):
        if tag != "a":
            return

        attributes = dict(attrs)
        if "href" in attributes:
            self.hrefs.append(attributes["href"])


def test_home_page_is_public_and_renders_template(client):
    response = client.get(reverse("home"))

    assert response.status_code == 200
    assert "home.html" in [template.name for template in response.templates]
    assert "base.html" in [template.name for template in response.templates]


def test_home_page_uses_shared_shell_and_retrospective_copy(client):
    response = client.get("/")
    content = response.content.decode()

    assert "<header" in content
    assert "<main" in content
    assert "Start, Stop, and Continue" in content
    assert "cluster themes" in content
    assert "action items" in content
    assert "employee" not in content.lower()
    assert "scoring" not in content.lower()
    assert "project-management" not in content.lower()
    assert "meeting-recording" not in content.lower()


def test_navigation_links_only_to_implemented_destinations(client):
    response = client.get("/")
    parser = LinkParser()
    parser.feed(response.content.decode())

    assert parser.hrefs
    assert set(parser.hrefs) == {"/", "/accounts/login/", "/accounts/signup/"}


@override_settings(ROOT_URLCONF=__name__)
def test_shared_shell_renders_django_messages(client):
    response = client.get("/message-home/")
    content = response.content.decode()

    assert 'role="status"' in content
    assert "Cycle ready" in content


def test_development_server_serves_media_only_when_debug_is_true(settings, tmp_path):
    with override_settings(DEBUG=True, MEDIA_URL="/media/", MEDIA_ROOT=tmp_path):
        debug_urls = importlib.reload(project_urls).urlpatterns

    with override_settings(DEBUG=False, MEDIA_URL="/media/", MEDIA_ROOT=tmp_path):
        production_urls = importlib.reload(project_urls).urlpatterns

    importlib.reload(project_urls)

    debug_patterns = [str(pattern.pattern) for pattern in debug_urls]
    production_patterns = [str(pattern.pattern) for pattern in production_urls]

    assert any("media/" in pattern for pattern in debug_patterns)
    assert not any("media/" in pattern for pattern in production_patterns)
