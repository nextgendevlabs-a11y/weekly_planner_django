from datetime import date

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from projects.models import (
    ActionItem,
    FeedbackCard,
    FeedbackCluster,
    FeedbackCycle,
    MeetingMaterial,
    MeetingMaterialDraftActionItem,
    MeetingMaterialDraftDecision,
    MeetingMaterialExtractionDraft,
    MeetingMaterialTranscript,
    Membership,
    Project,
    RetrospectiveDecision,
)
from projects.permissions import (
    can_facilitate_project,
    can_view_project,
    facilitatable_projects_for,
    viewable_projects_for,
)
from projects.retrospective_board import (
    retrospective_board_context_for,
    revealed_feedback_cards_for,
    suggestion_draft_context_for,
)
from projects.submission_progress import submission_progress_for
from projects.summary import retrospective_summary_context_for, summary_feedback_cards_for


pytestmark = pytest.mark.django_db


def create_user(
    username,
    *,
    is_active=True,
    is_staff=False,
    is_superuser=False,
    first_name="",
    last_name="",
    email="",
):
    return get_user_model().objects.create_user(
        username=username,
        password="UsablePass123!",
        is_active=is_active,
        is_staff=is_staff,
        is_superuser=is_superuser,
        first_name=first_name,
        last_name=last_name,
        email=email,
    )


def create_project(name):
    return Project.objects.create(name=name)


def add_membership(user, project, role=Membership.Role.TEAM_MEMBER):
    return Membership.objects.create(user=user, project=project, role=role)


def create_cycle(
    project,
    facilitator,
    *,
    label="Week 34",
    status=FeedbackCycle.Status.COLLECTING_FEEDBACK,
    voting_status=FeedbackCycle.VotingStatus.CLUSTERING,
    summary_text="",
):
    return FeedbackCycle.objects.create(
        project=project,
        facilitator=facilitator,
        label=label,
        status=status,
        voting_status=voting_status,
        opens_at=timezone.now(),
        approved_retrospective_summary_text=summary_text,
    )


def create_cluster(cycle, name):
    return FeedbackCluster.objects.create(cycle=cycle, name=name)


def create_card(
    cycle,
    author,
    *,
    text,
    category=FeedbackCard.Category.START,
    cluster=None,
    is_anonymous=False,
):
    return FeedbackCard.objects.create(
        cycle=cycle,
        author=author,
        category=category,
        text=text,
        cluster=cluster,
        is_anonymous=is_anonymous,
    )


def create_action_item(
    cycle,
    owner,
    topic,
    *,
    description,
    status=ActionItem.Status.OPEN,
):
    return ActionItem.objects.create(
        cycle=cycle,
        owner=owner,
        topic=topic,
        description=description,
        status=status,
    )


def create_decision(cycle, topic, *, text):
    return RetrospectiveDecision.objects.create(cycle=cycle, topic=topic, text=text)


def create_material(
    cycle,
    submitter,
    *,
    processing_status=MeetingMaterial.ProcessingStatus.SUCCEEDED,
    text="Transcript source",
    failure_message="",
):
    material = MeetingMaterial.objects.create(
        cycle=cycle,
        submitted_by=submitter,
        source_type=MeetingMaterial.SourceType.PASTED_TRANSCRIPT,
        processing_status=processing_status,
        pasted_transcript_text=text,
        text_character_count=len(text),
        failure_message=failure_message,
    )
    if processing_status == MeetingMaterial.ProcessingStatus.SUCCEEDED:
        MeetingMaterialTranscript.objects.create(
            meeting_material=material,
            text=f"Processed {text}",
            character_count=len(f"Processed {text}"),
        )
    return material


def create_draft(material, *, summary_text="Draft summary"):
    return MeetingMaterialExtractionDraft.objects.create(
        meeting_material=material,
        retrospective_summary_text=summary_text,
    )


def create_draft_decision(draft, topic, *, text="Draft decision"):
    return MeetingMaterialDraftDecision.objects.create(
        extraction_draft=draft,
        text=text,
        topic_candidate=topic.name,
        matched_topic=topic,
    )


def create_draft_action(draft, owner, topic, *, description="Draft action"):
    return MeetingMaterialDraftActionItem.objects.create(
        extraction_draft=draft,
        description=description,
        owner_candidate=owner.get_username(),
        matched_owner=owner,
        due_date=date(2026, 9, 30),
        topic_candidate=topic.name,
        matched_topic=topic,
    )


