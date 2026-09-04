"""Page views for the weekly planner project."""

from django.contrib.auth import login
from django.contrib.auth.forms import UserCreationForm
from django.core.exceptions import RequestDataTooBig
from django.db import transaction
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.urls import reverse_lazy
from django.views.generic import DetailView
from django.views.generic import FormView
from django.views.generic import TemplateView
from django.views.generic import View

from projects.forms import (
    ActionItemForm,
    FeedbackCardForm,
    FeedbackClusterDiscussionForm,
    FeedbackClusterForm,
    FeedbackClusterSplitForm,
    FeedbackClusterSuggestionDraftForm,
    FeedbackClusterVoteForm,
    FeedbackCycleCreateForm,
    MeetingMaterialExtractionDraftReviewForm,
    MeetingMaterialForm,
    RetrospectiveDecisionForm,
)
from projects.cluster_suggestions import draft_from_suggestions, get_clustering_service
from projects.meeting_processing import (
    enqueue_meeting_material_processing,
    retry_meeting_material_processing,
    sanitize_processing_error,
)
from projects.models import (
    ActionItem,
    FeedbackCard,
    FeedbackCluster,
    FeedbackCycle,
    MeetingMaterial,
    MeetingMaterialExtractionDraft,
    Project,
    RetrospectiveDecision,
)
from projects.models import Membership
from projects.permissions import can_facilitate_project, facilitatable_projects_for
from projects.permissions import viewable_projects_for
from projects.retrospective_board import (
    retrospective_board_context_for,
    suggestion_draft_context_for,
)
from projects.submission_progress import submission_progress_for
from projects.voting import (
    close_voting,
    open_voting,
    ranked_clusters_for,
    save_vote_allocation,
    saved_vote_allocation_for,
    voting_progress_for,
)


SUGGESTION_DRAFT_SESSION_KEY = "feedback_cluster_suggestion_drafts"


def _suggestion_draft_key(cycle):
    return f"{cycle.project_id}:{cycle.pk}"


def _saved_suggestion_draft(request, cycle):
    drafts = request.session.get(SUGGESTION_DRAFT_SESSION_KEY, {})
    return drafts.get(_suggestion_draft_key(cycle))


def _save_suggestion_draft(request, cycle, draft):
    drafts = request.session.get(SUGGESTION_DRAFT_SESSION_KEY, {}).copy()
    drafts[_suggestion_draft_key(cycle)] = draft
    request.session[SUGGESTION_DRAFT_SESSION_KEY] = drafts
    request.session.modified = True


def _clear_suggestion_draft(request, cycle):
    drafts = request.session.get(SUGGESTION_DRAFT_SESSION_KEY, {}).copy()
    drafts.pop(_suggestion_draft_key(cycle), None)
    request.session[SUGGESTION_DRAFT_SESSION_KEY] = drafts
    request.session.modified = True


def _draft_from_post_for_render(data, cycle):
    try:
        suggestion_count = int(data.get("suggestion_count", 0))
    except (TypeError, ValueError):
        suggestion_count = 0
    suggestion_count = max(suggestion_count, 0)
    clusters = [
        {"name": data.get(f"suggestion-{index}-name", ""), "card_ids": []}
        for index in range(suggestion_count)
    ]

    for card_id in cycle.feedback_cards.values_list("id", flat=True):
        suggestion_value = data.get(f"card-{card_id}-suggestion", "")
        if not suggestion_value.isdigit():
            continue
        suggestion_index = int(suggestion_value)
        if suggestion_index < suggestion_count:
            clusters[suggestion_index]["card_ids"].append(card_id)

    return {"clusters": clusters}


def _form_errors(form):
    return [str(error) for error in form.non_field_errors()]


def _posted_vote_cluster_ids(data):
    cluster_ids = set()
    for key in data:
        if not key.startswith("cluster_") or not key.endswith("_votes"):
            continue
        cluster_id_value = key.removeprefix("cluster_").removesuffix("_votes")
        if not cluster_id_value.isdigit():
            raise Http404("No vote cluster matches the given query.")
        cluster_ids.add(int(cluster_id_value))
    return cluster_ids


def _posted_positive_int_or_404(data, field_name, message):
    value = data.get(field_name)
    if value in (None, ""):
        return None
    if not value.isdigit():
        raise Http404(message)
    return int(value)


def _posted_exact_int_or_404(data, field_name, expected_value, message):
    value = data.get(field_name)
    if value is None or not value.isdigit() or int(value) != expected_value:
        raise Http404(message)


def _posted_review_object_ids_or_404(data, *, prefix, suffixes, valid_ids, message):
    seen_ids = set()
    for key in data:
        if not key.startswith(prefix):
            continue

        tail = key.removeprefix(prefix)
        object_id_value, separator, suffix = tail.partition("_")
        if separator != "_" or suffix not in suffixes or not object_id_value.isdigit():
            raise Http404(message)

        object_id = int(object_id_value)
        if object_id not in valid_ids:
            raise Http404(message)
        seen_ids.add(object_id)
    return seen_ids


class HomeView(TemplateView):
    """Public landing page for the retrospective workflow."""

    template_name = "home.html"


class SignUpView(FormView):
    """Create an account and start the authenticated workflow."""

    form_class = UserCreationForm
    template_name = "registration/signup.html"
    success_url = reverse_lazy("projects")

    def form_valid(self, form):
        user = form.save()
        login(self.request, user)
        return super().form_valid(form)


class ProjectsView(LoginRequiredMixin, TemplateView):
    """List projects available to the signed-in user."""

    template_name = "projects/index.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["projects"] = viewable_projects_for(self.request.user)
        return context


