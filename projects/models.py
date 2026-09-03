from django.conf import settings
from django.db import models
from django.db.models import Q


class Project(models.Model):
    name = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name", "id"]

    def __str__(self) -> str:
        return self.name


class Membership(models.Model):
    class Role(models.TextChoices):
        FACILITATOR = "facilitator", "Facilitator"
        TEAM_MEMBER = "team_member", "Team member"

    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="memberships",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="project_memberships",
    )
    role = models.CharField(
        max_length=20,
        choices=Role.choices,
        default=Role.TEAM_MEMBER,
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["project", "user"],
                name="unique_project_membership",
            ),
            models.CheckConstraint(
                condition=Q(role__in=["facilitator", "team_member"]),
                name="membership_role_is_mvp_role",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.user} in {self.project} as {self.get_role_display()}"