def path(name, project, cycle=None, **kwargs):
    route_kwargs = {"project_id": project.pk, **kwargs}
    if cycle is not None:
        route_kwargs["cycle_id"] = cycle.pk
    return reverse(name, kwargs=route_kwargs)


def feedback_path(project, cycle):
    return path("feedback_submission", project, cycle)


def dashboard_path(project):
    return path("project_dashboard", project)


def board_path(project, cycle):
    return path("retrospective_board", project, cycle)


def summary_path(project, cycle):
    return path("retrospective_summary", project, cycle)


def action_payload(owner, topic, *, status=ActionItem.Status.OPEN):
    return {
        "description": "Edited action",
        "owner": str(owner.pk),
        "due_date": "",
        "status": status,
        "topic": str(topic.pk),
    }


def decision_payload(topic):
    return {"text": "Edited decision", "topic": str(topic.pk)}


def suggestion_payload(card):
    return {
        "suggestion_count": "1",
        "suggestion-0-name": "Edited suggestion",
        f"card-{card.pk}-suggestion": "0",
    }


def review_payload(material, draft):
    data = {
        "material_id": str(material.pk),
        "extraction_draft_id": str(draft.pk),
        "draft_decision_count": str(draft.draft_decisions.count()),
        "draft_action_item_count": str(draft.draft_action_items.count()),
        "summary_text": draft.retrospective_summary_text,
    }
    for draft_decision in draft.draft_decisions.all():
        data[f"decision_{draft_decision.pk}_text"] = draft_decision.text
        data[f"decision_{draft_decision.pk}_topic"] = str(
            draft_decision.matched_topic_id
        )
    for draft_action in draft.draft_action_items.all():
        data[f"action_{draft_action.pk}_description"] = draft_action.description
        data[f"action_{draft_action.pk}_owner"] = str(draft_action.matched_owner_id)
        data[f"action_{draft_action.pk}_due_date"] = draft_action.due_date.isoformat()
        data[f"action_{draft_action.pk}_topic"] = str(draft_action.matched_topic_id)
    return data


def publish_payload(*attendees):
    return {"attendees": [str(attendee.pk) for attendee in attendees]}


def assert_content_excludes(response, secrets):
    content = response.content.decode()
    for secret in secrets:
        assert secret not in content


def assert_values_exclude(value, secrets):
    rendered = repr(value)
    for secret in secrets:
        assert secret not in rendered


def test_pre_reveal_outputs_show_only_each_authors_feedback_and_progress_metadata(
    client,
):
    facilitator = create_user("facilitator", first_name="Facil", last_name="Leader")
    member = create_user("member", first_name="Mira", last_name="Member")
    other = create_user("other", first_name="Omar", last_name="Other")
    project = create_project("Pre Reveal Privacy Project")
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    add_membership(member, project)
    add_membership(other, project)
    cycle = create_cycle(project, facilitator, label="Pre Reveal Week")
    facilitator_card = create_card(
        cycle,
        facilitator,
        text="Facilitator private pre reveal card",
    )
    member_card = create_card(cycle, member, text="Member private pre reveal card")
    other_card = create_card(
        cycle,
        other,
        text="Other contributor hidden pre reveal card",
        is_anonymous=True,
    )

    for viewer, visible_card, hidden_cards, hidden_identities in [
        (facilitator, facilitator_card, [member_card, other_card], ["Mira Member", "other"]),
        (member, member_card, [facilitator_card, other_card], ["Facil Leader", "other"]),
    ]:
        client.force_login(viewer)

        feedback = client.get(feedback_path(project, cycle))
        dashboard = client.get(dashboard_path(project))

        assert feedback.status_code == 200
        assert dashboard.status_code == 200
        assert visible_card.text in feedback.content.decode()
        for hidden_card in hidden_cards:
            assert_content_excludes(feedback, [hidden_card.text])
            assert_content_excludes(dashboard, [hidden_card.text])
        assert_content_excludes(feedback, hidden_identities)
        assert_content_excludes(dashboard, [hidden_card.text for hidden_card in hidden_cards])

    client.force_login(facilitator)
    progress_response = client.get(dashboard_path(project))
    progress = progress_response.context["team_submission_progress"]
    assert progress == [
        {"user_label": "facilitator", "has_submitted_feedback": True},
        {"user_label": "member", "has_submitted_feedback": True},
        {"user_label": "other", "has_submitted_feedback": True},
    ]
    assert submission_progress_for(cycle) == progress
    assert_values_exclude(
        progress,
        [
            "Facilitator private pre reveal card",
            "Member private pre reveal card",
            "Other contributor hidden pre reveal card",
            str(facilitator_card.pk),
            str(member_card.pk),
            str(other_card.pk),
        ],
    )