class ProjectDashboardView(LoginRequiredMixin, DetailView):
    """Membership-scoped dashboard shell for one retrospective project."""

    context_object_name = "project"
    model = Project
    pk_url_kwarg = "project_id"
    template_name = "projects/dashboard.html"

    def get_queryset(self):
        return viewable_projects_for(self.request.user)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        active_cycle = (
            self.object.feedback_cycles.exclude(status=FeedbackCycle.Status.COMPLETED)
            .order_by("-opens_at", "-id")
            .first()
        )
        collecting_cycle = None
        retrospective_cycle = None
        has_submitted_feedback = False
        team_submission_progress = None
        can_facilitate = can_facilitate_project(self.request.user, self.object)
        if active_cycle and active_cycle.status == FeedbackCycle.Status.COLLECTING_FEEDBACK:
            collecting_cycle = active_cycle
            has_submitted_feedback = active_cycle.feedback_cards.filter(
                author=self.request.user
            ).exists()
            if can_facilitate:
                team_submission_progress = submission_progress_for(active_cycle)
        elif active_cycle and active_cycle.status == FeedbackCycle.Status.RETROSPECTIVE:
            retrospective_cycle = active_cycle

        context["active_cycle"] = active_cycle
        context["collecting_cycle"] = collecting_cycle
        context["retrospective_cycle"] = retrospective_cycle
        context["has_submitted_feedback"] = has_submitted_feedback
        context["team_submission_progress"] = team_submission_progress
        context["can_reveal_feedback"] = (
            collecting_cycle is not None and can_facilitate
        )
        context["can_create_feedback_cycle"] = (
            active_cycle is None and can_facilitate
        )
        open_action_items = list(
            ActionItem.objects.filter(
                cycle__project=self.object,
                status=ActionItem.Status.OPEN,
            )
            .select_related("owner", "topic", "cycle")
            .order_by("due_date", "created_at", "id")
        )
        for action_item in open_action_items:
            action_item.can_owner_complete = action_item.owner_id == self.request.user.pk
        context["open_action_items"] = open_action_items
        return context


class ActionItemOwnerCompleteView(LoginRequiredMixin, View):
    """Let an assigned project member mark only their own open action done."""

    def get_project(self):
        if not hasattr(self, "_project"):
            self._project = get_object_or_404(
                viewable_projects_for(self.request.user),
                pk=self.kwargs["project_id"],
            )
        return self._project

    def get_cycle(self):
        if not hasattr(self, "_cycle"):
            self._cycle = get_object_or_404(
                self.get_project().feedback_cycles.all(),
                pk=self.kwargs["cycle_id"],
            )
        return self._cycle

    def get_success_url(self):
        return reverse(
            "project_dashboard",
            kwargs={"project_id": self.get_project().pk},
        )

    def post(self, request, *args, **kwargs):
        updated_count = ActionItem.objects.filter(
            cycle=self.get_cycle(),
            owner=request.user,
            pk=self.kwargs["action_item_id"],
            status=ActionItem.Status.OPEN,
        ).update(status=ActionItem.Status.DONE)
        if updated_count != 1:
            raise Http404("No completable action item matches the given query.")

        return redirect(self.get_success_url())


class FeedbackCycleCreateView(LoginRequiredMixin, FormView):
    """Facilitator-only form for starting a project's weekly feedback cycle."""

    form_class = FeedbackCycleCreateForm
    template_name = "projects/feedback_cycle_form.html"

    def get_project(self):
        if not hasattr(self, "_project"):
            self._project = get_object_or_404(
                facilitatable_projects_for(self.request.user),
                pk=self.kwargs["project_id"],
            )
        return self._project

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["project"] = self.get_project()
        return context

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["project"] = self.get_project()
        kwargs["facilitator"] = self.request.user
        return kwargs

    def form_valid(self, form):
        form.save()
        return super().form_valid(form)

    def get_success_url(self):
        return reverse(
            "project_dashboard",
            kwargs={"project_id": self.get_project().pk},
        )


class CollectingFeedbackCycleMixin(LoginRequiredMixin):
    """Resolve member-only access to a collecting feedback cycle."""

    def get_project(self):
        if not hasattr(self, "_project"):
            self._project = get_object_or_404(
                viewable_projects_for(self.request.user),
                pk=self.kwargs["project_id"],
            )
        return self._project

    def get_cycle(self):
        if not hasattr(self, "_cycle"):
            self._cycle = get_object_or_404(
                self.get_project().feedback_cycles.filter(
                    status=FeedbackCycle.Status.COLLECTING_FEEDBACK,
                ),
                pk=self.kwargs["cycle_id"],
            )
        return self._cycle

    def get_success_url(self):
        return reverse(
            "feedback_submission",
            kwargs={
                "project_id": self.get_project().pk,
                "cycle_id": self.get_cycle().pk,
            },
        )

    def render_submission(self, *, invalid_create_forms=None, invalid_edit_forms=None):
        view = FeedbackSubmissionView()
        view.setup(self.request, *self.args, **self.kwargs)
        context = view.get_context_data(
            invalid_create_forms=invalid_create_forms or {},
            invalid_edit_forms=invalid_edit_forms or {},
        )
        return view.render_to_response(context)


class FeedbackSubmissionView(CollectingFeedbackCycleMixin, TemplateView):
    """Private Start, Stop, and Continue feedback form for one contributor."""

    template_name = "projects/feedback_submission.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        project = self.get_project()
        cycle = self.get_cycle()
        invalid_create_forms = kwargs.get("invalid_create_forms", {})
        invalid_edit_forms = kwargs.get("invalid_edit_forms", {})
        own_cards = cycle.feedback_cards.filter(author=self.request.user)
        category_sections = []

        for category_value, category_label in FeedbackCard.Category.choices:
            cards = list(own_cards.filter(category=category_value))
            for card in cards:
                card.edit_form = invalid_edit_forms.get(
                    card.pk,
                    FeedbackCardForm(
                        instance=card,
                        cycle=cycle,
                        author=self.request.user,
                        auto_id=f"id_card_{card.pk}_%s",
                    ),
                )

            category_sections.append(
                {
                    "value": category_value,
                    "label": category_label,
                    "cards": cards,
                    "create_form": invalid_create_forms.get(
                        category_value,
                        FeedbackCardForm(
                            cycle=cycle,
                            author=self.request.user,
                            category=category_value,
                            auto_id=f"id_new_{category_value}_%s",
                        ),
                    ),
                }
            )

        context["project"] = project
        context["cycle"] = cycle
        context["category_sections"] = category_sections
        return context


