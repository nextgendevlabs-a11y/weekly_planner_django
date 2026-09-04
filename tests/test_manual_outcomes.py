from html.parser import HTMLParser
from datetime import date

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import timezone

from projects.forms import ActionItemForm, RetrospectiveDecisionForm
from projects.models import (
    ActionItem,
    FeedbackCard,
    FeedbackCluster,
    FeedbackClusterVote,
    FeedbackCycle,
    Membership,
    Project,
    RetrospectiveDecision,
)


pytestmark = pytest.mark.django_db


class BoardParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.forms = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "form" and "action" in attributes:
            self.forms.append(
                {
                    "action": attributes["action"],
                    "method": attributes.get("method", "get").lower(),
                }
            )


def create_user(username="member", *, is_active=True, is_staff=False, is_superuser=False):
    return get_user_model().objects.create_user(
        username=username,
        password="UsablePass123!",
        is_active=is_active,
        is_staff=is_staff,
        is_superuser=is_superuser,
    )


def create_project(name="Weekly Ops"):
    return Project.objects.create(name=name)


def add_membership(user, project, role=Membership.Role.TEAM_MEMBER):
    return Membership.objects.create(user=user, project=project, role=role)


def create_cycle(
    project,
    facilitator,
    *,
    label="Week 34 Retrospective",
    status=FeedbackCycle.Status.RETROSPECTIVE,
    voting_status=FeedbackCycle.VotingStatus.CLOSED,
):
    return FeedbackCycle.objects.create(
        project=project,
        facilitator=facilitator,
        label=label,
        status=status,
        voting_status=voting_status,
        opens_at=timezone.now(),
    )


def create_cluster(cycle, name="Release readiness", **kwargs):
    return FeedbackCluster.objects.create(cycle=cycle, name=name, **kwargs)


def create_card(cycle, author, *, text="Keep this card", cluster=None):
    return FeedbackCard.objects.create(
        cycle=cycle,
        author=author,
        category=FeedbackCard.Category.START,
        text=text,
        cluster=cluster,
    )


def create_vote(cycle, voter, cluster, vote_count=3):
    return FeedbackClusterVote.objects.create(
        cycle=cycle,
        voter=voter,
        cluster=cluster,
        vote_count=vote_count,
    )


def create_action_item(
    cycle,
    owner,
    topic,
    *,
    description="Follow up on release risk",
    status=ActionItem.Status.OPEN,
    due_date=None,
):
    return ActionItem.objects.create(
        cycle=cycle,
        owner=owner,
        topic=topic,
        description=description,
        status=status,
        due_date=due_date,
    )


def create_decision(cycle, *, text="Keep the current release checklist", topic=None):
    return RetrospectiveDecision.objects.create(cycle=cycle, text=text, topic=topic)


def board_path(project, cycle):
    return reverse(
        "retrospective_board",
        kwargs={"project_id": project.pk, "cycle_id": cycle.pk},
    )


def dashboard_path(project):
    return reverse("project_dashboard", kwargs={"project_id": project.pk})


def action_create_path(project, cycle):
    return reverse(
        "action_item_create",
        kwargs={"project_id": project.pk, "cycle_id": cycle.pk},
    )


def action_update_path(project, cycle, action_item):
    return reverse(
        "action_item_update",
        kwargs={
            "project_id": project.pk,
            "cycle_id": cycle.pk,
            "action_item_id": action_item.pk,
        },
    )


def decision_create_path(project, cycle):
    return reverse(
        "retrospective_decision_create",
        kwargs={"project_id": project.pk, "cycle_id": cycle.pk},
    )


def decision_update_path(project, cycle, decision):
    return reverse(
        "retrospective_decision_update",
        kwargs={
            "project_id": project.pk,
            "cycle_id": cycle.pk,
            "decision_id": decision.pk,
        },
    )


def parser_from(response):
    parser = BoardParser()
    parser.feed(response.content.decode())
    return parser


def assert_no_secret_leak(response, secrets):
    content = response.content.decode()
    for secret in secrets:
        assert secret not in content


def action_payload(
    owner,
    topic,
    *,
    description="  Follow up on release risk  ",
    due_date="",
    status=None,
):
    data = {
        "description": description,
        "owner": str(owner.pk),
        "due_date": due_date,
        "topic": str(topic.pk),
    }
    if status is not None:
        data["status"] = status
    return data


def decision_payload(*, text="  Keep the current release checklist  ", topic=None):
    return {
        "text": text,
        "topic": "" if topic is None else str(topic.pk),
    }