def test_anonymous_revealed_feedback_hides_author_in_views_and_query_helpers(client):
    facilitator = create_user(
        "facilitator",
        first_name="Fran",
        last_name="Lead",
        email="facilitator@example.test",
    )
    member = create_user("member", first_name="Mira", last_name="Member")
    anonymous_author = create_user(
        "secret-author",
        first_name="Hidden",
        last_name="Author",
        email="hidden-author@example.test",
    )
    staff_superuser = create_user(
        "staff-superuser",
        is_staff=True,
        is_superuser=True,
    )
    project = create_project("Anonymous Privacy Project")
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    add_membership(member, project)
    add_membership(anonymous_author, project)
    cycle = create_cycle(
        project,
        facilitator,
        label="Anonymous Week",
        status=FeedbackCycle.Status.RETROSPECTIVE,
        voting_status=FeedbackCycle.VotingStatus.CLUSTERING,
        summary_text="Published anonymous-safe summary",
    )
    topic = create_cluster(cycle, "Anonymous topic")
    create_card(cycle, member, text="Attributed visible card", cluster=topic)
    anonymous_card = create_card(
        cycle,
        anonymous_author,
        text="Anonymous revealed card text",
        category=FeedbackCard.Category.STOP,
        cluster=topic,
        is_anonymous=True,
    )

    secrets = [
        "secret-author",
        "Hidden Author",
        "hidden-author@example.test",
    ]
    helper_outputs = [
        revealed_feedback_cards_for(cycle),
        retrospective_board_context_for(cycle),
        summary_feedback_cards_for(cycle),
        retrospective_summary_context_for(cycle),
        suggestion_draft_context_for(
            cycle,
            {"clusters": [{"name": "Suggested", "card_ids": [anonymous_card.pk]}]},
        ),
    ]
    for output in helper_outputs:
        assert "Anonymous contributor" in repr(output)
        assert "Anonymous revealed card text" in repr(output)
        assert_values_exclude(output, secrets)

    revealed_anonymous_card = [
        card for card in revealed_feedback_cards_for(cycle) if card["id"] == anonymous_card.pk
    ][0]
    assert anonymous_author.pk not in revealed_anonymous_card.values()
    assert str(anonymous_author.pk) not in revealed_anonymous_card.values()

    for viewer in [member, facilitator]:
        client.force_login(viewer)
        board = client.get(board_path(project, cycle))
        assert board.status_code == 200
        assert "Anonymous contributor" in board.content.decode()
        assert "Anonymous revealed card text" in board.content.decode()
        assert_content_excludes(board, secrets)

    cycle.voting_status = FeedbackCycle.VotingStatus.CLOSED
    cycle.save(update_fields=["voting_status", "updated_at"])
    client.force_login(facilitator)
    publish = client.post(
        path("retrospective_summary_publish", project, cycle),
        publish_payload(facilitator, member),
    )
    assert publish.status_code == 302

    for viewer in [member, facilitator]:
        client.force_login(viewer)
        summary = client.get(summary_path(project, cycle))
        assert summary.status_code == 200
        assert "Anonymous contributor" in summary.content.decode()
        assert "Anonymous revealed card text" in summary.content.decode()
        assert_content_excludes(summary, secrets)

    client.force_login(staff_superuser)
    for protected_response in [
        client.get(board_path(project, cycle)),
        client.get(summary_path(project, cycle)),
    ]:
        assert protected_response.status_code == 404
        assert_content_excludes(
            protected_response,
            [
                *secrets,
                "Anonymous Privacy Project",
                "Anonymous Week",
                "Anonymous topic",
                "Anonymous revealed card text",
                "Anonymous contributor",
            ],
        )


