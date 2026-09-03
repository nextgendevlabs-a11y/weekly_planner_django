from django import forms

from projects.models import FeedbackCycle


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