class FeedbackCardCreateView(CollectingFeedbackCycleMixin, View):
    """Create one private feedback card in a fixed Start/Stop/Continue category."""

    def get_category(self):
        category = self.kwargs["category"]
        if category not in FeedbackCard.Category.values:
            raise Http404("No feedback category matches the given query.")
        return category

    def post(self, request, *args, **kwargs):
        category = self.get_category()
        form = FeedbackCardForm(
            request.POST,
            cycle=self.get_cycle(),
            author=request.user,
            category=category,
            auto_id=f"id_new_{category}_%s",
        )
        if form.is_valid():
            form.save()
            return redirect(self.get_success_url())

        return self.render_submission(invalid_create_forms={category: form})


class FeedbackCardUpdateView(CollectingFeedbackCycleMixin, View):
    """Update a feedback card owned by the signed-in contributor."""

    def get_card(self):
        if not hasattr(self, "_card"):
            self._card = get_object_or_404(
                FeedbackCard.objects.filter(
                    cycle=self.get_cycle(),
                    author=self.request.user,
                ),
                pk=self.kwargs["card_id"],
            )
        return self._card

    def post(self, request, *args, **kwargs):
        card = self.get_card()
        form = FeedbackCardForm(
            request.POST,
            instance=card,
            cycle=self.get_cycle(),
            author=request.user,
            auto_id=f"id_card_{card.pk}_%s",
        )
        if form.is_valid():
            form.save()
            return redirect(self.get_success_url())

        return self.render_submission(invalid_edit_forms={card.pk: form})

    def get(self, request, *args, **kwargs):
        self.get_card()
        return redirect(self.get_success_url())


class FeedbackCardDeleteView(CollectingFeedbackCycleMixin, View):
    """Delete a feedback card owned by the signed-in contributor."""

    def get_card(self):
        if not hasattr(self, "_card"):
            self._card = get_object_or_404(
                FeedbackCard.objects.filter(
                    cycle=self.get_cycle(),
                    author=self.request.user,
                ),
                pk=self.kwargs["card_id"],
            )
        return self._card

    def post(self, request, *args, **kwargs):
        self.get_card().delete()
        return redirect(self.get_success_url())

    def get(self, request, *args, **kwargs):
        self.get_card()
        return redirect(self.get_success_url())


class FeedbackCycleRevealView(LoginRequiredMixin, View):
    """Facilitator-only action that starts the retrospective board."""

    def get_project(self):
        if not hasattr(self, "_project"):
            self._project = get_object_or_404(
                facilitatable_projects_for(self.request.user),
                pk=self.kwargs["project_id"],
            )
        return self._project

    def get_cycle(self):
        if not hasattr(self, "_cycle"):
            self._cycle = get_object_or_404(
                self.get_project().feedback_cycles.filter(
                    status=FeedbackCycle.Status.COLLECTING_FEEDBACK,
                ),
                pk=self.kwargs["cycle_id"],
            )
        return self._cycle

    def post(self, request, *args, **kwargs):
        cycle = self.get_cycle()
        cycle.status = FeedbackCycle.Status.RETROSPECTIVE
        cycle.save(update_fields=["status", "updated_at"])
        return redirect(
            "retrospective_board",
            project_id=self.get_project().pk,
            cycle_id=cycle.pk,
        )


class RetrospectiveCycleMemberMixin(LoginRequiredMixin):
    """Resolve member-only access to a revealed retrospective cycle."""

    def get_project(self):
        if not hasattr(self, "_project"):
            self._project = get_object_or_404(
                viewable_projects_for(self.request.user),
                pk=self.kwargs["project_id"],
            )
        return self._project

    def get_cycle(self):
        if not hasattr(self, "_cycle"):
            self._cycle = get_object_or_404(
                self.get_project().feedback_cycles.filter(
                    status=FeedbackCycle.Status.RETROSPECTIVE,
                ),
                pk=self.kwargs["cycle_id"],
            )
        return self._cycle

    def get_success_url(self):
        return reverse(
            "retrospective_board",
            kwargs={
                "project_id": self.get_project().pk,
                "cycle_id": self.get_cycle().pk,
            },
        )