def test_regular_members_are_denied_all_facilitator_only_workflows(client):
    facilitator = create_user("facilitator")
    member = create_user("member")
    project_without_cycle = create_project("Cycle Creation Project")
    add_membership(member, project_without_cycle)

    collecting_project = create_project("Collecting Facilitator Project")
    add_membership(facilitator, collecting_project, Membership.Role.FACILITATOR)
    add_membership(member, collecting_project)
    collecting_cycle = create_cycle(
        collecting_project,
        facilitator,
        label="Collecting Facilitator Week",
    )
    create_card(
        collecting_cycle,
        member,
        text="Collecting workflow private card",
    )

    clustering_project = create_project("Clustering Facilitator Project")
    add_membership(facilitator, clustering_project, Membership.Role.FACILITATOR)
    add_membership(member, clustering_project)
    clustering_cycle = create_cycle(
        clustering_project,
        facilitator,
        label="Clustering Facilitator Week",
        status=FeedbackCycle.Status.RETROSPECTIVE,
        voting_status=FeedbackCycle.VotingStatus.CLUSTERING,
    )
    source = create_cluster(clustering_cycle, "Source facilitator topic")
    target = create_cluster(clustering_cycle, "Target facilitator topic")
    card = create_card(
        clustering_cycle,
        member,
        text="Clustering facilitator-only card",
        cluster=source,
    )

    open_voting_project = create_project("Open Voting Facilitator Project")
    add_membership(facilitator, open_voting_project, Membership.Role.FACILITATOR)
    add_membership(member, open_voting_project)
    openable_cycle = create_cycle(
        open_voting_project,
        facilitator,
        label="Openable Voting Week",
        status=FeedbackCycle.Status.RETROSPECTIVE,
    )
    create_cluster(openable_cycle, "Openable voting topic")

    discussion_project = create_project("Discussion Facilitator Project")
    add_membership(facilitator, discussion_project, Membership.Role.FACILITATOR)
    add_membership(member, discussion_project)
    discussion_cycle = create_cycle(
        discussion_project,
        facilitator,
        label="Discussion Facilitator Week",
        status=FeedbackCycle.Status.RETROSPECTIVE,
        voting_status=FeedbackCycle.VotingStatus.CLOSED,
    )
    discussion_topic = create_cluster(discussion_cycle, "Discussion facilitator topic")
    action = create_action_item(
        discussion_cycle,
        member,
        discussion_topic,
        description="Facilitator-managed action",
    )
    decision = create_decision(
        discussion_cycle,
        discussion_topic,
        text="Facilitator-managed decision",
    )
    failed_material = create_material(
        discussion_cycle,
        facilitator,
        processing_status=MeetingMaterial.ProcessingStatus.FAILED,
        text="Failed material body",
        failure_message="Failed material reason",
    )
    review_material = create_material(
        discussion_cycle,
        facilitator,
        text="Review material body",
    )
    review_draft = create_draft(review_material, summary_text="Review draft summary")
    create_draft_decision(review_draft, discussion_topic, text="Review draft decision")
    create_draft_action(
        review_draft,
        member,
        discussion_topic,
        description="Review draft action",
    )

    client.force_login(member)
    cycle_create_url = path("feedback_cycle_create", project_without_cycle)
    denied_requests = [
        ("get", cycle_create_url, {}),
        (
            "post",
            cycle_create_url,
            {"label": "Forbidden", "opens_at": timezone.now().strftime("%Y-%m-%dT%H:%M")},
        ),
        ("post", path("feedback_cycle_reveal", collecting_project, collecting_cycle), {}),
        ("post", path("feedback_cluster_create", clustering_project, clustering_cycle), {"name": "Forbidden"}),
        (
            "post",
            path(
                "feedback_cluster_rename",
                clustering_project,
                clustering_cycle,
                cluster_id=source.pk,
            ),
            {"name": "Forbidden"},
        ),
        (
            "post",
            path(
                "feedback_card_move",
                clustering_project,
                clustering_cycle,
                card_id=card.pk,
            ),
            {"cluster": str(target.pk)},
        ),
        (
            "post",
            path(
                "feedback_cluster_merge",
                clustering_project,
                clustering_cycle,
                cluster_id=source.pk,
            ),
            {"target_cluster": str(target.pk)},
        ),
        (
            "post",
            path(
                "feedback_cluster_split",
                clustering_project,
                clustering_cycle,
                cluster_id=source.pk,
            ),
            {"name": "Forbidden", "cards": [str(card.pk)]},
        ),
        ("post", path("feedback_cluster_suggestions_generate", clustering_project, clustering_cycle), {}),
        (
            "post",
            path("feedback_cluster_suggestions_edit", clustering_project, clustering_cycle),
            suggestion_payload(card),
        ),
        (
            "post",
            path("feedback_cluster_suggestions_accept", clustering_project, clustering_cycle),
            suggestion_payload(card),
        ),
        ("post", path("feedback_cluster_suggestions_ignore", clustering_project, clustering_cycle), {}),
        ("post", path("feedback_cycle_voting_open", open_voting_project, openable_cycle), {}),
        ("post", path("feedback_cycle_voting_close", discussion_project, discussion_cycle), {}),
        (
            "post",
            path(
                "feedback_cluster_discussion_update",
                discussion_project,
                discussion_cycle,
                cluster_id=discussion_topic.pk,
            ),
            {
                "discussion_status": FeedbackCluster.DiscussionStatus.DISCUSSED,
                "discussion_notes": "Forbidden",
            },
        ),
        (
            "post",
            path("action_item_create", discussion_project, discussion_cycle),
            action_payload(member, discussion_topic),
        ),
        (
            "post",
            path(
                "action_item_update",
                discussion_project,
                discussion_cycle,
                action_item_id=action.pk,
            ),
            action_payload(member, discussion_topic, status=ActionItem.Status.DONE),
        ),
        (
            "post",
            path("retrospective_decision_create", discussion_project, discussion_cycle),
            decision_payload(discussion_topic),
        ),
        (
            "post",
            path(
                "retrospective_decision_update",
                discussion_project,
                discussion_cycle,
                decision_id=decision.pk,
            ),
            decision_payload(discussion_topic),
        ),
        (
            "post",
            path("meeting_material_create", discussion_project, discussion_cycle),
            {"pasted_transcript": "Forbidden transcript"},
        ),
        (
            "post",
            path(
                "meeting_material_retry",
                discussion_project,
                discussion_cycle,
                meeting_material_id=failed_material.pk,
            ),
            {},
        ),
        (
            "post",
            path(
                "meeting_material_extraction_draft_approve",
                discussion_project,
                discussion_cycle,
                meeting_material_id=review_material.pk,
                extraction_draft_id=review_draft.pk,
            ),
            review_payload(review_material, review_draft),
        ),
        (
            "post",
            path(
                "meeting_material_extraction_draft_discard",
                discussion_project,
                discussion_cycle,
                meeting_material_id=review_material.pk,
                extraction_draft_id=review_draft.pk,
            ),
            {},
        ),
        (
            "get",
            path("retrospective_summary_publish", discussion_project, discussion_cycle),
            {},
        ),
        (
            "post",
            path("retrospective_summary_publish", discussion_project, discussion_cycle),
            publish_payload(member),
        ),
    ]
    secrets = [
        "Cycle Creation Project",
        "Collecting Facilitator Project",
        "Collecting Facilitator Week",
        "Collecting workflow private card",
        "Clustering Facilitator Project",
        "Clustering Facilitator Week",
        "Source facilitator topic",
        "Target facilitator topic",
        "Clustering facilitator-only card",
        "Open Voting Facilitator Project",
        "Openable Voting Week",
        "Openable voting topic",
        "Discussion Facilitator Project",
        "Discussion Facilitator Week",
        "Discussion facilitator topic",
        "Facilitator-managed action",
        "Facilitator-managed decision",
        "Failed material body",
        "Failed material reason",
        "Review material body",
        "Review draft summary",
        "Review draft decision",
        "Review draft action",
    ]

    for method, url, data in denied_requests:
        response = getattr(client, method)(url, data)
        assert response.status_code == 404
        assert_content_excludes(response, secrets)

    collecting_cycle.refresh_from_db()
    openable_cycle.refresh_from_db()
    source.refresh_from_db()
    card.refresh_from_db()
    discussion_topic.refresh_from_db()
    action.refresh_from_db()
    decision.refresh_from_db()
    failed_material.refresh_from_db()
    review_draft.refresh_from_db()
    assert collecting_cycle.status == FeedbackCycle.Status.COLLECTING_FEEDBACK
    assert openable_cycle.voting_status == FeedbackCycle.VotingStatus.CLUSTERING
    assert source.name == "Source facilitator topic"
    assert card.cluster == source
    assert discussion_topic.discussion_notes == ""
    assert action.status == ActionItem.Status.OPEN
    assert decision.text == "Facilitator-managed decision"
    assert failed_material.processing_status == MeetingMaterial.ProcessingStatus.FAILED
    assert review_draft.review_status == MeetingMaterialExtractionDraft.ReviewStatus.PENDING
    assert not FeedbackCycle.objects.filter(label="Forbidden").exists()
    assert not MeetingMaterial.objects.filter(pasted_transcript_text="Forbidden transcript").exists()


