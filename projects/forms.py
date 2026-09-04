from pathlib import Path

from django import forms
from django.conf import settings
from django.contrib.auth import get_user_model

from projects.models import (
    ActionItem,
    FeedbackCard,
    FeedbackCluster,
    FeedbackCycle,
    MeetingMaterial,
    RetrospectiveDecision,
)


class FeedbackCycleCreateForm(forms.ModelForm):
    class Meta:
        model = FeedbackCycle
        fields = ["label", "opens_at", "closes_at"]
        labels = {
            "opens_at": "Opening time",
            "closes_at": "Closing time",
        }
        widgets = {
            "opens_at": forms.DateTimeInput(
                attrs={"type": "datetime-local"},
                format="%Y-%m-%dT%H:%M",
            ),
            "closes_at": forms.DateTimeInput(
                attrs={"type": "datetime-local"},
                format="%Y-%m-%dT%H:%M",
            ),
        }

    def __init__(self, *args, project, facilitator, **kwargs):
        super().__init__(*args, **kwargs)
        self.project = project
        self.facilitator = facilitator
        self.fields["opens_at"].input_formats = ["%Y-%m-%dT%H:%M"]
        self.fields["closes_at"].input_formats = ["%Y-%m-%dT%H:%M"]

    def clean(self):
        cleaned_data = super().clean()
        active_cycle_exists = FeedbackCycle.objects.filter(
            project=self.project,
        ).exclude(status=FeedbackCycle.Status.COMPLETED).exists()
        if active_cycle_exists:
            raise forms.ValidationError(
                "This project already has an active feedback cycle."
            )
        return cleaned_data

    def save(self, commit=True):
        cycle = super().save(commit=False)
        cycle.project = self.project
        cycle.facilitator = self.facilitator
        if commit:
            cycle.save()
        return cycle