class RetrospectiveCycleFacilitatorMixin(LoginRequiredMixin):
    """Resolve facilitator-only access to a revealed retrospective cycle."""

    def get_project(self):
        if not hasattr(self, "_project"):
            self._project = get_object_or_404(
                facilitatable_projects_for(self.request.user),
                pk=self.kwargs["project_id"],
            )
        return self._project

    def get_cycle(self):
        if not hasattr(self, "_cycle"):
            self._cycle = get_object_or_404(
                self.get_project().feedback_cycles.filter(
                    status=FeedbackCycle.Status.RETROSPECTIVE,
                ),
                pk=self.kwargs["cycle_id"],
            )
        return self._cycle

    def get_success_url(self):
        return reverse(
            "retrospective_board",
            kwargs={
                "project_id": self.get_project().pk,
                "cycle_id": self.get_cycle().pk,
            },
        )

    def render_board(
        self,
        *,
        invalid_create_form=None,
        invalid_rename_forms=None,
        invalid_split_forms=None,
        merge_errors=None,
        suggestion_draft=None,
        suggestion_errors=None,
        suggestion_empty_message="",
        invalid_vote_form=None,
        invalid_discussion_forms=None,
        invalid_action_item_create_form=None,
        invalid_action_item_edit_forms=None,
        invalid_decision_create_form=None,
        invalid_decision_edit_forms=None,
        invalid_meeting_material_form=None,
        invalid_extraction_review_forms=None,
    ):
        view = RetrospectiveBoardView()
        view.setup(self.request, *self.args, **self.kwargs)
        context = view.get_context_data(
            invalid_create_form=invalid_create_form,
            invalid_rename_forms=invalid_rename_forms or {},
            invalid_split_forms=invalid_split_forms or {},
            merge_errors=merge_errors or {},
            suggestion_draft=suggestion_draft,
            suggestion_errors=suggestion_errors or [],
            suggestion_empty_message=suggestion_empty_message,
            invalid_vote_form=invalid_vote_form,
            invalid_discussion_forms=invalid_discussion_forms or {},
            invalid_action_item_create_form=invalid_action_item_create_form,
            invalid_action_item_edit_forms=invalid_action_item_edit_forms or {},
            invalid_decision_create_form=invalid_decision_create_form,
            invalid_decision_edit_forms=invalid_decision_edit_forms or {},
            invalid_meeting_material_form=invalid_meeting_material_form,
            invalid_extraction_review_forms=invalid_extraction_review_forms or {},
        )
        return view.render_to_response(context)

    def require_mutable_clustering(self):
        if self.get_cycle().voting_status != FeedbackCycle.VotingStatus.CLUSTERING:
            raise Http404("No editable clustering stage matches the given query.")

    def require_mutable_discussion(self):
        if self.get_cycle().voting_status != FeedbackCycle.VotingStatus.CLOSED:
            raise Http404("No editable discussion stage matches the given query.")

    def require_posted_action_scope(self):
        owner_id = _posted_positive_int_or_404(
            self.request.POST,
            "owner",
            "No action item owner matches the given query.",
        )
        if owner_id is not None:
            owner_is_active_project_member = Membership.objects.filter(
                project=self.get_project(),
                user_id=owner_id,
                user__is_active=True,
            ).exists()
            if not owner_is_active_project_member:
                raise Http404("No action item owner matches the given query.")

        topic_id = _posted_positive_int_or_404(
            self.request.POST,
            "topic",
            "No action item topic matches the given query.",
        )
        if topic_id is not None and not self.get_cycle().feedback_clusters.filter(
            pk=topic_id
        ).exists():
            raise Http404("No action item topic matches the given query.")

    def require_posted_decision_scope(self):
        topic_id = _posted_positive_int_or_404(
            self.request.POST,
            "topic",
            "No decision topic matches the given query.",
        )
        if topic_id is not None and not self.get_cycle().feedback_clusters.filter(
            pk=topic_id
        ).exists():
            raise Http404("No decision topic matches the given query.")

    def require_posted_extraction_review_scope(self, extraction_draft):
        _posted_exact_int_or_404(
            self.request.POST,
            "material_id",
            extraction_draft.meeting_material_id,
            "No extraction draft matches the given query.",
        )
        _posted_exact_int_or_404(
            self.request.POST,
            "extraction_draft_id",
            extraction_draft.pk,
            "No extraction draft matches the given query.",
        )
        draft_decision_ids = set(
            extraction_draft.draft_decisions.values_list("id", flat=True)
        )
        draft_action_ids = set(
            extraction_draft.draft_action_items.values_list("id", flat=True)
        )
        _posted_exact_int_or_404(
            self.request.POST,
            "draft_decision_count",
            len(draft_decision_ids),
            "No extraction draft matches the given query.",
        )
        _posted_exact_int_or_404(
            self.request.POST,
            "draft_action_item_count",
            len(draft_action_ids),
            "No extraction draft matches the given query.",
        )
        _posted_review_object_ids_or_404(
            self.request.POST,
            prefix="decision_",
            suffixes={"text", "topic"},
            valid_ids=draft_decision_ids,
            message="No draft decision matches the given query.",
        )
        _posted_review_object_ids_or_404(
            self.request.POST,
            prefix="action_",
            suffixes={"description", "owner", "due_date", "topic"},
            valid_ids=draft_action_ids,
            message="No draft action item matches the given query.",
        )

        valid_topic_ids = set(
            self.get_cycle().feedback_clusters.values_list("id", flat=True)
        )
        valid_owner_ids = set(
            Membership.objects.filter(
                project=self.get_project(),
                user__is_active=True,
            ).values_list("user_id", flat=True)
        )

        for decision_id in draft_decision_ids:
            topic_id = _posted_positive_int_or_404(
                self.request.POST,
                f"decision_{decision_id}_topic",
                "No review topic matches the given query.",
            )
            if topic_id is not None and topic_id not in valid_topic_ids:
                raise Http404("No review topic matches the given query.")

        for action_id in draft_action_ids:
            owner_id = _posted_positive_int_or_404(
                self.request.POST,
                f"action_{action_id}_owner",
                "No review owner matches the given query.",
            )
            if owner_id is not None and owner_id not in valid_owner_ids:
                raise Http404("No review owner matches the given query.")

            topic_id = _posted_positive_int_or_404(
                self.request.POST,
                f"action_{action_id}_topic",
                "No review topic matches the given query.",
            )
            if topic_id is not None and topic_id not in valid_topic_ids:
                raise Http404("No review topic matches the given query.")


