import pytest
from django.apps import apps
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from projects.models import Membership, Project
from projects.permissions import (
    can_facilitate_project,
    can_view_project,
    facilitatable_projects_for,
    viewable_projects_for,
)


pytestmark = pytest.mark.django_db


def create_user(username="member", *, is_active=True, is_staff=False, is_superuser=False):
    return get_user_model().objects.create_user(
        username=username,
        password="UsablePass123!",
        is_active=is_active,
        is_staff=is_staff,
        is_superuser=is_superuser,
    )


def test_projects_app_is_installed():
    assert apps.is_installed("projects")


def test_project_has_name_and_stable_timestamps():
    project = Project.objects.create(name="Platform Retrospective")

    assert str(project) == "Platform Retrospective"
    assert project.created_at is not None
    assert project.updated_at is not None


def test_membership_connects_user_to_project_with_mvp_roles():
    facilitator = create_user(username="facilitator")
    member = create_user(username="team-member")
    project = Project.objects.create(name="Weekly Ops")

    facilitator_membership = Membership.objects.create(
        project=project,
        user=facilitator,
        role=Membership.Role.FACILITATOR,
    )
    member_membership = Membership.objects.create(project=project, user=member)

    assert facilitator_membership.role == Membership.Role.FACILITATOR
    assert member_membership.role == Membership.Role.TEAM_MEMBER
    assert Membership.Role.values == ["facilitator", "team_member"]


def test_membership_rejects_roles_outside_mvp_choices():
    user = create_user()
    project = Project.objects.create(name="Weekly Ops")
    membership = Membership(project=project, user=user, role="observer")

    with pytest.raises(ValidationError):
        membership.full_clean()

    with pytest.raises(IntegrityError), transaction.atomic():
        Membership.objects.create(project=project, user=user, role="observer")


def test_user_cannot_have_duplicate_membership_for_same_project():
    user = create_user()
    project = Project.objects.create(name="Weekly Ops")
    Membership.objects.create(project=project, user=user)

    with pytest.raises(IntegrityError), transaction.atomic():
        Membership.objects.create(project=project, user=user)


def test_deleting_project_removes_memberships():
    user = create_user()
    project = Project.objects.create(name="Weekly Ops")
    Membership.objects.create(project=project, user=user)

    project.delete()

    assert Membership.objects.count() == 0


def test_deleting_user_removes_memberships_without_deleting_project():
    user = create_user()
    project = Project.objects.create(name="Weekly Ops")
    Membership.objects.create(project=project, user=user)

    user.delete()

    assert Membership.objects.count() == 0
    assert Project.objects.filter(pk=project.pk).exists()


def test_permission_helpers_distinguish_members_and_facilitators():
    facilitator = create_user(username="facilitator")
    member = create_user(username="member")
    non_member = create_user(username="non-member")
    project = Project.objects.create(name="Weekly Ops")
    Membership.objects.create(
        project=project,
        user=facilitator,
        role=Membership.Role.FACILITATOR,
    )
    Membership.objects.create(project=project, user=member)

    assert can_view_project(facilitator, project) is True
    assert can_facilitate_project(facilitator, project) is True
    assert can_view_project(member, project) is True
    assert can_facilitate_project(member, project) is False
    assert can_view_project(non_member, project) is False
    assert can_facilitate_project(non_member, project) is False


def test_anonymous_and_inactive_users_cannot_view_or_facilitate_projects():
    inactive_user = create_user(username="inactive", is_active=False)
    project = Project.objects.create(name="Weekly Ops")
    Membership.objects.create(project=project, user=inactive_user)

    assert can_view_project(AnonymousUser(), project) is False
    assert can_facilitate_project(AnonymousUser(), project) is False
    assert can_view_project(inactive_user, project) is False
    assert can_facilitate_project(inactive_user, project) is False


def test_staff_and_superuser_status_do_not_grant_project_access_without_membership():
    staff_user = create_user(username="staff", is_staff=True)
    superuser = create_user(username="superuser", is_staff=True, is_superuser=True)
    project = Project.objects.create(name="Weekly Ops")

    assert can_view_project(staff_user, project) is False
    assert can_facilitate_project(staff_user, project) is False
    assert can_view_project(superuser, project) is False
    assert can_facilitate_project(superuser, project) is False


def test_project_query_helpers_scope_to_viewable_and_facilitatable_projects():
    facilitator = create_user(username="facilitator")
    member = create_user(username="member")
    inactive_user = create_user(username="inactive", is_active=False)
    facilitated_project = Project.objects.create(name="Facilitated Retrospective")
    member_project = Project.objects.create(name="Member Retrospective")
    other_project = Project.objects.create(name="Other Retrospective")
    inactive_project = Project.objects.create(name="Inactive Retrospective")
    Membership.objects.create(
        project=facilitated_project,
        user=facilitator,
        role=Membership.Role.FACILITATOR,
    )
    Membership.objects.create(project=member_project, user=facilitator)
    Membership.objects.create(project=other_project, user=member)
    Membership.objects.create(project=inactive_project, user=inactive_user)

    assert list(viewable_projects_for(facilitator)) == [
        facilitated_project,
        member_project,
    ]
    assert list(facilitatable_projects_for(facilitator)) == [facilitated_project]
    assert list(viewable_projects_for(member)) == [other_project]
    assert list(facilitatable_projects_for(member)) == []
    assert list(viewable_projects_for(inactive_user)) == []
    assert list(facilitatable_projects_for(AnonymousUser())) == []