def test_action_item_and_decision_models_and_forms_validate_scope_and_required_fields():
    facilitator = create_user("facilitator")
    owner = create_user("owner")
    inactive_member = create_user("inactive-member", is_active=False)
    non_member = create_user("non-member")
    other_owner = create_user("other-owner")
    project = create_project("Outcome Model Project")
    other_project = create_project("Other Outcome Project")
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    add_membership(owner, project)
    add_membership(inactive_member, project)
    add_membership(other_owner, other_project)
    cycle = create_cycle(project, facilitator)
    topic = create_cluster(cycle, "Planning")
    other_cycle = create_cycle(other_project, facilitator, label="Other Week")
    other_topic = create_cluster(other_cycle, "Other planning")

    for status in [ActionItem.Status.OPEN, ActionItem.Status.DONE]:
        action = ActionItem(
            cycle=cycle,
            owner=owner,
            topic=topic,
            description="  Valid action  ",
            status=status,
        )
        action.full_clean()
        assert action.description == "Valid action"

    invalid_status = ActionItem(
        cycle=cycle,
        owner=owner,
        topic=topic,
        description="Invalid status",
        status="blocked",
    )
    with pytest.raises(ValidationError) as status_error:
        invalid_status.full_clean()
    assert "status" in status_error.value.message_dict

    with pytest.raises(IntegrityError), transaction.atomic():
        create_action_item(cycle, owner, topic, status="blocked")

    for bad_owner in [inactive_member, non_member, other_owner]:
        action = ActionItem(
            cycle=cycle,
            owner=bad_owner,
            topic=topic,
            description="Bad owner",
        )
        with pytest.raises(ValidationError) as owner_error:
            action.full_clean()
        assert "owner" in owner_error.value.message_dict

    action = ActionItem(
        cycle=cycle,
        owner=owner,
        topic=other_topic,
        description="Bad topic",
    )
    with pytest.raises(ValidationError) as topic_error:
        action.full_clean()
    assert "same feedback cycle" in topic_error.value.message_dict["topic"][0]

    valid_form = ActionItemForm(
        action_payload(owner, topic, status=None),
        cycle=cycle,
    )
    assert valid_form.is_valid() is True
    saved_action = valid_form.save()
    assert saved_action.description == "Follow up on release risk"
    assert saved_action.status == ActionItem.Status.OPEN
    assert saved_action.due_date is None

    owner_choices = list(ActionItemForm(cycle=cycle).fields["owner"].queryset)
    topic_choices = list(ActionItemForm(cycle=cycle).fields["topic"].queryset)
    assert owner in owner_choices
    assert inactive_member not in owner_choices
    assert non_member not in owner_choices
    assert other_owner not in owner_choices
    assert topic in topic_choices
    assert other_topic not in topic_choices

    invalid_forms = [
        ActionItemForm(action_payload(owner, topic, description="   "), cycle=cycle),
        ActionItemForm(action_payload(owner, topic, due_date="not-a-date"), cycle=cycle),
        ActionItemForm(action_payload(owner, topic, status="blocked"), cycle=cycle),
        ActionItemForm(action_payload(non_member, topic), cycle=cycle),
        ActionItemForm(action_payload(owner, other_topic), cycle=cycle),
    ]
    for form in invalid_forms:
        assert form.is_valid() is False
    assert ActionItem.objects.count() == 1

    blank_decision = RetrospectiveDecision(cycle=cycle, text="   ")
    with pytest.raises(ValidationError) as text_error:
        blank_decision.full_clean()
    assert "text" in text_error.value.message_dict

    bad_decision_topic = RetrospectiveDecision(
        cycle=cycle,
        text="Use the old checklist",
        topic=other_topic,
    )
    with pytest.raises(ValidationError) as decision_topic_error:
        bad_decision_topic.full_clean()
    assert "same feedback cycle" in decision_topic_error.value.message_dict["topic"][0]

    decision_form = RetrospectiveDecisionForm(decision_payload(topic=None), cycle=cycle)
    assert decision_form.is_valid() is True
    decision = decision_form.save()
    assert decision.text == "Keep the current release checklist"
    assert decision.topic is None

    invalid_decision_forms = [
        RetrospectiveDecisionForm(decision_payload(text="   "), cycle=cycle),
        RetrospectiveDecisionForm(decision_payload(topic=other_topic), cycle=cycle),
    ]
    for form in invalid_decision_forms:
        assert form.is_valid() is False
    assert RetrospectiveDecision.objects.count() == 1