class RetrospectiveBoardView(RetrospectiveCycleMemberMixin, TemplateView):
    """Revealed board for Start, Stop, and Continue feedback themes."""

    template_name = "projects/retrospective_board.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        cycle = self.get_cycle()
        board_context = retrospective_board_context_for(cycle)
        invalid_rename_forms = kwargs.get("invalid_rename_forms", {})
        invalid_split_forms = kwargs.get("invalid_split_forms", {})
        merge_errors = kwargs.get("merge_errors", {})
        invalid_discussion_forms = kwargs.get("invalid_discussion_forms", {})
        invalid_action_item_edit_forms = kwargs.get("invalid_action_item_edit_forms", {})
        invalid_decision_edit_forms = kwargs.get("invalid_decision_edit_forms", {})
        invalid_extraction_review_forms = kwargs.get(
            "invalid_extraction_review_forms",
            {},
        )
        can_facilitate = can_facilitate_project(self.request.user, self.get_project())
        can_manage_clusters = (
            can_facilitate
            and cycle.voting_status == FeedbackCycle.VotingStatus.CLUSTERING
        )
        can_manage_discussion = (
            can_facilitate
            and cycle.voting_status == FeedbackCycle.VotingStatus.CLOSED
        )

        for cluster in board_context["clusters"]:
            cluster_id = cluster["id"]
            cluster["rename_form"] = invalid_rename_forms.get(
                cluster_id,
                FeedbackClusterForm(
                    instance=cluster["object"],
                    cycle=cycle,
                    auto_id=f"id_cluster_{cluster_id}_%s",
                ),
            )
            cluster["split_form"] = invalid_split_forms.get(
                cluster_id,
                FeedbackClusterSplitForm(
                    cluster=cluster["object"],
                    auto_id=f"id_split_{cluster_id}_%s",
                ),
            )
            cluster["merge_error"] = merge_errors.get(cluster_id)

        context["project"] = self.get_project()
        context["cycle"] = cycle
        context["can_facilitate"] = can_facilitate
        context["can_manage_clusters"] = can_manage_clusters
        context["cluster_create_form"] = kwargs.get(
            "invalid_create_form",
            FeedbackClusterForm(cycle=cycle, auto_id="id_new_cluster_%s"),
        )
        context["clusters"] = board_context["clusters"]
        context["cluster_options"] = [
            {"id": cluster["id"], "name": cluster["name"]}
            for cluster in board_context["clusters"]
        ]
        context["category_sections"] = board_context["ungrouped_sections"]
        context["voting_status"] = cycle.voting_status
        context["voting_is_clustering"] = (
            cycle.voting_status == FeedbackCycle.VotingStatus.CLUSTERING
        )
        context["voting_is_open"] = (
            cycle.voting_status == FeedbackCycle.VotingStatus.OPEN
        )
        context["voting_is_closed"] = (
            cycle.voting_status == FeedbackCycle.VotingStatus.CLOSED
        )
        context["can_open_voting"] = can_manage_clusters and bool(
            board_context["clusters"]
        )
        context["can_close_voting"] = (
            can_facilitate
            and cycle.voting_status == FeedbackCycle.VotingStatus.OPEN
        )
        vote_initial = {
            f"cluster_{cluster_id}_votes": vote_count
            for cluster_id, vote_count in saved_vote_allocation_for(
                cycle, self.request.user
            ).items()
        }
        context["vote_form"] = kwargs.get(
            "invalid_vote_form",
            FeedbackClusterVoteForm(
                cycle=cycle,
                initial=vote_initial,
                auto_id="id_vote_%s",
            ),
        )
        context["voting_progress"] = (
            voting_progress_for(cycle) if context["can_close_voting"] else []
        )
        ranked_clusters = (
            ranked_clusters_for(cycle)
            if cycle.voting_status == FeedbackCycle.VotingStatus.CLOSED
            else []
        )
        for topic in ranked_clusters:
            cluster = topic["object"]
            topic["discussion_status"] = cluster.discussion_status
            topic["discussion_status_label"] = cluster.get_discussion_status_display()
            topic["discussion_notes"] = cluster.discussion_notes
            topic["discussion_form"] = invalid_discussion_forms.get(
                cluster.pk,
                FeedbackClusterDiscussionForm(
                    cluster=cluster,
                    initial={
                        "discussion_status": cluster.discussion_status,
                        "discussion_notes": cluster.discussion_notes,
                    },
                    auto_id=f"id_discussion_{cluster.pk}_%s",
                ),
            )
        context["ranked_clusters"] = ranked_clusters
        context["discussion_topics"] = ranked_clusters
        context["can_manage_discussion"] = can_manage_discussion
        action_items = []
        decisions = []
        if cycle.voting_status == FeedbackCycle.VotingStatus.CLOSED:
            action_items = list(
                cycle.action_items.select_related("owner", "topic").order_by(
                    "due_date",
                    "created_at",
                    "id",
                )
            )
            decisions = list(
                cycle.decisions.select_related("topic").order_by("created_at", "id")
            )
            if can_manage_discussion:
                for action_item in action_items:
                    action_item.edit_form = invalid_action_item_edit_forms.get(
                        action_item.pk,
                        ActionItemForm(
                            instance=action_item,
                            cycle=cycle,
                            auto_id=f"id_action_{action_item.pk}_%s",
                        ),
                    )
                for decision in decisions:
                    decision.edit_form = invalid_decision_edit_forms.get(
                        decision.pk,
                        RetrospectiveDecisionForm(
                            instance=decision,
                            cycle=cycle,
                            auto_id=f"id_decision_{decision.pk}_%s",
                        ),
                    )
        context["action_items"] = action_items
        context["decisions"] = decisions
        context["action_item_create_form"] = kwargs.get(
            "invalid_action_item_create_form",
            ActionItemForm(cycle=cycle, auto_id="id_new_action_%s"),
        )
        context["decision_create_form"] = kwargs.get(
            "invalid_decision_create_form",
            RetrospectiveDecisionForm(cycle=cycle, auto_id="id_new_decision_%s"),
        )
        meeting_materials = []
        if cycle.voting_status == FeedbackCycle.VotingStatus.CLOSED:
            meeting_materials = list(
                cycle.meeting_materials.select_related(
                    "submitted_by",
                    "processed_transcript",
                    "extraction_draft",
                )
                .prefetch_related(
                    "extraction_draft__draft_decisions__matched_topic",
                    "extraction_draft__draft_action_items__matched_owner",
                    "extraction_draft__draft_action_items__matched_topic",
                )
                .order_by("-created_at", "-id")
            )
            if can_manage_discussion:
                for material in meeting_materials:
                    try:
                        extraction_draft = material.extraction_draft
                    except MeetingMaterialExtractionDraft.DoesNotExist:
                        continue
                    extraction_draft.can_review = (
                        material.processing_status
                        == MeetingMaterial.ProcessingStatus.SUCCEEDED
                        and extraction_draft.review_status
                        == MeetingMaterialExtractionDraft.ReviewStatus.PENDING
                    )
                    if extraction_draft.can_review:
                        extraction_draft.review_form = (
                            invalid_extraction_review_forms.get(
                                extraction_draft.pk,
                                MeetingMaterialExtractionDraftReviewForm(
                                    extraction_draft=extraction_draft,
                                    auto_id=f"id_extraction_review_{extraction_draft.pk}_%s",
                                ),
                            )
                        )
        context["meeting_materials"] = meeting_materials
        context["meeting_material_form"] = kwargs.get(
            "invalid_meeting_material_form",
            MeetingMaterialForm(
                cycle=cycle,
                submitter=self.request.user,
                auto_id="id_meeting_material_%s",
            ),
        )
        suggestion_draft = kwargs.get("suggestion_draft")
        if suggestion_draft is None and can_manage_clusters:
            suggestion_draft = _saved_suggestion_draft(self.request, cycle)
        context["suggestion_draft"] = None
        if can_manage_clusters and suggestion_draft is not None:
            context["suggestion_draft"] = suggestion_draft_context_for(
                cycle,
                suggestion_draft,
                errors=kwargs.get("suggestion_errors", []),
                empty_message=kwargs.get("suggestion_empty_message", ""),
            )
        return context


