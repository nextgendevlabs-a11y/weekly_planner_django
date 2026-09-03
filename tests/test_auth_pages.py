from html.parser import HTMLParser

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse


pytestmark = pytest.mark.django_db


class NavigationParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hrefs = []
        self.form_actions = []
        self.nav_text = []
        self._in_nav = False

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "nav":
            self._in_nav = True
        if not self._in_nav:
            return
        if tag == "a" and "href" in attributes:
            self.hrefs.append(attributes["href"])
        if tag == "form" and "action" in attributes:
            self.form_actions.append(attributes["action"])

    def handle_endtag(self, tag):
        if tag == "nav":
            self._in_nav = False

    def handle_data(self, data):
        if self._in_nav and data.strip():
            self.nav_text.append(data.strip())


def navigation_from(response):
    parser = NavigationParser()
    parser.feed(response.content.decode())
    return parser


def create_user(username="reader", password="UsablePass123!"):
    return get_user_model().objects.create_user(username=username, password=password)


def test_anonymous_home_page_remains_public(client):
    response = client.get(reverse("home"))

    assert response.status_code == 200


def test_anonymous_navigation_lists_public_destinations_only(client):
    response = client.get(reverse("home"))
    nav = navigation_from(response)

    assert set(nav.hrefs) == {
        reverse("home"),
        reverse("login"),
        reverse("signup"),
    }
    assert nav.form_actions == []
    assert "Projects" not in nav.nav_text
    assert "Sign out" not in nav.nav_text


def test_authenticated_navigation_lists_implemented_destinations_only(client):
    client.force_login(create_user())

    response = client.get(reverse("projects"))
    nav = navigation_from(response)

    assert set(nav.hrefs) == {
        reverse("home"),
        reverse("projects"),
    }
    assert nav.form_actions == [reverse("logout")]
    assert "Sign out" in nav.nav_text
    assert "Sign in" not in nav.nav_text
    assert "Create account" not in nav.nav_text


def test_signup_page_renders_server_template_and_expected_fields(client):
    response = client.get(reverse("signup"))
    content = response.content.decode()

    assert response.status_code == 200
    assert "registration/signup.html" in [template.name for template in response.templates]
    assert "base.html" in [template.name for template in response.templates]
    assert 'name="username"' in content
    assert 'name="password1"' in content
    assert 'name="password2"' in content


def test_valid_signup_creates_user_signs_in_and_redirects_to_projects(client):
    response = client.post(
        reverse("signup"),
        {
            "username": "new-user",
            "password1": "UsablePass123!",
            "password2": "UsablePass123!",
        },
    )

    assert response.status_code == 302
    assert response["Location"] == reverse("projects")
    user = get_user_model().objects.get(username="new-user")
    assert str(client.session["_auth_user_id"]) == str(user.pk)


def test_invalid_signup_shows_errors_without_creating_or_signing_in(client):
    response = client.post(
        reverse("signup"),
        {
            "username": "new-user",
            "password1": "short",
            "password2": "different",
        },
    )

    assert response.status_code == 200
    assert get_user_model().objects.filter(username="new-user").exists() is False
    assert "_auth_user_id" not in client.session
    assert "errorlist" in response.content.decode()


def test_login_page_renders_server_template(client):
    response = client.get(reverse("login"))

    assert response.status_code == 200
    assert "registration/login.html" in [template.name for template in response.templates]
    assert "base.html" in [template.name for template in response.templates]


def test_valid_login_redirects_to_projects_by_default(client):
    create_user(username="reader", password="UsablePass123!")

    response = client.post(
        reverse("login"),
        {"username": "reader", "password": "UsablePass123!"},
    )

    assert response.status_code == 302
    assert response["Location"] == reverse("projects")
    assert "_auth_user_id" in client.session


def test_login_redirects_to_next_protected_path(client):
    create_user(username="reader", password="UsablePass123!")
    protected_path = reverse("projects")

    login_page = client.get(f"{reverse('login')}?next={protected_path}")
    assert f'name="next" value="{protected_path}"' in login_page.content.decode()

    response = client.post(
        reverse("login"),
        {
            "username": "reader",
            "password": "UsablePass123!",
            "next": protected_path,
        },
    )

    assert response.status_code == 302
    assert response["Location"] == protected_path


def test_invalid_login_shows_error_and_does_not_sign_in(client):
    create_user(username="reader", password="UsablePass123!")

    response = client.post(
        reverse("login"),
        {"username": "reader", "password": "wrong-password"},
    )

    assert response.status_code == 200
    assert "_auth_user_id" not in client.session
    assert "Please enter a correct username and password" in response.content.decode()


def test_signed_in_user_can_sign_out_and_loses_protected_access(client):
    client.force_login(create_user())

    response = client.post(reverse("logout"))

    assert response.status_code == 302
    assert response["Location"] == reverse("home")
    assert "_auth_user_id" not in client.session

    protected_response = client.get(reverse("projects"))
    assert protected_response.status_code == 302
    assert protected_response["Location"] == f"{reverse('login')}?next={reverse('projects')}"


def test_projects_redirects_anonymous_visitors_to_login_with_next(client):
    response = client.get(reverse("projects"))

    assert response.status_code == 302
    assert response["Location"] == f"{reverse('login')}?next={reverse('projects')}"


def test_projects_page_renders_placeholder_for_signed_in_users(client):
    client.force_login(create_user())

    response = client.get(reverse("projects"))
    content = response.content.decode()

    assert response.status_code == 200
    assert "projects/index.html" in [template.name for template in response.templates]
    assert "base.html" in [template.name for template in response.templates]
    assert "starting place for each team retrospective" in content
    assert "Start, Stop, and Continue" in content


def test_projects_placeholder_does_not_show_later_workflow_scope(client):
    client.force_login(create_user())

    response = client.get(reverse("projects"))
    content = response.content.decode().lower()

    assert "membership" not in content
    assert "facilitator" not in content
    assert "feedback cycle" not in content
    assert "dashboard" not in content
    assert "board" not in content
    assert "meeting upload" not in content
    assert "summary" not in content
