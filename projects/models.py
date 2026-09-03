from django.conf import settings
from django.core.exceptions import ValidationError
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


class FeedbackCycle(models.Model):
    class Status(models.TextChoices):
        COLLECTING_FEEDBACK = "collecting_feedback", "Collecting feedback"
        RETROSPECTIVE = "retrospective", "Retrospective"
        COMPLETED = "completed", "Completed"

    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="feedback_cycles",
    )
    facilitator = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="created_feedback_cycles",
    )
    label = models.CharField(max_length=255)
    status = models.CharField(
        max_length=32,
        choices=Status.choices,
        default=Status.COLLECTING_FEEDBACK,
    )
    opens_at = models.DateTimeField()
    closes_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-opens_at", "-id"]
        constraints = [
            models.CheckConstraint(
                condition=Q(
                    status__in=[
                        "collecting_feedback",
                        "retrospective",
                        "completed",
                    ]
                ),
                name="feedback_cycle_status_is_mvp_status",
            ),
            models.CheckConstraint(
                condition=Q(closes_at__isnull=True) | Q(closes_at__gte=models.F("opens_at")),
                name="feedback_cycle_closes_at_not_before_opens_at",
            ),
            models.UniqueConstraint(
                fields=["project"],
                condition=~Q(status="completed"),
                name="unique_active_feedback_cycle_per_project",
            ),
        ]

    def clean(self):
        super().clean()
        if self.closes_at is not None and self.closes_at < self.opens_at:
            raise ValidationError(
                {"closes_at": "Closing time cannot be earlier than the opening time."}
            )

    @property
    def is_completed(self) -> bool:
        return self.status == self.Status.COMPLETED

    def __str__(self) -> str:
        return f"{self.label} ({self.project})"


class FeedbackCard(models.Model):
    class Category(models.TextChoices):
        START = "start", "Start"
        STOP = "stop", "Stop"
        CONTINUE = "continue", "Continue"

    cycle = models.ForeignKey(
        FeedbackCycle,
        on_delete=models.CASCADE,
        related_name="feedback_cards",
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="feedback_cards",
    )
    category = models.CharField(
        max_length=16,
        choices=Category.choices,
    )
    text = models.TextField()
    is_anonymous = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["created_at", "id"]
        constraints = [
            models.CheckConstraint(
                condition=Q(category__in=["start", "stop", "continue"]),
                name="feedback_card_category_is_mvp_category",
            ),
        ]

    def clean(self):
        super().clean()
        if not self.text or not self.text.strip():
            raise ValidationError({"text": "Feedback text cannot be empty."})

    def __str__(self) -> str:
        return f"{self.get_category_display()} feedback by {self.author} for {self.cycle}"