def test_project_scoping_denies_protected_users_views_actions_and_query_helpers(client):
    facilitator = create_user("facilitator")
    member = create_user("member")
    outsider = create_user("outsider")
    inactive_member = create_user("inactive", is_active=False)
    staff_superuser = create_user(
        "staff-superuser",
        is_staff=True,
        is_superuser=True,
    )
    project = create_project("Scoped Secret Project")
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    add_membership(member, project)
    add_membership(inactive_member, project, Membership.Role.FACILITATOR)
    collecting_cycle = create_cycle(project, facilitator, label="Scoped Collecting Week")
    create_card(
        collecting_cycle,
        member,
        text="Scoped unrevealed card",
    )

    retro_project = create_project("Scoped Retro Project")
    add_membership(facilitator, retro_project, Membership.Role.FACILITATOR)
    add_membership(member, retro_project)
    retro_cycle = create_cycle(
        retro_project,
        facilitator,
        label="Scoped Retro Week",
        status=FeedbackCycle.Status.RETROSPECTIVE,
        voting_status=FeedbackCycle.VotingStatus.CLOSED,
    )
    topic = create_cluster(retro_cycle, "Scoped topic")
    action = create_action_item(
        retro_cycle,
        member,
        topic,
        description="Scoped action",
    )
    material = create_material(
        retro_cycle,
        facilitator,
        processing_status=MeetingMaterial.ProcessingStatus.FAILED,
        text="Scoped source",
        failure_message="Scoped failure",
    )

    completed_project = create_project("Scoped Completed Project")
    add_membership(facilitator, completed_project, Membership.Role.FACILITATOR)
    add_membership(member, completed_project)
    completed_cycle = create_cycle(
        completed_project,
        facilitator,
        label="Scoped Completed Week",
        status=FeedbackCycle.Status.COMPLETED,
        voting_status=FeedbackCycle.VotingStatus.CLOSED,
        summary_text="Scoped published summary",
    )
    completed_topic = create_cluster(completed_cycle, "Scoped completed topic")
    create_card(
        completed_cycle,
        member,
        text="Scoped completed card",
        cluster=completed_topic,
    )

    url_cases = [
        ("get", dashboard_path(project), {}),
        ("get", feedback_path(project, collecting_cycle), {}),
        (
            "post",
            path(
                "feedback_card_create",
                project,
                collecting_cycle,
                category=FeedbackCard.Category.START,
            ),
            {"text": "Forbidden"},
        ),
        ("post", path("feedback_cycle_reveal", project, collecting_cycle), {}),
        ("get", board_path(retro_project, retro_cycle), {}),
        (
            "post",
            path("meeting_material_create", retro_project, retro_cycle),
            {"pasted_transcript": "Forbidden material"},
        ),
        (
            "post",
            path(
                "meeting_material_retry",
                retro_project,
                retro_cycle,
                meeting_material_id=material.pk,
            ),
            {},
        ),
        (
            "post",
            path(
                "action_item_owner_complete",
                retro_project,
                retro_cycle,
                action_item_id=action.pk,
            ),
            {},
        ),
        ("get", summary_path(completed_project, completed_cycle), {}),
        (
            "get",
            path(
                "retrospective_summary_publish",
                retro_project,
                retro_cycle,
            ),
            {},
        ),
    ]
    secrets = [
        "Scoped Secret Project",
        "Scoped Collecting Week",
        "Scoped unrevealed card",
        "Scoped Retro Project",
        "Scoped Retro Week",
        "Scoped topic",
        "Scoped action",
        "Scoped source",
        "Scoped failure",
        "Scoped Completed Project",
        "Scoped Completed Week",
        "Scoped published summary",
        "Scoped completed topic",
        "Scoped completed card",
    ]

    for protected_user in [outsider, staff_superuser]:
        client.force_login(protected_user)
        for method, url, data in url_cases:
            response = getattr(client, method)(url, data)
            assert response.status_code == 404
            assert_content_excludes(response, secrets)

        assert can_view_project(protected_user, project) is False
        assert can_facilitate_project(protected_user, project) is False
        assert list(viewable_projects_for(protected_user)) == []
        assert list(facilitatable_projects_for(protected_user)) == []

    client.force_login(inactive_member)
    for method, url, data in url_cases:
        response = getattr(client, method)(url, data)
        assert response.status_code == 302
        assert response["Location"] == f"{reverse('login')}?next={url}"

    assert can_view_project(inactive_member, project) is False
    assert can_facilitate_project(inactive_member, project) is False
    assert list(viewable_projects_for(inactive_member)) == []
    assert list(facilitatable_projects_for(inactive_member)) == []


