from html.parser import HTMLParser

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from projects.models import Membership, Project


pytestmark = pytest.mark.django_db


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


def create_user(username="member", *, is_active=True):
    return get_user_model().objects.create_user(
        username=username,
        password="UsablePass123!",
        is_active=is_active,
    )


def create_project(name="Weekly Ops"):
    return Project.objects.create(name=name)


def add_membership(user, project, role=Membership.Role.TEAM_MEMBER):
    return Membership.objects.create(user=user, project=project, role=role)


def links_from(response):
    parser = LinkParser()
    parser.feed(response.content.decode())
    return parser.hrefs


def test_projects_list_redirects_anonymous_visitors_with_next(client):
    response = client.get(reverse("projects"))

    assert response.status_code == 302
    assert response["Location"] == f"{reverse('login')}?next={reverse('projects')}"


def test_projects_list_is_empty_for_user_without_memberships(client):
    client.force_login(create_user())

    response = client.get(reverse("projects"))
    content = response.content.decode()

    assert response.status_code == 200
    assert "projects/index.html" in [template.name for template in response.templates]
    assert "base.html" in [template.name for template in response.templates]
    assert "Projects appear here after you are added to a retrospective team." in content


def test_projects_list_shows_only_projects_viewable_by_signed_in_user(client):
    user = create_user(username="viewer")
    other_user = create_user(username="other")
    member_project = create_project("Member Retrospective")
    facilitator_project = create_project("Facilitator Retrospective")
    other_project = create_project("Other Team Retrospective")
    add_membership(user, member_project)
    add_membership(user, facilitator_project, Membership.Role.FACILITATOR)
    add_membership(other_user, other_project)
    client.force_login(user)

    response = client.get(reverse("projects"))
    content = response.content.decode()

    assert response.status_code == 200
    assert "Member Retrospective" in content
    assert "Facilitator Retrospective" in content
    assert "Other Team Retrospective" not in content
    assert reverse("project_dashboard", kwargs={"project_id": member_project.pk}) in links_from(
        response
    )
    assert reverse(
        "project_dashboard",
        kwargs={"project_id": facilitator_project.pk},
    ) in links_from(response)


def test_inactive_user_sees_no_projects_in_list(client):
    inactive_user = create_user(username="inactive", is_active=False)
    project = create_project("Inactive Member Retrospective")
    add_membership(inactive_user, project)
    client.force_login(inactive_user)

    response = client.get(reverse("projects"))

    assert response.status_code == 302
    assert response["Location"] == f"{reverse('login')}?next={reverse('projects')}"


def test_project_dashboard_redirects_anonymous_visitors_with_next(client):
    project = create_project()
    dashboard_path = reverse("project_dashboard", kwargs={"project_id": project.pk})

    response = client.get(dashboard_path)

    assert response.status_code == 302
    assert response["Location"] == f"{reverse('login')}?next={dashboard_path}"


@pytest.mark.parametrize(
    "role",
    [Membership.Role.TEAM_MEMBER, Membership.Role.FACILITATOR],
)
def test_project_dashboard_is_visible_to_project_members(client, role):
    user = create_user(username=f"viewer-{role}")
    project = create_project("Platform Retrospective")
    add_membership(user, project, role)
    client.force_login(user)

    response = client.get(reverse("project_dashboard", kwargs={"project_id": project.pk}))

    assert response.status_code == 200
    assert "projects/dashboard.html" in [template.name for template in response.templates]
    assert "base.html" in [template.name for template in response.templates]
    assert "<h1 id=\"project-heading\">Platform Retrospective</h1>" in response.content.decode()


def test_project_dashboard_returns_404_for_non_member_without_revealing_name(client):
    user = create_user(username="viewer")
    project = create_project("Confidential Retrospective")
    client.force_login(user)

    response = client.get(reverse("project_dashboard", kwargs={"project_id": project.pk}))

    assert response.status_code == 404
    assert "Confidential Retrospective" not in response.content.decode()


def test_inactive_user_cannot_open_dashboard_through_membership(client):
    inactive_user = create_user(username="inactive", is_active=False)
    project = create_project("Inactive Member Retrospective")
    add_membership(inactive_user, project)
    client.force_login(inactive_user)

    response = client.get(reverse("project_dashboard", kwargs={"project_id": project.pk}))

    assert response.status_code == 302
    assert response["Location"] == (
        f"{reverse('login')}?next="
        f"{reverse('project_dashboard', kwargs={'project_id': project.pk})}"
    )


def test_project_dashboard_renders_foundation_empty_sections(client):
    user = create_user()
    project = create_project("Weekly Product Retrospective")
    add_membership(user, project)
    client.force_login(user)

    response = client.get(reverse("project_dashboard", kwargs={"project_id": project.pk}))
    content = response.content.decode()

    assert "Current feedback cycle" in content
    assert "No feedback cycle has been started for this project yet." in content
    assert "Your submission status" in content
    assert "There is no open Start, Stop, and Continue submission yet." in content
    assert "Retrospective" in content
    assert "No retrospective is ready to open yet." in content
    assert "Previous retrospectives" in content
    assert "No completed retrospectives yet." in content
    assert "Open action items" in content
    assert "No open action items yet." in content


def test_project_dashboard_has_no_links_to_unimplemented_retrospective_routes(client):
    user = create_user()
    project = create_project()
    add_membership(user, project)
    client.force_login(user)

    response = client.get(reverse("project_dashboard", kwargs={"project_id": project.pk}))

    assert set(links_from(response)) == {reverse("home"), reverse("projects")}


def test_project_dashboard_does_not_show_later_workflow_controls(client):
    user = create_user()
    project = create_project()
    add_membership(user, project)
    client.force_login(user)

    response = client.get(reverse("project_dashboard", kwargs={"project_id": project.pk}))
    content = response.content.decode().lower()

    assert "feedback cards" not in content
    assert "cycle creation" not in content
    assert "reveal controls" not in content
    assert "clustering controls" not in content
    assert "voting controls" not in content
    assert "discussion controls" not in content
    assert "meeting upload" not in content
    assert "extracted outcomes" not in content
    assert "published summaries" not in content
    assert "facilitator-only" not in content