class MeetingMaterialCreateView(RetrospectiveCycleFacilitatorMixin, View):
    """Create one facilitator-submitted meeting material record."""

    def post(self, request, *args, **kwargs):
        self.require_mutable_discussion()
        try:
            form = MeetingMaterialForm(
                request.POST,
                request.FILES,
                cycle=self.get_cycle(),
                submitter=request.user,
                auto_id="id_meeting_material_%s",
            )
        except RequestDataTooBig:
            form = MeetingMaterialForm(
                {},
                {},
                cycle=self.get_cycle(),
                submitter=request.user,
                auto_id="id_meeting_material_%s",
            )
            form.is_valid()
            form.add_error(
                None,
                "Pasted transcript exceeds the configured upload limit.",
            )
            return self.render_board(invalid_meeting_material_form=form)

        if form.is_valid():
            material = form.save()
            try:
                enqueue_meeting_material_processing(material)
            except Exception as exc:
                material.processing_status = MeetingMaterial.ProcessingStatus.FAILED
                material.failure_message = sanitize_processing_error(exc)
                material.save(
                    update_fields=[
                        "processing_status",
                        "failure_message",
                        "updated_at",
                    ]
                )
            return redirect(self.get_success_url())

        return self.render_board(invalid_meeting_material_form=form)


class MeetingMaterialRetryView(RetrospectiveCycleFacilitatorMixin, View):
    """Queue a failed meeting material for another background processing attempt."""

    def get_material(self):
        if not hasattr(self, "_material"):
            self._material = get_object_or_404(
                self.get_cycle().meeting_materials.all(),
                pk=self.kwargs["meeting_material_id"],
            )
        return self._material

    def post(self, request, *args, **kwargs):
        self.require_mutable_discussion()
        material = self.get_material()
        try:
            retry_meeting_material_processing(material)
        except ValueError as exc:
            raise Http404("No retryable meeting material matches the given query.") from exc
        except Exception as exc:
            material.processing_status = MeetingMaterial.ProcessingStatus.FAILED
            material.failure_message = sanitize_processing_error(exc)
            material.save(
                update_fields=[
                    "processing_status",
                    "failure_message",
                    "updated_at",
                ]
            )
        return redirect(self.get_success_url())


class MeetingMaterialExtractionDraftMixin(RetrospectiveCycleFacilitatorMixin):
    """Resolve one pending succeeded meeting-material extraction draft."""

    def get_extraction_draft(self):
        if not hasattr(self, "_extraction_draft"):
            self._extraction_draft = get_object_or_404(
                MeetingMaterialExtractionDraft.objects.select_related(
                    "meeting_material",
                    "meeting_material__cycle",
                    "meeting_material__cycle__project",
                ).filter(
                    meeting_material__cycle=self.get_cycle(),
                    meeting_material_id=self.kwargs["meeting_material_id"],
                    meeting_material__processing_status=(
                        MeetingMaterial.ProcessingStatus.SUCCEEDED
                    ),
                    review_status=MeetingMaterialExtractionDraft.ReviewStatus.PENDING,
                ),
                pk=self.kwargs["extraction_draft_id"],
            )
        return self._extraction_draft


class MeetingMaterialExtractionDraftApproveView(MeetingMaterialExtractionDraftMixin, View):
    """Approve one reviewed extraction draft into confirmed retrospective outcomes."""

    def post(self, request, *args, **kwargs):
        self.require_mutable_discussion()
        extraction_draft = self.get_extraction_draft()
        self.require_posted_extraction_review_scope(extraction_draft)
        form = MeetingMaterialExtractionDraftReviewForm(
            request.POST,
            extraction_draft=extraction_draft,
            auto_id=f"id_extraction_review_{extraction_draft.pk}_%s",
        )
        if not form.is_valid():
            return self.render_board(
                invalid_extraction_review_forms={extraction_draft.pk: form}
            )

        with transaction.atomic():
            locked_draft = get_object_or_404(
                MeetingMaterialExtractionDraft.objects.select_for_update()
                .select_related(
                    "meeting_material",
                    "meeting_material__cycle",
                    "meeting_material__cycle__project",
                )
                .filter(
                    meeting_material__cycle=self.get_cycle(),
                    meeting_material_id=self.kwargs["meeting_material_id"],
                    meeting_material__processing_status=(
                        MeetingMaterial.ProcessingStatus.SUCCEEDED
                    ),
                    review_status=MeetingMaterialExtractionDraft.ReviewStatus.PENDING,
                ),
                pk=self.kwargs["extraction_draft_id"],
            )
            locked_form = MeetingMaterialExtractionDraftReviewForm(
                request.POST,
                extraction_draft=locked_draft,
                auto_id=f"id_extraction_review_{locked_draft.pk}_%s",
            )
            if not locked_form.is_valid():
                return self.render_board(
                    invalid_extraction_review_forms={locked_draft.pk: locked_form}
                )
            locked_form.save()

        return redirect(self.get_success_url())


class MeetingMaterialExtractionDraftDiscardView(MeetingMaterialExtractionDraftMixin, View):
    """Discard one pending extraction draft without creating confirmed outcomes."""

    def post(self, request, *args, **kwargs):
        self.require_mutable_discussion()
        extraction_draft = self.get_extraction_draft()
        updated_count = MeetingMaterialExtractionDraft.objects.filter(
            pk=extraction_draft.pk,
            review_status=MeetingMaterialExtractionDraft.ReviewStatus.PENDING,
            meeting_material__cycle=self.get_cycle(),
            meeting_material__processing_status=(
                MeetingMaterial.ProcessingStatus.SUCCEEDED
            ),
        ).update(review_status=MeetingMaterialExtractionDraft.ReviewStatus.DISCARDED)
        if updated_count != 1:
            raise Http404("No extraction draft matches the given query.")
        return redirect(self.get_success_url())


class FeedbackClusterDiscussionUpdateView(RetrospectiveCycleFacilitatorMixin, View):
    """Save facilitator-managed discussion status and notes for one topic."""

    def get_cluster(self):
        if not hasattr(self, "_cluster"):
            self._cluster = get_object_or_404(
                self.get_cycle().feedback_clusters.all(),
                pk=self.kwargs["cluster_id"],
            )
        return self._cluster

    def post(self, request, *args, **kwargs):
        self.require_mutable_discussion()
        cluster = self.get_cluster()
        form = FeedbackClusterDiscussionForm(
            request.POST,
            cluster=cluster,
            auto_id=f"id_discussion_{cluster.pk}_%s",
        )
        if form.is_valid():
            form.save()
            return redirect(self.get_success_url())

        return self.render_board(invalid_discussion_forms={cluster.pk: form})


