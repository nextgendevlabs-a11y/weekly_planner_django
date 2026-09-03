"""Page views for the weekly planner project."""

from django.contrib.auth import login
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.urls import reverse_lazy
from django.views.generic import DetailView
from django.views.generic import FormView
from django.views.generic import TemplateView
from django.views.generic import View

from projects.forms import FeedbackCardForm, FeedbackCycleCreateForm
from projects.models import FeedbackCard, FeedbackCycle, Project
from projects.permissions import can_facilitate_project, facilitatable_projects_for
from projects.permissions import viewable_projects_for
from projects.retrospective_board import retrospective_board_sections_for
from projects.submission_progress import submission_progress_for


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
        return context


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


class RetrospectiveBoardView(LoginRequiredMixin, TemplateView):
    """First revealed board for Start, Stop, and Continue feedback."""

    template_name = "projects/retrospective_board.html"

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

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        cycle = self.get_cycle()
        context["project"] = self.get_project()
        context["cycle"] = cycle
        context["category_sections"] = retrospective_board_sections_for(cycle)
        return context