def test_facilitator_can_create_and_edit_action_items_without_other_mutations(client):
    facilitator = create_user("facilitator")
    owner = create_user("owner")
    next_owner = create_user("next-owner")
    project = create_project("Editable Action Project")
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    add_membership(owner, project)
    add_membership(next_owner, project)
    cycle = create_cycle(project, facilitator)
    topic = create_cluster(cycle, "Release readiness", discussion_notes="Keep notes")
    next_topic = create_cluster(cycle, "Planning quality")
    card = create_card(cycle, owner, text="Do not mutate card", cluster=topic)
    vote = create_vote(cycle, owner, topic)
    other_action = create_action_item(cycle, owner, topic, description="Leave me open")
    decision = create_decision(cycle, text="Leave decision alone", topic=topic)
    client.force_login(facilitator)

    create_response = client.post(action_create_path(project, cycle), action_payload(owner, topic))
    assert create_response.status_code == 302
    action = ActionItem.objects.get(description="Follow up on release risk")
    assert action.status == ActionItem.Status.OPEN
    assert action.due_date is None

    board_response = client.get(board_path(project, cycle))
    content = board_response.content.decode()
    assert "Manual outcomes" in content
    assert "Follow up on release risk" in content
    assert "Owner: owner" in content
    assert "Status: Open" in content
    assert "Topic: Release readiness" in content
    assert "Due: No due date" in content
    assert "Create action item" in content

    update_response = client.post(
        action_update_path(project, cycle, action),
        action_payload(
            next_owner,
            next_topic,
            description="  Ship the launch checklist  ",
            due_date="2026-09-30",
            status=ActionItem.Status.DONE,
        ),
    )

    assert update_response.status_code == 302
    action.refresh_from_db()
    other_action.refresh_from_db()
    decision.refresh_from_db()
    topic.refresh_from_db()
    card.refresh_from_db()
    vote.refresh_from_db()
    assert action.description == "Ship the launch checklist"
    assert action.owner == next_owner
    assert action.due_date.isoformat() == "2026-09-30"
    assert action.status == ActionItem.Status.DONE
    assert action.topic == next_topic
    assert other_action.description == "Leave me open"
    assert other_action.status == ActionItem.Status.OPEN
    assert decision.text == "Leave decision alone"
    assert topic.discussion_notes == "Keep notes"
    assert card.text == "Do not mutate card"
    assert card.cluster == topic
    assert vote.vote_count == 3

    updated_board = client.get(board_path(project, cycle)).content.decode()
    assert "Ship the launch checklist" in updated_board
    assert "Owner: next-owner" in updated_board
    assert "Status: Done" in updated_board
    assert "Topic: Planning quality" in updated_board
    assert "Due: Sept. 30, 2026" in updated_board


def test_action_item_validation_and_tampering_leave_existing_data_unchanged(client):
    facilitator = create_user("facilitator")
    owner = create_user("owner")
    inactive_member = create_user("inactive-member", is_active=False)
    non_member = create_user("non-member")
    other_owner = create_user("other-owner")
    project = create_project("Action Validation Project")
    other_project = create_project("Other Action Project")
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    add_membership(owner, project)
    add_membership(inactive_member, project)
    add_membership(other_owner, other_project)
    cycle = create_cycle(project, facilitator)
    topic = create_cluster(cycle, "Current topic")
    other_cycle = create_cycle(other_project, facilitator, label="Other Action Week")
    other_topic = create_cluster(other_cycle, "Other secret topic")
    action = create_action_item(cycle, owner, topic, description="Original action")
    client.force_login(facilitator)

    validation_responses = [
        client.post(
            action_create_path(project, cycle),
            action_payload(owner, topic, description="   "),
        ),
        client.post(
            action_update_path(project, cycle, action),
            action_payload(owner, topic, due_date="bad-date"),
        ),
        client.post(
            action_update_path(project, cycle, action),
            action_payload(owner, topic, status="blocked"),
        ),
    ]
    for response in validation_responses:
        assert response.status_code == 200
    assert "Action item description cannot be empty." in (
        validation_responses[0].content.decode()
    )
    assert "Enter a valid due date." in validation_responses[1].content.decode()
    assert "Choose a valid action item status." in validation_responses[2].content.decode()

    tamper_responses = [
        client.post(action_create_path(project, cycle), action_payload(non_member, topic)),
        client.post(action_create_path(project, cycle), action_payload(inactive_member, topic)),
        client.post(action_create_path(project, cycle), action_payload(other_owner, topic)),
        client.post(action_create_path(project, cycle), action_payload(owner, other_topic)),
        client.post(
            action_update_path(project, cycle, action),
            action_payload(owner, other_topic),
        ),
        client.post(
            action_update_path(project, other_cycle, action),
            action_payload(owner, topic),
        ),
        client.post(
            action_update_path(other_project, cycle, action),
            action_payload(owner, topic),
        ),
    ]
    secrets = [
        "Action Validation Project",
        "Other Action Project",
        "Other Action Week",
        "Other secret topic",
        "Original action",
    ]
    for response in tamper_responses:
        assert response.status_code == 404
        assert_no_secret_leak(response, secrets)

    action.refresh_from_db()
    assert action.description == "Original action"
    assert action.owner == owner
    assert action.due_date is None
    assert action.status == ActionItem.Status.OPEN
    assert action.topic == topic
    assert ActionItem.objects.count() == 1


