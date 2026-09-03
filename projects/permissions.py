from django.contrib.auth.models import AnonymousUser

from projects.models import Membership, Project


def _is_active_authenticated_user(user) -> bool:
    return (
        user is not None
        and not isinstance(user, AnonymousUser)
        and user.is_authenticated
        and user.is_active
    )


def can_view_project(user, project: Project) -> bool:
    if not _is_active_authenticated_user(user):
        return False

    return Membership.objects.filter(user=user, project=project).exists()


def can_facilitate_project(user, project: Project) -> bool:
    if not _is_active_authenticated_user(user):
        return False

    return Membership.objects.filter(
        user=user,
        project=project,
        role=Membership.Role.FACILITATOR,
    ).exists()


def viewable_projects_for(user):
    if not _is_active_authenticated_user(user):
        return Project.objects.none()

    return Project.objects.filter(memberships__user=user).distinct()


def facilitatable_projects_for(user):
    if not _is_active_authenticated_user(user):
        return Project.objects.none()

    return Project.objects.filter(
        memberships__user=user,
        memberships__role=Membership.Role.FACILITATOR,
    ).distinct()