class ActionItemCreateView(RetrospectiveCycleFacilitatorMixin, View):
    """Create one facilitator-managed manual action item for the discussion."""

    def post(self, request, *args, **kwargs):
        self.require_mutable_discussion()
        self.require_posted_action_scope()
        form = ActionItemForm(
            request.POST,
            cycle=self.get_cycle(),
            auto_id="id_new_action_%s",
        )
        if form.is_valid():
            form.save()
            return redirect(self.get_success_url())

        return self.render_board(invalid_action_item_create_form=form)


class ActionItemUpdateView(RetrospectiveCycleFacilitatorMixin, View):
    """Update one action item scoped to the requested retrospective cycle."""

    def get_action_item(self):
        if not hasattr(self, "_action_item"):
            self._action_item = get_object_or_404(
                self.get_cycle().action_items.all(),
                pk=self.kwargs["action_item_id"],
            )
        return self._action_item

    def post(self, request, *args, **kwargs):
        self.require_mutable_discussion()
        action_item = self.get_action_item()
        self.require_posted_action_scope()
        form = ActionItemForm(
            request.POST,
            instance=action_item,
            cycle=self.get_cycle(),
            auto_id=f"id_action_{action_item.pk}_%s",
        )
        if form.is_valid():
            form.save()
            return redirect(self.get_success_url())

        return self.render_board(invalid_action_item_edit_forms={action_item.pk: form})


class RetrospectiveDecisionCreateView(RetrospectiveCycleFacilitatorMixin, View):
    """Create one facilitator-confirmed manual decision for the discussion."""

    def post(self, request, *args, **kwargs):
        self.require_mutable_discussion()
        self.require_posted_decision_scope()
        form = RetrospectiveDecisionForm(
            request.POST,
            cycle=self.get_cycle(),
            auto_id="id_new_decision_%s",
        )
        if form.is_valid():
            form.save()
            return redirect(self.get_success_url())

        return self.render_board(invalid_decision_create_form=form)


class RetrospectiveDecisionUpdateView(RetrospectiveCycleFacilitatorMixin, View):
    """Update one confirmed decision scoped to the requested cycle."""

    def get_decision(self):
        if not hasattr(self, "_decision"):
            self._decision = get_object_or_404(
                self.get_cycle().decisions.all(),
                pk=self.kwargs["decision_id"],
            )
        return self._decision

    def post(self, request, *args, **kwargs):
        self.require_mutable_discussion()
        decision = self.get_decision()
        self.require_posted_decision_scope()
        form = RetrospectiveDecisionForm(
            request.POST,
            instance=decision,
            cycle=self.get_cycle(),
            auto_id=f"id_decision_{decision.pk}_%s",
        )
        if form.is_valid():
            form.save()
            return redirect(self.get_success_url())

        return self.render_board(invalid_decision_edit_forms={decision.pk: form})


class FeedbackClusterCreateView(RetrospectiveCycleFacilitatorMixin, View):
    """Create one manual cluster on a revealed retrospective board."""

    def post(self, request, *args, **kwargs):
        self.require_mutable_clustering()
        form = FeedbackClusterForm(
            request.POST,
            cycle=self.get_cycle(),
            auto_id="id_new_cluster_%s",
        )
        if form.is_valid():
            form.save()
            return redirect(self.get_success_url())

        return self.render_board(invalid_create_form=form)


class FeedbackClusterRenameView(RetrospectiveCycleFacilitatorMixin, View):
    """Rename one cluster in the requested revealed feedback cycle."""

    def get_cluster(self):
        if not hasattr(self, "_cluster"):
            self._cluster = get_object_or_404(
                self.get_cycle().feedback_clusters.all(),
                pk=self.kwargs["cluster_id"],
            )
        return self._cluster

    def post(self, request, *args, **kwargs):
        self.require_mutable_clustering()
        cluster = self.get_cluster()
        form = FeedbackClusterForm(
            request.POST,
            instance=cluster,
            cycle=self.get_cycle(),
            auto_id=f"id_cluster_{cluster.pk}_%s",
        )
        if form.is_valid():
            form.save()
            return redirect(self.get_success_url())

        return self.render_board(invalid_rename_forms={cluster.pk: form})


class FeedbackCardClusterMoveView(RetrospectiveCycleFacilitatorMixin, View):
    """Move one revealed feedback card into a cluster or back to ungrouped."""

    def get_card(self):
        if not hasattr(self, "_card"):
            self._card = get_object_or_404(
                FeedbackCard.objects.filter(cycle=self.get_cycle()),
                pk=self.kwargs["card_id"],
            )
        return self._card

    def get_target_cluster(self):
        target_cluster_id = self.request.POST.get("cluster")
        if not target_cluster_id:
            return None
        return get_object_or_404(
            self.get_cycle().feedback_clusters.all(),
            pk=target_cluster_id,
        )

    def post(self, request, *args, **kwargs):
        self.require_mutable_clustering()
        card = self.get_card()
        target_cluster = self.get_target_cluster()
        FeedbackCard.objects.filter(pk=card.pk).update(cluster=target_cluster)
        return redirect(self.get_success_url())


class FeedbackClusterMergeView(RetrospectiveCycleFacilitatorMixin, View):
    """Merge a source cluster into another cluster on the same board."""

    def get_source_cluster(self):
        if not hasattr(self, "_source_cluster"):
            self._source_cluster = get_object_or_404(
                self.get_cycle().feedback_clusters.all(),
                pk=self.kwargs["cluster_id"],
            )
        return self._source_cluster

    def get_target_cluster(self):
        target_cluster_id = self.request.POST.get("target_cluster")
        return get_object_or_404(
            self.get_cycle().feedback_clusters.all(),
            pk=target_cluster_id,
        )

    def post(self, request, *args, **kwargs):
        self.require_mutable_clustering()
        source_cluster = self.get_source_cluster()
        target_cluster = self.get_target_cluster()
        if source_cluster.pk == target_cluster.pk:
            return self.render_board(
                merge_errors={
                    source_cluster.pk: "Choose a different cluster to merge into."
                }
            )

        FeedbackCard.objects.filter(
            cycle=self.get_cycle(),
            cluster=source_cluster,
        ).update(cluster=target_cluster)
        source_cluster.delete()
        return redirect(self.get_success_url())