def test_facilitator_can_create_and_edit_decisions_without_other_mutations(client):
    facilitator = create_user("facilitator")
    owner = create_user("owner")
    project = create_project("Editable Decision Project")
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    add_membership(owner, project)
    cycle = create_cycle(project, facilitator)
    topic = create_cluster(cycle, "Release readiness", discussion_notes="Keep notes")
    next_topic = create_cluster(cycle, "Planning quality")
    card = create_card(cycle, owner, text="Do not mutate card", cluster=topic)
    vote = create_vote(cycle, owner, topic)
    action = create_action_item(cycle, owner, topic, description="Leave action open")
    other_decision = create_decision(cycle, text="Leave other decision alone", topic=topic)
    client.force_login(facilitator)

    create_response = client.post(decision_create_path(project, cycle), decision_payload())
    assert create_response.status_code == 302
    decision = RetrospectiveDecision.objects.get(text="Keep the current release checklist")
    assert decision.topic is None

    board_response = client.get(board_path(project, cycle))
    content = board_response.content.decode()
    assert "Confirmed decisions" in content
    assert "Keep the current release checklist" in content
    assert "No related topic" in content
    assert "Create decision" in content

    update_response = client.post(
        decision_update_path(project, cycle, decision),
        decision_payload(text="  Keep Friday launch reviews  ", topic=next_topic),
    )

    assert update_response.status_code == 302
    decision.refresh_from_db()
    other_decision.refresh_from_db()
    action.refresh_from_db()
    topic.refresh_from_db()
    card.refresh_from_db()
    vote.refresh_from_db()
    assert decision.text == "Keep Friday launch reviews"
    assert decision.topic == next_topic
    assert other_decision.text == "Leave other decision alone"
    assert action.description == "Leave action open"
    assert topic.discussion_notes == "Keep notes"
    assert card.text == "Do not mutate card"
    assert vote.vote_count == 3

    updated_board = client.get(board_path(project, cycle)).content.decode()
    assert "Keep Friday launch reviews" in updated_board
    assert "Topic: Planning quality" in updated_board


def test_decision_validation_and_tampering_leave_existing_data_unchanged(client):
    facilitator = create_user("facilitator")
    owner = create_user("owner")
    project = create_project("Decision Validation Project")
    other_project = create_project("Other Decision Project")
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    add_membership(facilitator, other_project, Membership.Role.FACILITATOR)
    add_membership(owner, project)
    cycle = create_cycle(project, facilitator)
    topic = create_cluster(cycle, "Current topic")
    other_cycle = create_cycle(other_project, facilitator, label="Other Decision Week")
    other_topic = create_cluster(other_cycle, "Other secret topic")
    decision = create_decision(cycle, text="Original decision", topic=topic)
    client.force_login(facilitator)

    blank_response = client.post(
        decision_update_path(project, cycle, decision),
        decision_payload(text="   ", topic=topic),
    )
    assert blank_response.status_code == 200
    assert "Decision text cannot be empty." in blank_response.content.decode()

    tamper_responses = [
        client.post(decision_create_path(project, cycle), decision_payload(topic=other_topic)),
        client.post(
            decision_update_path(project, cycle, decision),
            decision_payload(topic=other_topic),
        ),
        client.post(
            decision_update_path(project, other_cycle, decision),
            decision_payload(topic=topic),
        ),
        client.post(
            decision_update_path(other_project, cycle, decision),
            decision_payload(topic=topic),
        ),
    ]
    secrets = [
        "Decision Validation Project",
        "Other Decision Project",
        "Other Decision Week",
        "Other secret topic",
        "Original decision",
    ]
    for response in tamper_responses:
        assert response.status_code == 404
        assert_no_secret_leak(response, secrets)

    decision.refresh_from_db()
    assert decision.text == "Original decision"
    assert decision.topic == topic
    assert RetrospectiveDecision.objects.count() == 1


