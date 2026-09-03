import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import timezone

from projects.models import FeedbackCycle, Membership, Project


pytestmark = pytest.mark.django_db


def create_user(username="member", *, is_active=True, is_staff=False, is_superuser=False):
    return get_user_model().objects.create_user(
        username=username,
        password="UsablePass123!",
        is_active=is_active,
        is_staff=is_staff,
        is_superuser=is_superuser,
    )


def create_project(name="Weekly Ops"):
    return Project.objects.create(name=name)


def add_membership(user, project, role=Membership.Role.TEAM_MEMBER):
    return Membership.objects.create(user=user, project=project, role=role)


def create_cycle(
    project,
    facilitator,
    *,
    label="Week 34 Retrospective",
    status=FeedbackCycle.Status.COLLECTING_FEEDBACK,
    opens_at=None,
    closes_at=None,
):
    return FeedbackCycle.objects.create(
        project=project,
        facilitator=facilitator,
        label=label,
        status=status,
        opens_at=opens_at or timezone.now(),
        closes_at=closes_at,
    )


def test_feedback_cycle_has_expected_fields_defaults_and_relationships():
    facilitator = create_user(username="facilitator")
    project = create_project("Platform Retrospective")
    opens_at = timezone.now()

    cycle = create_cycle(project, facilitator, opens_at=opens_at)

    assert cycle.project == project
    assert cycle.facilitator == facilitator
    assert cycle.label == "Week 34 Retrospective"
    assert cycle.status == FeedbackCycle.Status.COLLECTING_FEEDBACK
    assert cycle.opens_at == opens_at
    assert cycle.closes_at is None
    assert cycle.created_at is not None
    assert cycle.updated_at is not None
    assert str(cycle) == "Week 34 Retrospective (Platform Retrospective)"


def test_feedback_cycle_status_is_limited_to_mvp_lifecycle_values():
    facilitator = create_user(username="facilitator")
    project = create_project()
    cycle = FeedbackCycle(
        project=project,
        facilitator=facilitator,
        label="Week 34",
        status="draft",
        opens_at=timezone.now(),
    )

    with pytest.raises(ValidationError):
        cycle.full_clean()

    with pytest.raises(IntegrityError), transaction.atomic():
        create_cycle(project, facilitator, status="draft")


def test_feedback_cycle_rejects_closing_time_before_opening_time():
    facilitator = create_user(username="facilitator")
    project = create_project()
    opens_at = timezone.now()
    cycle = FeedbackCycle(
        project=project,
        facilitator=facilitator,
        label="Week 34",
        opens_at=opens_at,
        closes_at=opens_at - timezone.timedelta(hours=1),
    )

    with pytest.raises(ValidationError) as error:
        cycle.full_clean()

    assert "closes_at" in error.value.message_dict
    assert "Closing time cannot be earlier than the opening time." in error.value.message_dict[
        "closes_at"
    ]

    with pytest.raises(IntegrityError), transaction.atomic():
        create_cycle(
            project,
            facilitator,
            opens_at=opens_at,
            closes_at=opens_at - timezone.timedelta(hours=1),
        )


def test_only_one_active_feedback_cycle_can_exist_per_project():
    facilitator = create_user(username="facilitator")
    project = create_project()
    create_cycle(project, facilitator)

    with pytest.raises(IntegrityError), transaction.atomic():
        create_cycle(project, facilitator, label="Second active cycle")


def test_completed_feedback_cycle_does_not_block_later_cycle_for_same_project():
    facilitator = create_user(username="facilitator")
    project = create_project()
    create_cycle(
        project,
        facilitator,
        label="Completed cycle",
        status=FeedbackCycle.Status.COMPLETED,
    )

    later_cycle = create_cycle(project, facilitator, label="Next cycle")

    assert later_cycle.status == FeedbackCycle.Status.COLLECTING_FEEDBACK
    assert FeedbackCycle.objects.filter(project=project).count() == 2


def test_deleting_project_removes_feedback_cycles():
    facilitator = create_user(username="facilitator")
    project = create_project()
    create_cycle(project, facilitator)

    project.delete()

    assert FeedbackCycle.objects.count() == 0


def test_create_feedback_cycle_redirects_anonymous_visitors_with_next(client):
    project = create_project()
    create_path = reverse("feedback_cycle_create", kwargs={"project_id": project.pk})

    response = client.get(create_path)

    assert response.status_code == 302
    assert response["Location"] == f"{reverse('login')}?next={create_path}"


def test_create_feedback_cycle_page_is_visible_to_project_facilitators(client):
    facilitator = create_user(username="facilitator")
    project = create_project("Platform Retrospective")
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    client.force_login(facilitator)

    response = client.get(reverse("feedback_cycle_create", kwargs={"project_id": project.pk}))
    content = response.content.decode()

    assert response.status_code == 200
    assert "projects/feedback_cycle_form.html" in [
        template.name for template in response.templates
    ]
    assert "base.html" in [template.name for template in response.templates]
    assert 'name="label"' in content
    assert 'name="opens_at"' in content
    assert 'name="closes_at"' in content
    assert 'name="project"' not in content
    assert 'name="facilitator"' not in content
    assert 'name="status"' not in content
    assert "Platform Retrospective" in content


def test_create_feedback_cycle_returns_404_for_non_member_without_revealing_name(client):
    user = create_user(username="outsider")
    project = create_project("Confidential Retrospective")
    client.force_login(user)

    response = client.get(reverse("feedback_cycle_create", kwargs={"project_id": project.pk}))

    assert response.status_code == 404
    assert "Confidential Retrospective" not in response.content.decode()


