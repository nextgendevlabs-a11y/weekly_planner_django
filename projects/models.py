from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q


def meeting_material_upload_path(instance, filename):
    return f"meeting_materials/{instance.cycle_id}/{filename}"


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

    class VotingStatus(models.TextChoices):
        CLUSTERING = "clustering", "Clustering"
        OPEN = "open", "Voting open"
        CLOSED = "closed", "Voting closed"

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
    voting_status = models.CharField(
        max_length=16,
        choices=VotingStatus.choices,
        default=VotingStatus.CLUSTERING,
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
                condition=Q(voting_status__in=["clustering", "open", "closed"]),
                name="feedback_cycle_voting_status_is_mvp_status",
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

    @property
    def is_voting_open(self) -> bool:
        return self.voting_status == self.VotingStatus.OPEN

    @property
    def is_voting_closed(self) -> bool:
        return self.voting_status == self.VotingStatus.CLOSED

    def __str__(self) -> str:
        return f"{self.label} ({self.project})"


class FeedbackCluster(models.Model):
    class DiscussionStatus(models.TextChoices):
        PENDING = "pending", "Not started"
        DISCUSSED = "discussed", "Discussed"
        SKIPPED = "skipped", "Skipped"
        DEFERRED = "deferred", "Deferred"

    cycle = models.ForeignKey(
        FeedbackCycle,
        on_delete=models.CASCADE,
        related_name="feedback_clusters",
    )
    name = models.CharField(max_length=255)
    discussion_status = models.CharField(
        max_length=16,
        choices=DiscussionStatus.choices,
        default=DiscussionStatus.PENDING,
    )
    discussion_notes = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["created_at", "id"]
        constraints = [
            models.CheckConstraint(
                condition=Q(
                    discussion_status__in=[
                        "pending",
                        "discussed",
                        "skipped",
                        "deferred",
                    ]
                ),
                name="feedback_cluster_discussion_status_is_mvp_status",
            ),
        ]

    def clean(self):
        super().clean()
        if not self.name or not self.name.strip():
            raise ValidationError({"name": "Cluster name cannot be empty."})

    def __str__(self) -> str:
        return f"{self.name} ({self.cycle})"


class ActionItem(models.Model):
    class Status(models.TextChoices):
        OPEN = "open", "Open"
        DONE = "done", "Done"

    cycle = models.ForeignKey(
        FeedbackCycle,
        on_delete=models.CASCADE,
        related_name="action_items",
    )
    description = models.TextField()
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="retrospective_action_items",
    )
    due_date = models.DateField(blank=True, null=True)
    status = models.CharField(
        max_length=8,
        choices=Status.choices,
        default=Status.OPEN,
    )
    topic = models.ForeignKey(
        FeedbackCluster,
        on_delete=models.PROTECT,
        related_name="action_items",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["due_date", "created_at", "id"]
        constraints = [
            models.CheckConstraint(
                condition=Q(status__in=["open", "done"]),
                name="action_item_status_is_mvp_status",
            ),
        ]

    def clean(self):
        super().clean()
        if not self.description or not self.description.strip():
            raise ValidationError({"description": "Action item description cannot be empty."})
        self.description = self.description.strip()

        if self.owner_id is not None and self.cycle_id is not None:
            owner_is_active_member = Membership.objects.filter(
                project=self.cycle.project,
                user=self.owner,
                user__is_active=True,
            ).exists()
            if not owner_is_active_member:
                raise ValidationError(
                    {"owner": "Choose an active project member as the action item owner."}
                )

        if (
            self.topic_id is not None
            and self.cycle_id is not None
            and self.topic.cycle_id != self.cycle_id
        ):
            raise ValidationError(
                {"topic": "Action item topic must belong to the same feedback cycle."}
            )

    def __str__(self) -> str:
        return f"{self.description} ({self.get_status_display()})"


class RetrospectiveDecision(models.Model):
    cycle = models.ForeignKey(
        FeedbackCycle,
        on_delete=models.CASCADE,
        related_name="decisions",
    )
    text = models.TextField()
    topic = models.ForeignKey(
        FeedbackCluster,
        on_delete=models.PROTECT,
        related_name="decisions",
        blank=True,
        null=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["created_at", "id"]

    def clean(self):
        super().clean()
        if not self.text or not self.text.strip():
            raise ValidationError({"text": "Decision text cannot be empty."})
        self.text = self.text.strip()

        if (
            self.topic_id is not None
            and self.cycle_id is not None
            and self.topic.cycle_id != self.cycle_id
        ):
            raise ValidationError(
                {"topic": "Decision topic must belong to the same feedback cycle."}
            )

    def __str__(self) -> str:
        return self.text


class MeetingMaterial(models.Model):
    class SourceType(models.TextChoices):
        AUDIO_UPLOAD = "audio_upload", "Audio upload"
        VIDEO_UPLOAD = "video_upload", "Video upload"
        TRANSCRIPT_FILE = "transcript_file", "Transcript file"
        PASTED_TRANSCRIPT = "pasted_transcript", "Pasted transcript"

    class ProcessingStatus(models.TextChoices):
        QUEUED = "queued", "Queued"
        PROCESSING = "processing", "Processing"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"

    cycle = models.ForeignKey(
        FeedbackCycle,
        on_delete=models.CASCADE,
        related_name="meeting_materials",
    )
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="submitted_meeting_materials",
    )
    source_type = models.CharField(
        max_length=32,
        choices=SourceType.choices,
    )
    processing_status = models.CharField(
        max_length=16,
        choices=ProcessingStatus.choices,
        default=ProcessingStatus.QUEUED,
    )
    source_file = models.FileField(
        upload_to=meeting_material_upload_path,
        blank=True,
    )
    original_filename = models.CharField(max_length=255, blank=True, default="")
    content_type = models.CharField(max_length=255, blank=True, default="")
    byte_size = models.PositiveBigIntegerField(blank=True, null=True)
    pasted_transcript_text = models.TextField(blank=True, default="")
    text_character_count = models.PositiveIntegerField(default=0)
    failure_message = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            models.CheckConstraint(
                condition=Q(
                    source_type__in=[
                        "audio_upload",
                        "video_upload",
                        "transcript_file",
                        "pasted_transcript",
                    ]
                ),
                name="meeting_material_source_type_is_mvp_type",
            ),
            models.CheckConstraint(
                condition=Q(
                    processing_status__in=[
                        "queued",
                        "processing",
                        "succeeded",
                        "failed",
                    ]
                ),
                name="meeting_material_status_is_mvp_status",
            ),
        ]

    @property
    def source_label(self) -> str:
        if self.source_type == self.SourceType.PASTED_TRANSCRIPT:
            return "Pasted transcript"
        return self.original_filename or self.get_source_type_display()

    def clean(self):
        super().clean()
        if self.source_type == self.SourceType.PASTED_TRANSCRIPT:
            transcript_text = self.pasted_transcript_text.strip()
            if not transcript_text:
                raise ValidationError(
                    {"pasted_transcript_text": "Pasted transcript cannot be empty."}
                )
            if self.source_file:
                raise ValidationError(
                    {"source_file": "Pasted transcript records cannot include a file."}
                )
            self.pasted_transcript_text = transcript_text
            self.text_character_count = len(transcript_text)
            self.original_filename = ""
            self.content_type = ""
            self.byte_size = None
            return

        if self.source_type in {
            self.SourceType.AUDIO_UPLOAD,
            self.SourceType.VIDEO_UPLOAD,
            self.SourceType.TRANSCRIPT_FILE,
        }:
            if not self.source_file:
                raise ValidationError({"source_file": "Uploaded source file is required."})
            if not self.original_filename:
                raise ValidationError(
                    {"original_filename": "Original filename is required."}
                )
            if self.byte_size is None:
                raise ValidationError({"byte_size": "Uploaded file size is required."})
            if self.pasted_transcript_text:
                raise ValidationError(
                    {
                        "pasted_transcript_text": (
                            "Uploaded source records cannot include pasted transcript text."
                        )
                    }
                )
            self.text_character_count = 0

    def __str__(self) -> str:
        return f"{self.get_source_type_display()} for {self.cycle}"