def test_members_can_view_manual_outcomes_but_cannot_mutate_them(client):
    facilitator = create_user("facilitator")
    owner = create_user("owner")
    project = create_project("Read Only Outcomes Project")
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    add_membership(owner, project)
    cycle = create_cycle(project, facilitator)
    topic = create_cluster(cycle, "Shared topic")
    action = create_action_item(cycle, owner, topic, description="Owner cannot self-complete")
    decision = create_decision(cycle, text="Keep the documented plan", topic=topic)

    client.force_login(owner)
    board_response = client.get(board_path(project, cycle))
    content = board_response.content.decode()
    forms = parser_from(board_response).forms
    assert board_response.status_code == 200
    assert "Manual outcomes" in content
    assert "Owner cannot self-complete" in content
    assert "Keep the documented plan" in content
    assert "Create action item" not in content
    assert "Save action item" not in content
    assert "Create decision" not in content
    assert "Save decision" not in content
    assert action_update_path(project, cycle, action) not in [
        form["action"] for form in forms
    ]

    action_response = client.post(
        action_update_path(project, cycle, action),
        action_payload(owner, topic, status=ActionItem.Status.DONE),
    )
    decision_response = client.post(
        decision_update_path(project, cycle, decision),
        decision_payload(text="Member write", topic=topic),
    )
    assert action_response.status_code == 404
    assert decision_response.status_code == 404
    action.refresh_from_db()
    decision.refresh_from_db()
    assert action.status == ActionItem.Status.OPEN
    assert decision.text == "Keep the documented plan"


def test_anonymous_and_protected_users_get_no_leakage_on_outcome_routes(client):
    facilitator = create_user("facilitator")
    member = create_user("member")
    outsider = create_user("outsider")
    admin = create_user("admin", is_staff=True, is_superuser=True)
    inactive = create_user("inactive", is_active=False)
    project = create_project("Secret Outcome Project")
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    add_membership(member, project)
    add_membership(inactive, project, Membership.Role.FACILITATOR)
    cycle = create_cycle(project, facilitator, label="Secret Outcome Week")
    topic = create_cluster(cycle, "Secret outcome topic")
    action = create_action_item(cycle, member, topic, description="Secret outcome action")
    decision = create_decision(cycle, text="Secret outcome decision", topic=topic)
    endpoints = [
        action_create_path(project, cycle),
        action_update_path(project, cycle, action),
        decision_create_path(project, cycle),
        decision_update_path(project, cycle, decision),
    ]

    for path in [board_path(project, cycle), *endpoints]:
        response = client.post(path) if path in endpoints else client.get(path)
        assert response.status_code == 302
        assert response["Location"] == f"{reverse('login')}?next={path}"

    secrets = [
        "Secret Outcome Project",
        "Secret Outcome Week",
        "Secret outcome topic",
        "Secret outcome action",
        "Secret outcome decision",
        "3 votes",
    ]
    create_vote(cycle, member, topic)
    for user in [outsider, admin]:
        client.force_login(user)
        responses = [
            client.get(board_path(project, cycle)),
            client.post(action_create_path(project, cycle), action_payload(member, topic)),
            client.post(
                action_update_path(project, cycle, action),
                action_payload(member, topic, status=ActionItem.Status.DONE),
            ),
            client.post(decision_create_path(project, cycle), decision_payload(topic=topic)),
            client.post(
                decision_update_path(project, cycle, decision),
                decision_payload(text="Leaked write", topic=topic),
            ),
        ]
        for response in responses:
            assert response.status_code == 404
            assert_no_secret_leak(response, secrets)

    client.force_login(inactive)
    inactive_path = action_update_path(project, cycle, action)
    inactive_response = client.post(inactive_path, action_payload(member, topic))
    assert inactive_response.status_code == 302
    assert inactive_response["Location"] == f"{reverse('login')}?next={inactive_path}"

    action.refresh_from_db()
    decision.refresh_from_db()
    assert action.status == ActionItem.Status.OPEN
    assert decision.text == "Secret outcome decision"


