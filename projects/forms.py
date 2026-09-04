from django import forms

from projects.models import FeedbackCard, FeedbackCluster, FeedbackCycle


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