class MeetingMaterialTranscript(models.Model):
    meeting_material = models.OneToOneField(
        MeetingMaterial,
        on_delete=models.CASCADE,
        related_name="processed_transcript",
    )
    text = models.TextField()
    character_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["meeting_material_id"]

    def clean(self):
        super().clean()
        text = self.text.strip()
        if not text:
            raise ValidationError({"text": "Processed transcript cannot be empty."})
        self.text = text
        self.character_count = len(text)

    def __str__(self) -> str:
        return f"Processed transcript for {self.meeting_material}"


class MeetingMaterialExtractionDraft(models.Model):
    meeting_material = models.OneToOneField(
        MeetingMaterial,
        on_delete=models.CASCADE,
        related_name="extraction_draft",
    )
    retrospective_summary_text = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["meeting_material_id"]

    @property
    def cycle(self):
        return self.meeting_material.cycle

    def clean(self):
        super().clean()
        self.retrospective_summary_text = self.retrospective_summary_text.strip()

    def __str__(self) -> str:
        return f"Extraction draft for {self.meeting_material}"


class MeetingMaterialDraftDecision(models.Model):
    extraction_draft = models.ForeignKey(
        MeetingMaterialExtractionDraft,
        on_delete=models.CASCADE,
        related_name="draft_decisions",
    )
    text = models.TextField()
    topic_candidate = models.CharField(max_length=255, blank=True, default="")
    matched_topic = models.ForeignKey(
        FeedbackCluster,
        on_delete=models.SET_NULL,
        related_name="meeting_material_draft_decisions",
        blank=True,
        null=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["created_at", "id"]

    def clean(self):
        super().clean()
        text = self.text.strip()
        if not text:
            raise ValidationError({"text": "Draft decision text cannot be empty."})
        self.text = text
        self.topic_candidate = self.topic_candidate.strip()
        if (
            self.matched_topic_id is not None
            and self.extraction_draft_id is not None
            and self.matched_topic.cycle_id
            != self.extraction_draft.meeting_material.cycle_id
        ):
            raise ValidationError(
                {"matched_topic": "Matched topic must belong to the same feedback cycle."}
            )

    def __str__(self) -> str:
        return self.text


class MeetingMaterialDraftActionItem(models.Model):
    extraction_draft = models.ForeignKey(
        MeetingMaterialExtractionDraft,
        on_delete=models.CASCADE,
        related_name="draft_action_items",
    )
    description = models.TextField()
    owner_candidate = models.CharField(max_length=255, blank=True, default="")
    matched_owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="meeting_material_draft_action_items",
        blank=True,
        null=True,
    )
    due_date = models.DateField(blank=True, null=True)
    topic_candidate = models.CharField(max_length=255, blank=True, default="")
    matched_topic = models.ForeignKey(
        FeedbackCluster,
        on_delete=models.SET_NULL,
        related_name="meeting_material_draft_action_items",
        blank=True,
        null=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["due_date", "created_at", "id"]

    def clean(self):
        super().clean()
        description = self.description.strip()
        if not description:
            raise ValidationError(
                {"description": "Draft action item description cannot be empty."}
            )
        self.description = description
        self.owner_candidate = self.owner_candidate.strip()
        self.topic_candidate = self.topic_candidate.strip()

        material = None
        if self.extraction_draft_id is not None:
            material = self.extraction_draft.meeting_material

        if self.matched_topic_id is not None and material is not None:
            if self.matched_topic.cycle_id != material.cycle_id:
                raise ValidationError(
                    {
                        "matched_topic": (
                            "Matched topic must belong to the same feedback cycle."
                        )
                    }
                )

        if self.matched_owner_id is not None and material is not None:
            owner_is_active_member = Membership.objects.filter(
                project=material.cycle.project,
                user=self.matched_owner,
                user__is_active=True,
            ).exists()
            if not owner_is_active_member:
                raise ValidationError(
                    {
                        "matched_owner": (
                            "Matched owner must be an active project member."
                        )
                    }
                )

    def __str__(self) -> str:
        return self.description


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
    cluster = models.ForeignKey(
        FeedbackCluster,
        on_delete=models.SET_NULL,
        related_name="feedback_cards",
        blank=True,
        null=True,
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
        if (
            self.cluster_id is not None
            and self.cycle_id is not None
            and self.cluster.cycle_id != self.cycle_id
        ):
            raise ValidationError(
                {"cluster": "Feedback card cluster must belong to the same feedback cycle."}
            )

    def __str__(self) -> str:
        return f"{self.get_category_display()} feedback by {self.author} for {self.cycle}"


class FeedbackClusterVote(models.Model):
    cycle = models.ForeignKey(
        FeedbackCycle,
        on_delete=models.CASCADE,
        related_name="cluster_votes",
    )
    voter = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="feedback_cluster_votes",
    )
    cluster = models.ForeignKey(
        FeedbackCluster,
        on_delete=models.CASCADE,
        related_name="votes",
    )
    vote_count = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["cycle", "voter", "cluster"]
        constraints = [
            models.UniqueConstraint(
                fields=["cycle", "voter", "cluster"],
                name="unique_feedback_cluster_vote_allocation",
            ),
            models.CheckConstraint(
                condition=Q(vote_count__gte=0) & Q(vote_count__lte=3),
                name="feedback_cluster_vote_count_is_three_vote_budget",
            ),
        ]

    def clean(self):
        super().clean()
        if (
            self.cluster_id is not None
            and self.cycle_id is not None
            and self.cluster.cycle_id != self.cycle_id
        ):
            raise ValidationError(
                {"cluster": "Vote cluster must belong to the same feedback cycle."}
            )

    def __str__(self) -> str:
        return f"{self.voter} vote for {self.cluster}"
