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