def test_create_feedback_cycle_is_not_available_to_team_members(client):
    member = create_user(username="member")
    project = create_project("Team Member Retrospective")
    add_membership(member, project)
    client.force_login(member)

    response = client.get(reverse("feedback_cycle_create", kwargs={"project_id": project.pk}))

    assert response.status_code == 404
    assert "Team Member Retrospective" not in response.content.decode()


def test_staff_or_superuser_without_facilitator_membership_cannot_create_cycle(client):
    superuser = create_user(username="admin", is_staff=True, is_superuser=True)
    project = create_project("Admin Retrospective")
    client.force_login(superuser)

    response = client.get(reverse("feedback_cycle_create", kwargs={"project_id": project.pk}))

    assert response.status_code == 404
    assert "Admin Retrospective" not in response.content.decode()


def test_valid_cycle_submission_creates_cycle_and_redirects_to_dashboard(client):
    facilitator = create_user(username="facilitator")
    other_facilitator = create_user(username="other-facilitator")
    project = create_project()
    other_project = create_project("Other Retrospective")
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    client.force_login(facilitator)

    response = client.post(
        reverse("feedback_cycle_create", kwargs={"project_id": project.pk}),
        {
            "label": "Week 34 Retrospective",
            "opens_at": "2026-09-03T09:00",
            "closes_at": "2026-09-05T17:00",
            "project": str(other_project.pk),
            "facilitator": str(other_facilitator.pk),
            "status": FeedbackCycle.Status.COMPLETED,
        },
    )

    assert response.status_code == 302
    assert response["Location"] == reverse(
        "project_dashboard", kwargs={"project_id": project.pk}
    )
    cycle = FeedbackCycle.objects.get()
    assert cycle.project == project
    assert cycle.facilitator == facilitator
    assert cycle.label == "Week 34 Retrospective"
    assert cycle.status == FeedbackCycle.Status.COLLECTING_FEEDBACK


def test_invalid_cycle_submission_shows_errors_without_creating_cycle(client):
    facilitator = create_user(username="facilitator")
    project = create_project()
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    client.force_login(facilitator)

    response = client.post(
        reverse("feedback_cycle_create", kwargs={"project_id": project.pk}),
        {
            "label": "Week 34 Retrospective",
            "opens_at": "2026-09-05T17:00",
            "closes_at": "2026-09-03T09:00",
        },
    )

    assert response.status_code == 200
    assert FeedbackCycle.objects.count() == 0
    assert "errorlist" in response.content.decode()
    assert "Closing time cannot be earlier than the opening time." in response.content.decode()


def test_existing_active_cycle_prevents_second_cycle_through_form(client):
    facilitator = create_user(username="facilitator")
    project = create_project()
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    create_cycle(project, facilitator)
    client.force_login(facilitator)

    response = client.post(
        reverse("feedback_cycle_create", kwargs={"project_id": project.pk}),
        {
            "label": "Second active cycle",
            "opens_at": "2026-09-10T09:00",
            "closes_at": "",
        },
    )

    assert response.status_code == 200
    assert FeedbackCycle.objects.filter(project=project).count() == 1
    assert "This project already has an active feedback cycle." in response.content.decode()


def test_project_dashboard_cycle_state_for_facilitators_and_members(client):
    facilitator = create_user(username="facilitator")
    member = create_user(username="member")
    project = create_project()
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    add_membership(member, project)

    client.force_login(facilitator)
    empty_response = client.get(reverse("project_dashboard", kwargs={"project_id": project.pk}))
    empty_content = empty_response.content.decode()

    assert "No feedback cycle has been started for this project yet." in empty_content
    assert reverse("feedback_cycle_create", kwargs={"project_id": project.pk}) in empty_content
    assert "Create feedback cycle" in empty_content

    client.force_login(member)
    member_empty_response = client.get(
        reverse("project_dashboard", kwargs={"project_id": project.pk})
    )
    member_empty_content = member_empty_response.content.decode()

    assert "No feedback cycle has been started for this project yet." in member_empty_content
    assert "Create feedback cycle" not in member_empty_content
    assert (
        reverse("feedback_cycle_create", kwargs={"project_id": project.pk})
        not in member_empty_content
    )

    create_cycle(project, facilitator, label="Week 34 Retrospective")

    client.force_login(facilitator)
    active_response = client.get(reverse("project_dashboard", kwargs={"project_id": project.pk}))
    active_content = active_response.content.decode()

    assert "Week 34 Retrospective" in active_content
    assert "Collecting feedback" in active_content
    assert reverse("feedback_cycle_create", kwargs={"project_id": project.pk}) not in active_content
    assert "Not submitted yet for Week 34 Retrospective." in active_content
    assert (
        reverse(
            "feedback_submission",
            kwargs={"project_id": project.pk, "cycle_id": project.feedback_cycles.get().pk},
        )
        in active_content
    )
    assert "No retrospective is ready to open yet." in active_content
    assert "reveal" not in active_content.lower()
    assert "clustering" not in active_content.lower()
    assert "voting" not in active_content.lower()
    assert "discussion" not in active_content.lower()

    client.force_login(member)
    member_response = client.get(reverse("project_dashboard", kwargs={"project_id": project.pk}))
    member_content = member_response.content.decode()

    assert "Week 34 Retrospective" in member_content
    assert "Collecting feedback" in member_content
    assert "Create feedback cycle" not in member_content
    assert reverse("feedback_cycle_create", kwargs={"project_id": project.pk}) not in member_content