class FeedbackClusterSplitView(RetrospectiveCycleFacilitatorMixin, View):
    """Split selected cards from one cluster into a new manual cluster."""

    def get_cluster(self):
        if not hasattr(self, "_cluster"):
            self._cluster = get_object_or_404(
                self.get_cycle().feedback_clusters.all(),
                pk=self.kwargs["cluster_id"],
            )
        return self._cluster

    def post(self, request, *args, **kwargs):
        self.require_mutable_clustering()
        cluster = self.get_cluster()
        form = FeedbackClusterSplitForm(
            request.POST,
            cluster=cluster,
            auto_id=f"id_split_{cluster.pk}_%s",
        )
        if not form.is_valid():
            return self.render_board(invalid_split_forms={cluster.pk: form})

        new_cluster = FeedbackCluster.objects.create(
            cycle=self.get_cycle(),
            name=form.cleaned_data["name"],
        )
        form.cleaned_data["cards"].update(cluster=new_cluster)
        return redirect(self.get_success_url())


class FeedbackClusterSuggestionGenerateView(RetrospectiveCycleFacilitatorMixin, View):
    """Create or refresh an editable draft of local clustering suggestions."""

    def post(self, request, *args, **kwargs):
        self.require_mutable_clustering()
        cycle = self.get_cycle()
        if not cycle.feedback_cards.exists():
            draft = {
                "clusters": [],
                "empty_message": "No revealed feedback cards are available for suggestions.",
            }
            _save_suggestion_draft(request, cycle, draft)
            return self.render_board(suggestion_draft=draft)

        suggestions = get_clustering_service().suggest_clusters(cycle)
        draft = draft_from_suggestions(suggestions)
        if not draft["clusters"]:
            draft["empty_message"] = "No clustering suggestions were found."
        _save_suggestion_draft(request, cycle, draft)
        return self.render_board(suggestion_draft=draft)


class FeedbackClusterSuggestionEditView(RetrospectiveCycleFacilitatorMixin, View):
    """Preview facilitator edits to draft suggestion names and card membership."""

    def post(self, request, *args, **kwargs):
        self.require_mutable_clustering()
        form = FeedbackClusterSuggestionDraftForm(
            request.POST,
            cycle=self.get_cycle(),
        )
        draft = _draft_from_post_for_render(request.POST, self.get_cycle())
        if not form.is_valid():
            return self.render_board(
                suggestion_draft=draft,
                suggestion_errors=_form_errors(form),
            )

        draft = form.draft()
        _save_suggestion_draft(request, self.get_cycle(), draft)
        return self.render_board(suggestion_draft=draft)


class FeedbackClusterSuggestionAcceptView(RetrospectiveCycleFacilitatorMixin, View):
    """Accept an edited draft as regular manual clusters on the board."""

    def post(self, request, *args, **kwargs):
        self.require_mutable_clustering()
        cycle = self.get_cycle()
        form = FeedbackClusterSuggestionDraftForm(
            request.POST,
            cycle=cycle,
            require_clusters=True,
        )
        draft = _draft_from_post_for_render(request.POST, cycle)
        if not form.is_valid():
            return self.render_board(
                suggestion_draft=draft,
                suggestion_errors=_form_errors(form),
            )

        draft = form.draft()
        with transaction.atomic():
            for suggested_cluster in draft["clusters"]:
                cluster = FeedbackCluster.objects.create(
                    cycle=cycle,
                    name=suggested_cluster["name"],
                )
                FeedbackCard.objects.filter(
                    cycle=cycle,
                    pk__in=suggested_cluster["card_ids"],
                ).update(cluster=cluster)

        _clear_suggestion_draft(request, cycle)
        return redirect(self.get_success_url())


class FeedbackClusterSuggestionIgnoreView(RetrospectiveCycleFacilitatorMixin, View):
    """Discard the current draft suggestions without changing board state."""

    def post(self, request, *args, **kwargs):
        self.require_mutable_clustering()
        _clear_suggestion_draft(request, self.get_cycle())
        return redirect(self.get_success_url())


class FeedbackCycleVotingOpenView(RetrospectiveCycleFacilitatorMixin, View):
    """Facilitator-only action that opens voting after clustering."""

    def post(self, request, *args, **kwargs):
        if not open_voting(self.get_cycle()):
            raise Http404("No openable voting stage matches the given query.")
        return redirect(self.get_success_url())


class FeedbackCycleVotingCloseView(RetrospectiveCycleFacilitatorMixin, View):
    """Facilitator-only action that closes voting early."""

    def post(self, request, *args, **kwargs):
        if not close_voting(self.get_cycle()):
            raise Http404("No closable voting stage matches the given query.")
        return redirect(self.get_success_url())


class FeedbackCycleVoteSubmitView(RetrospectiveCycleMemberMixin, View):
    """Save one member's three-vote allocation for the revealed cycle."""

    def post(self, request, *args, **kwargs):
        cycle = self.get_cycle()
        if (
            cycle.voting_status != FeedbackCycle.VotingStatus.OPEN
            or not cycle.feedback_clusters.exists()
        ):
            raise Http404("No votable cycle matches the given query.")

        valid_cluster_ids = set(cycle.feedback_clusters.values_list("id", flat=True))
        if _posted_vote_cluster_ids(request.POST) - valid_cluster_ids:
            raise Http404("No vote cluster matches the given query.")

        form = FeedbackClusterVoteForm(
            request.POST,
            cycle=cycle,
            auto_id="id_vote_%s",
        )
        if not form.is_valid():
            return RetrospectiveCycleFacilitatorMixin.render_board(
                self,
                invalid_vote_form=form,
            )

        if not save_vote_allocation(cycle, request.user, form.cleaned_data["allocations"]):
            raise Http404("No votable cycle matches the given query.")
        return redirect(self.get_success_url())