def test_action_owner_completion_is_limited_to_assigned_open_owner_and_facilitator_edit(
    client,
):
    facilitator = create_user("facilitator")
    owner = create_user("owner")
    coworker = create_user("coworker")
    non_member = create_user("non-member")
    project = create_project("Owner Boundary Project")
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    add_membership(owner, project)
    add_membership(coworker, project)
    cycle = create_cycle(
        project,
        facilitator,
        label="Owner Boundary Week",
        status=FeedbackCycle.Status.RETROSPECTIVE,
        voting_status=FeedbackCycle.VotingStatus.CLOSED,
    )
    topic = create_cluster(cycle, "Owner boundary topic")
    owner_action = create_action_item(
        cycle,
        owner,
        topic,
        description="Owner may complete this",
    )
    coworker_action = create_action_item(
        cycle,
        coworker,
        topic,
        description="Coworker owns this",
    )
    done_action = create_action_item(
        cycle,
        owner,
        topic,
        description="Already done owner action",
        status=ActionItem.Status.DONE,
    )
    complete_url = path(
        "action_item_owner_complete",
        project,
        cycle,
        action_item_id=owner_action.pk,
    )
    coworker_complete_url = path(
        "action_item_owner_complete",
        project,
        cycle,
        action_item_id=coworker_action.pk,
    )
    done_complete_url = path(
        "action_item_owner_complete",
        project,
        cycle,
        action_item_id=done_action.pk,
    )
    edit_url = path(
        "action_item_update",
        project,
        cycle,
        action_item_id=owner_action.pk,
    )
    secrets = [
        "Owner Boundary Project",
        "Owner Boundary Week",
        "Owner boundary topic",
        "Owner may complete this",
        "Coworker owns this",
        "Already done owner action",
        "owner",
        "coworker",
    ]

    client.force_login(coworker)
    coworker_response = client.post(complete_url)
    assert coworker_response.status_code == 404
    assert_content_excludes(coworker_response, secrets)

    client.force_login(non_member)
    non_member_response = client.post(complete_url)
    assert non_member_response.status_code == 404
    assert_content_excludes(non_member_response, secrets)

    client.force_login(facilitator)
    facilitator_owner_complete = client.post(complete_url)
    assert facilitator_owner_complete.status_code == 404
    assert_content_excludes(facilitator_owner_complete, secrets)

    facilitator_edit = client.post(
        edit_url,
        action_payload(coworker, topic, status=ActionItem.Status.DONE),
    )
    assert facilitator_edit.status_code == 302
    owner_action.refresh_from_db()
    assert owner_action.owner == coworker
    assert owner_action.status == ActionItem.Status.DONE

    client.force_login(owner)
    stale_owner_response = client.post(complete_url)
    done_response = client.post(done_complete_url)
    assert stale_owner_response.status_code == 404
    assert done_response.status_code == 404
    assert_content_excludes(stale_owner_response, secrets)
    assert_content_excludes(done_response, secrets)

    owner_coworker_response = client.post(coworker_complete_url)
    assert owner_coworker_response.status_code == 404
    assert_content_excludes(owner_coworker_response, secrets)

    client.force_login(coworker)
    valid_response = client.post(coworker_complete_url)
    assert valid_response.status_code == 302
    assert valid_response["Location"] == dashboard_path(project)
    coworker_action.refresh_from_db()
    assert coworker_action.status == ActionItem.Status.DONE