class FeedbackCardForm(forms.ModelForm):
    class Meta:
        model = FeedbackCard
        fields = ["text", "is_anonymous"]
        labels = {
            "text": "Feedback",
            "is_anonymous": "Submit this card anonymously",
        }
        widgets = {
            "text": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, cycle, author, category=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.cycle = cycle
        self.author = author
        self.category = category
        self.fields["text"].error_messages["required"] = "Feedback text cannot be empty."

    def clean_text(self):
        text = self.cleaned_data["text"]
        if not text.strip():
            raise forms.ValidationError("Feedback text cannot be empty.")
        return text.strip()

    def save(self, commit=True):
        card = super().save(commit=False)
        card.cycle = self.cycle
        card.author = self.author
        if self.category is not None:
            card.category = self.category
        if commit:
            card.save()
        return card


class FeedbackClusterForm(forms.ModelForm):
    class Meta:
        model = FeedbackCluster
        fields = ["name"]
        labels = {
            "name": "Cluster name",
        }

    def __init__(self, *args, cycle, **kwargs):
        super().__init__(*args, **kwargs)
        self.cycle = cycle
        self.fields["name"].error_messages["required"] = "Cluster name cannot be empty."

    def clean_name(self):
        name = self.cleaned_data["name"]
        if not name.strip():
            raise forms.ValidationError("Cluster name cannot be empty.")
        return name.strip()

    def save(self, commit=True):
        cluster = super().save(commit=False)
        cluster.cycle = self.cycle
        if commit:
            cluster.save()
        return cluster


class FeedbackClusterDiscussionForm(forms.Form):
    discussion_status = forms.ChoiceField(
        choices=FeedbackCluster.DiscussionStatus.choices,
        required=False,
        label="Topic status",
        error_messages={
            "invalid_choice": "Choose a valid discussion status.",
        },
    )
    discussion_notes = forms.CharField(
        required=False,
        label="Discussion notes",
        widget=forms.Textarea(attrs={"rows": 4}),
    )

    def __init__(self, *args, cluster, **kwargs):
        super().__init__(*args, **kwargs)
        self.cluster = cluster

    def clean(self):
        cleaned_data = super().clean()
        if self.errors:
            return cleaned_data

        if not cleaned_data.get("discussion_status"):
            cleaned_data["discussion_status"] = self.cluster.discussion_status
        cleaned_data["discussion_notes"] = cleaned_data.get("discussion_notes", "").strip()
        return cleaned_data

    def save(self):
        self.cluster.discussion_status = self.cleaned_data["discussion_status"]
        self.cluster.discussion_notes = self.cleaned_data["discussion_notes"]
        self.cluster.save(
            update_fields=[
                "discussion_status",
                "discussion_notes",
                "updated_at",
            ]
        )
        return self.cluster


class ActionItemForm(forms.ModelForm):
    class Meta:
        model = ActionItem
        fields = ["description", "owner", "due_date", "status", "topic"]
        labels = {
            "description": "Action item",
            "owner": "Owner",
            "due_date": "Due date",
            "status": "Status",
            "topic": "Related discussion topic",
        }
        widgets = {
            "description": forms.Textarea(attrs={"rows": 3}),
            "due_date": forms.DateInput(attrs={"type": "date"}),
        }
        error_messages = {
            "description": {
                "required": "Action item description cannot be empty.",
            },
            "owner": {
                "required": "Choose an active project member as the action item owner.",
                "invalid_choice": "Choose an active project member as the action item owner.",
            },
            "topic": {
                "required": "Choose a discussion topic from this cycle.",
                "invalid_choice": "Choose a discussion topic from this cycle.",
            },
            "status": {
                "invalid_choice": "Choose a valid action item status.",
            },
            "due_date": {
                "invalid": "Enter a valid due date.",
            },
        }

    def __init__(self, *args, cycle, **kwargs):
        super().__init__(*args, **kwargs)
        self.cycle = cycle
        self.instance.cycle = cycle
        self.fields["owner"].queryset = (
            get_user_model()
            .objects.filter(is_active=True, project_memberships__project=cycle.project)
            .distinct()
            .order_by("username", "id")
        )
        self.fields["topic"].queryset = cycle.feedback_clusters.all()
        self.fields["status"].required = False

    def clean_description(self):
        description = self.cleaned_data["description"]
        if not description.strip():
            raise forms.ValidationError("Action item description cannot be empty.")
        return description.strip()

    def clean_status(self):
        status = self.cleaned_data.get("status")
        if status:
            return status
        if self.instance.pk:
            return self.instance.status
        return ActionItem.Status.OPEN

    def save(self, commit=True):
        action_item = super().save(commit=False)
        action_item.cycle = self.cycle
        if commit:
            action_item.save()
        return action_item


class RetrospectiveDecisionForm(forms.ModelForm):
    class Meta:
        model = RetrospectiveDecision
        fields = ["text", "topic"]
        labels = {
            "text": "Decision",
            "topic": "Related discussion topic",
        }
        widgets = {
            "text": forms.Textarea(attrs={"rows": 3}),
        }
        error_messages = {
            "text": {
                "required": "Decision text cannot be empty.",
            },
            "topic": {
                "invalid_choice": "Choose a discussion topic from this cycle.",
            },
        }

    def __init__(self, *args, cycle, **kwargs):
        super().__init__(*args, **kwargs)
        self.cycle = cycle
        self.instance.cycle = cycle
        self.fields["topic"].queryset = cycle.feedback_clusters.all()
        self.fields["topic"].required = False

    def clean_text(self):
        text = self.cleaned_data["text"]
        if not text.strip():
            raise forms.ValidationError("Decision text cannot be empty.")
        return text.strip()

    def save(self, commit=True):
        decision = super().save(commit=False)
        decision.cycle = self.cycle
        if commit:
            decision.save()
        return decision


class MeetingMaterialForm(forms.Form):
    audio_file = forms.FileField(
        required=False,
        label="Audio file",
        widget=forms.FileInput(attrs={"accept": "audio/*"}),
    )
    video_file = forms.FileField(
        required=False,
        label="Video file",
        widget=forms.FileInput(attrs={"accept": "video/*"}),
    )
    transcript_file = forms.FileField(
        required=False,
        label="Transcript file",
        widget=forms.FileInput(attrs={"accept": ".txt,.md,.markdown,.vtt,.srt,text/*"}),
    )
    pasted_transcript = forms.CharField(
        required=False,
        label="Pasted transcript",
        widget=forms.Textarea(attrs={"rows": 6}),
    )

    audio_extensions = {
        ".aac",
        ".aiff",
        ".flac",
        ".m4a",
        ".mp3",
        ".oga",
        ".ogg",
        ".opus",
        ".wav",
        ".wma",
    }
    video_extensions = {
        ".avi",
        ".m4v",
        ".mkv",
        ".mov",
        ".mp4",
        ".mpeg",
        ".mpg",
        ".webm",
        ".wmv",
    }
    transcript_extensions = {".md", ".markdown", ".srt", ".txt", ".vtt"}
    transcript_content_types = {
        "application/srt",
        "application/x-subrip",
        "text/markdown",
        "text/plain",
        "text/srt",
        "text/vtt",
    }

    def __init__(self, *args, cycle, submitter, **kwargs):
        super().__init__(*args, **kwargs)
        self.cycle = cycle
        self.submitter = submitter
        self.selected_source = None

    def _validate_upload(self, uploaded_file, *, field_name, source_name):
        if uploaded_file is None:
            return None

        upload_limit = settings.FILE_UPLOAD_MAX_MEMORY_SIZE
        if uploaded_file.size > upload_limit:
            raise forms.ValidationError(
                f"Uploaded files must be {upload_limit} bytes or smaller."
            )

        content_type = (getattr(uploaded_file, "content_type", "") or "").lower()
        extension = Path(uploaded_file.name).suffix.lower()
        if field_name == "audio_file":
            allowed = content_type.startswith("audio/") or extension in self.audio_extensions
        elif field_name == "video_file":
            allowed = content_type.startswith("video/") or extension in self.video_extensions
        else:
            allowed = (
                content_type.startswith("text/")
                or content_type in self.transcript_content_types
                or extension in self.transcript_extensions
            )

        if not allowed:
            raise forms.ValidationError(
                f"Choose a clearly supported {source_name} file."
            )
        return uploaded_file

    def clean_audio_file(self):
        return self._validate_upload(
            self.cleaned_data.get("audio_file"),
            field_name="audio_file",
            source_name="audio",
        )

    def clean_video_file(self):
        return self._validate_upload(
            self.cleaned_data.get("video_file"),
            field_name="video_file",
            source_name="video",
        )

    def clean_transcript_file(self):
        return self._validate_upload(
            self.cleaned_data.get("transcript_file"),
            field_name="transcript_file",
            source_name="transcript",
        )

    def clean_pasted_transcript(self):
        text = self.cleaned_data.get("pasted_transcript", "").strip()
        if not text:
            return ""

        upload_limit = settings.DATA_UPLOAD_MAX_MEMORY_SIZE
        if len(text.encode("utf-8")) > upload_limit:
            raise forms.ValidationError(
                f"Pasted transcript must be {upload_limit} bytes or smaller."
            )
        return text

    def clean(self):
        cleaned_data = super().clean()
        if self.errors:
            return cleaned_data

        posted_source_type = self.data.get("source_type")
        if (
            posted_source_type
            and posted_source_type not in MeetingMaterial.SourceType.values
        ):
            raise forms.ValidationError("Choose a valid meeting material source type.")

        posted_status = self.data.get("processing_status")
        if (
            posted_status
            and posted_status not in MeetingMaterial.ProcessingStatus.values
        ):
            raise forms.ValidationError("Choose a valid processing status.")

        sources = [
            (
                MeetingMaterial.SourceType.AUDIO_UPLOAD,
                "audio_file",
                cleaned_data.get("audio_file"),
            ),
            (
                MeetingMaterial.SourceType.VIDEO_UPLOAD,
                "video_file",
                cleaned_data.get("video_file"),
            ),
            (
                MeetingMaterial.SourceType.TRANSCRIPT_FILE,
                "transcript_file",
                cleaned_data.get("transcript_file"),
            ),
            (
                MeetingMaterial.SourceType.PASTED_TRANSCRIPT,
                "pasted_transcript",
                cleaned_data.get("pasted_transcript"),
            ),
        ]
        selected_sources = [
            (source_type, field_name, source)
            for source_type, field_name, source in sources
            if source
        ]
        if not selected_sources:
            raise forms.ValidationError(
                "Add one audio file, video file, transcript file, or pasted transcript."
            )
        if len(selected_sources) > 1:
            raise forms.ValidationError(
                "Submit exactly one meeting material source at a time."
            )

        self.selected_source = selected_sources[0]
        return cleaned_data

    def save(self):
        source_type, _field_name, source = self.selected_source
        meeting_material = MeetingMaterial(
            cycle=self.cycle,
            submitted_by=self.submitter,
            source_type=source_type,
            processing_status=MeetingMaterial.ProcessingStatus.QUEUED,
        )
        if source_type == MeetingMaterial.SourceType.PASTED_TRANSCRIPT:
            meeting_material.pasted_transcript_text = source
            meeting_material.text_character_count = len(source)
        else:
            meeting_material.source_file = source
            meeting_material.original_filename = source.name
            meeting_material.content_type = (
                getattr(source, "content_type", "") or ""
            )
            meeting_material.byte_size = source.size

        meeting_material.full_clean()
        meeting_material.save()
        return meeting_material


class FeedbackClusterSplitForm(forms.Form):
    name = forms.CharField(
        label="New cluster name",
        error_messages={"required": "Cluster name cannot be empty."},
    )
    cards = forms.ModelMultipleChoiceField(
        queryset=FeedbackCard.objects.none(),
        widget=forms.CheckboxSelectMultiple,
        error_messages={
            "required": "Select at least one card to split into the new cluster.",
        },
    )

    def __init__(self, *args, cluster, **kwargs):
        super().__init__(*args, **kwargs)
        self.cluster = cluster
        self.fields["cards"].queryset = cluster.feedback_cards.all()

    def clean_name(self):
        name = self.cleaned_data["name"]
        if not name.strip():
            raise forms.ValidationError("Cluster name cannot be empty.")
        return name.strip()


class FeedbackClusterSuggestionDraftForm(forms.Form):
    suggestion_count = forms.IntegerField(min_value=0)

    def __init__(self, *args, cycle, require_clusters=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.cycle = cycle
        self.require_clusters = require_clusters
        self.cards = list(cycle.feedback_cards.all())
        self.valid_card_ids = {card.pk for card in self.cards}

    def clean(self):
        cleaned_data = super().clean()
        suggestion_count = cleaned_data.get("suggestion_count")
        if suggestion_count is None:
            raise forms.ValidationError("Draft suggestions could not be read.")
        if self.require_clusters and suggestion_count == 0:
            raise forms.ValidationError("There are no draft suggestions to accept.")

        names = []
        for index in range(suggestion_count):
            name = self.data.get(f"suggestion-{index}-name", "")
            if not name.strip():
                raise forms.ValidationError("Cluster name cannot be empty.")
            names.append(name.strip())

        for key in self.data:
            if key.startswith("suggestion-") and key.endswith("-name"):
                index_value = key.removeprefix("suggestion-").removesuffix("-name")
                if not index_value.isdigit() or int(index_value) >= suggestion_count:
                    raise forms.ValidationError("Draft suggestions could not be read.")

        clusters = [{"name": name, "card_ids": []} for name in names]
        for key in self.data:
            if not key.startswith("card-") or not key.endswith("-suggestion"):
                continue

            card_id_value = key.removeprefix("card-").removesuffix("-suggestion")
            if not card_id_value.isdigit():
                raise forms.ValidationError("Draft suggestions could not be read.")

            card_id = int(card_id_value)
            if card_id not in self.valid_card_ids:
                raise forms.ValidationError("Draft contains a card outside this cycle.")

            suggestion_value = self.data.get(key, "")
            if suggestion_value == "":
                continue
            if not suggestion_value.isdigit():
                raise forms.ValidationError("Draft suggestions could not be read.")

            suggestion_index = int(suggestion_value)
            if suggestion_index >= suggestion_count:
                raise forms.ValidationError("Draft suggestions could not be read.")
            clusters[suggestion_index]["card_ids"].append(card_id)

        cleaned_data["clusters"] = clusters
        return cleaned_data

    def draft(self) -> dict:
        return {"clusters": self.cleaned_data["clusters"]}


class FeedbackClusterVoteForm(forms.Form):
    required_total = 3

    def __init__(self, *args, cycle, **kwargs):
        super().__init__(*args, **kwargs)
        self.cycle = cycle
        self.clusters = list(cycle.feedback_clusters.all())
        self.valid_field_names = set()
        for cluster in self.clusters:
            field_name = self.field_name_for(cluster)
            self.valid_field_names.add(field_name)
            self.fields[field_name] = forms.IntegerField(
                min_value=0,
                max_value=self.required_total,
                label=cluster.name,
                error_messages={
                    "required": "Enter a vote count for every cluster.",
                    "invalid": "Vote counts must be whole numbers.",
                    "min_value": "Vote counts cannot be negative.",
                    "max_value": "No cluster can receive more than three votes.",
                },
            )

    @staticmethod
    def field_name_for(cluster):
        return f"cluster_{cluster.pk}_votes"

    def clean(self):
        cleaned_data = super().clean()
        if self.errors:
            return cleaned_data

        total = sum(cleaned_data[field_name] for field_name in self.valid_field_names)
        if total != self.required_total:
            raise forms.ValidationError("Allocate exactly three votes.")

        cleaned_data["allocations"] = {
            cluster: cleaned_data[self.field_name_for(cluster)]
            for cluster in self.clusters
        }
        return cleaned_data