@pytest.mark.parametrize(
    ("status", "voting_status"),
    [
        (FeedbackCycle.Status.COLLECTING_FEEDBACK, FeedbackCycle.VotingStatus.CLOSED),
        (FeedbackCycle.Status.RETROSPECTIVE, FeedbackCycle.VotingStatus.CLUSTERING),
        (FeedbackCycle.Status.RETROSPECTIVE, FeedbackCycle.VotingStatus.OPEN),
        (FeedbackCycle.Status.COMPLETED, FeedbackCycle.VotingStatus.CLOSED),
    ],
)
def test_manual_outcome_visibility_and_mutation_require_closed_voting_discussion_stage(
    client,
    status,
    voting_status,
):
    facilitator = create_user(f"facilitator-{status}-{voting_status}")
    owner = create_user(f"owner-{status}-{voting_status}")
    project = create_project(f"Gated Outcome {status} {voting_status}")
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    add_membership(owner, project)
    cycle = create_cycle(
        project,
        facilitator,
        status=status,
        voting_status=voting_status,
    )
    topic = create_cluster(cycle, "Hidden gated topic")
    action = create_action_item(cycle, owner, topic, description="Hidden gated action")
    decision = create_decision(cycle, text="Hidden gated decision", topic=topic)
    client.force_login(facilitator)

    board_response = client.get(board_path(project, cycle))
    create_action_response = client.post(
        action_create_path(project, cycle),
        action_payload(owner, topic, description="Late action"),
    )
    update_action_response = client.post(
        action_update_path(project, cycle, action),
        action_payload(owner, topic, description="Late edit"),
    )
    create_decision_response = client.post(
        decision_create_path(project, cycle),
        decision_payload(text="Late decision", topic=topic),
    )
    update_decision_response = client.post(
        decision_update_path(project, cycle, decision),
        decision_payload(text="Late edit", topic=topic),
    )

    if status == FeedbackCycle.Status.RETROSPECTIVE:
        assert board_response.status_code == 200
        content = board_response.content.decode()
        assert "Manual outcomes" not in content
        assert "Hidden gated action" not in content
        assert "Hidden gated decision" not in content
    else:
        assert board_response.status_code == 404

    for response in [
        create_action_response,
        update_action_response,
        create_decision_response,
        update_decision_response,
    ]:
        assert response.status_code == 404

    action.refresh_from_db()
    decision.refresh_from_db()
    assert action.description == "Hidden gated action"
    assert decision.text == "Hidden gated decision"
    assert ActionItem.objects.filter(description="Late action").exists() is False
    assert RetrospectiveDecision.objects.filter(text="Late decision").exists() is False


def test_dashboard_shows_only_open_project_action_items_to_project_members(client):
    facilitator = create_user("facilitator")
    member = create_user("member")
    other_member = create_user("other-member")
    outsider = create_user("outsider")
    project = create_project("Dashboard Action Project")
    other_project = create_project("Other Dashboard Project")
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    add_membership(member, project)
    add_membership(other_member, other_project)
    cycle = create_cycle(project, facilitator, label="Dashboard Week")
    topic = create_cluster(cycle, "Dashboard topic")
    open_action = create_action_item(
        cycle,
        member,
        topic,
        description="Visible open dashboard action",
        due_date=date(2026, 10, 1),
    )
    no_due_open_action = create_action_item(
        cycle,
        member,
        topic,
        description="Visible no due dashboard action",
    )
    done_action = create_action_item(
        cycle,
        member,
        topic,
        description="Hidden done dashboard action",
        status=ActionItem.Status.DONE,
    )
    other_cycle = create_cycle(
        other_project,
        facilitator,
        label="Other Dashboard Week",
    )
    other_topic = create_cluster(other_cycle, "Other dashboard topic")
    create_action_item(
        other_cycle,
        other_member,
        other_topic,
        description="Hidden other project action",
    )

    for viewer in [facilitator, member]:
        client.force_login(viewer)
        response = client.get(dashboard_path(project))
        content = response.content.decode()
        assert response.status_code == 200
        assert open_action.description in content
        assert no_due_open_action.description in content
        assert "Owner: member" in content
        assert "Topic: Dashboard topic" in content
        assert "Cycle: Dashboard Week" in content
        assert "Due: Oct. 1, 2026" in content
        assert "Due: No due date" in content
        assert done_action.description not in content
        assert "Hidden other project action" not in content

    client.force_login(outsider)
    response = client.get(dashboard_path(project))
    assert response.status_code == 404
    assert_no_secret_leak(
        response,
        [
            "Dashboard Action Project",
            "Visible open dashboard action",
            "Hidden done dashboard action",
        ],
    )
