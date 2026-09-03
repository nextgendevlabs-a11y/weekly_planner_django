from django import forms

from projects.models import FeedbackCard, FeedbackCycle


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
