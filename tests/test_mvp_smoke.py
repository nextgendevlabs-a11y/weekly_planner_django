from datetime import date
from html.parser import HTMLParser

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from projects.meeting_processing import process_meeting_material
from projects.models import (
    ActionItem,
    FeedbackCard,
    FeedbackCluster,
    FeedbackClusterVote,
    FeedbackCycle,
    MeetingMaterial,
    MeetingMaterialDraftActionItem,
    MeetingMaterialDraftDecision,
    MeetingMaterialExtractionDraft,
    Membership,
    Project,
    RetrospectiveAttendance,
    RetrospectiveDecision,
)


pytestmark = pytest.mark.django_db


class FormAndLinkParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.forms = []
        self.links = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "form" and "action" in attributes:
            self.forms.append(
                {
                    "action": attributes["action"],
                    "method": attributes.get("method", "get").lower(),
                }
            )
        if tag == "a" and "href" in attributes:
            self.links.append(attributes["href"])


def create_user(
    username,
    *,
    first_name="",
    last_name="",
    email="",
    is_staff=False,
    is_superuser=False,
):
    return get_user_model().objects.create_user(
        username=username,
        password="UsablePass123!",
        first_name=first_name,
        last_name=last_name,
        email=email,
        is_staff=is_staff,
        is_superuser=is_superuser,
    )


def add_membership(user, project, role=Membership.Role.TEAM_MEMBER):
    return Membership.objects.create(user=user, project=project, role=role)


def dashboard_path(project):
    return reverse("project_dashboard", kwargs={"project_id": project.pk})


def cycle_create_path(project):
    return reverse("feedback_cycle_create", kwargs={"project_id": project.pk})


def feedback_path(project, cycle):
    return reverse(
        "feedback_submission",
        kwargs={"project_id": project.pk, "cycle_id": cycle.pk},
    )


def card_create_path(project, cycle, category):
    return reverse(
        "feedback_card_create",
        kwargs={
            "project_id": project.pk,
            "cycle_id": cycle.pk,
            "category": category,
        },
    )


def reveal_path(project, cycle):
    return reverse(
        "feedback_cycle_reveal",
        kwargs={"project_id": project.pk, "cycle_id": cycle.pk},
    )


def board_path(project, cycle):
    return reverse(
        "retrospective_board",
        kwargs={"project_id": project.pk, "cycle_id": cycle.pk},
    )


def suggestions_accept_path(project, cycle):
    return reverse(
        "feedback_cluster_suggestions_accept",
        kwargs={"project_id": project.pk, "cycle_id": cycle.pk},
    )


def voting_open_path(project, cycle):
    return reverse(
        "feedback_cycle_voting_open",
        kwargs={"project_id": project.pk, "cycle_id": cycle.pk},
    )


def voting_submit_path(project, cycle):
    return reverse(
        "feedback_cycle_vote_submit",
        kwargs={"project_id": project.pk, "cycle_id": cycle.pk},
    )


def discussion_update_path(project, cycle, cluster):
    return reverse(
        "feedback_cluster_discussion_update",
        kwargs={
            "project_id": project.pk,
            "cycle_id": cycle.pk,
            "cluster_id": cluster.pk,
        },
    )


def action_create_path(project, cycle):
    return reverse(
        "action_item_create",
        kwargs={"project_id": project.pk, "cycle_id": cycle.pk},
    )


def action_owner_complete_path(project, cycle, action_item):
    return reverse(
        "action_item_owner_complete",
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


def material_create_path(project, cycle):
    return reverse(
        "meeting_material_create",
        kwargs={"project_id": project.pk, "cycle_id": cycle.pk},
    )


def draft_approve_path(project, cycle, material, draft):
    return reverse(
        "meeting_material_extraction_draft_approve",
        kwargs={
            "project_id": project.pk,
            "cycle_id": cycle.pk,
            "meeting_material_id": material.pk,
            "extraction_draft_id": draft.pk,
        },
    )


def publish_path(project, cycle):
    return reverse(
        "retrospective_summary_publish",
        kwargs={"project_id": project.pk, "cycle_id": cycle.pk},
    )


def summary_path(project, cycle):
    return reverse(
        "retrospective_summary",
        kwargs={"project_id": project.pk, "cycle_id": cycle.pk},
    )


def parse(response):
    parser = FormAndLinkParser()
    parser.feed(response.content.decode())
    return parser


def content(response):
    return response.content.decode()


def assert_not_rendered(response, hidden_values):
    rendered = content(response)
    for hidden in hidden_values:
        assert hidden not in rendered


def create_cycle_through_view(client, project, facilitator, *, label):
    client.force_login(facilitator)
    response = client.post(
        cycle_create_path(project),
        {
            "label": label,
            "opens_at": "2026-09-03T09:00",
            "closes_at": "2026-09-05T17:00",
        },
    )
    assert response.status_code == 302
    return FeedbackCycle.objects.get(project=project, label=label)


def submit_card_through_view(client, project, cycle, author, category, text, anonymous=False):
    client.force_login(author)
    post_data = {"text": text}
    if anonymous:
        post_data["is_anonymous"] = "on"
    response = client.post(card_create_path(project, cycle, category), post_data)
    assert response.status_code == 302
    return FeedbackCard.objects.get(cycle=cycle, author=author, category=category, text=text)


def accept_cluster_suggestions_through_view(client, project, cycle, facilitator, clusters):
    post_data = {"suggestion_count": str(len(clusters))}
    for index, (name, cards) in enumerate(clusters):
        post_data[f"suggestion-{index}-name"] = name
        for card in cards:
            post_data[f"card-{card.pk}-suggestion"] = str(index)

    client.force_login(facilitator)
    response = client.post(suggestions_accept_path(project, cycle), post_data)
    assert response.status_code == 302


def vote_payload(allocations):
    return {
        f"cluster_{cluster.pk}_votes": str(vote_count)
        for cluster, vote_count in allocations.items()
    }


def submit_votes_through_view(client, project, cycle, voter, allocations):
    client.force_login(voter)
    response = client.post(
        voting_submit_path(project, cycle),
        vote_payload(allocations),
    )
    assert response.status_code == 302


def review_payload(material, draft, *, summary_text=None):
    data = {
        "material_id": str(material.pk),
        "extraction_draft_id": str(draft.pk),
        "draft_decision_count": str(draft.draft_decisions.count()),
        "draft_action_item_count": str(draft.draft_action_items.count()),
        "summary_text": (
            draft.retrospective_summary_text if summary_text is None else summary_text
        ),
    }
    for draft_decision in draft.draft_decisions.all():
        data[f"decision_{draft_decision.pk}_text"] = draft_decision.text
        data[f"decision_{draft_decision.pk}_topic"] = (
            "" if draft_decision.matched_topic_id is None else str(draft_decision.matched_topic_id)
        )
    for draft_action in draft.draft_action_items.all():
        data[f"action_{draft_action.pk}_description"] = draft_action.description
        data[f"action_{draft_action.pk}_owner"] = (
            "" if draft_action.matched_owner_id is None else str(draft_action.matched_owner_id)
        )
        data[f"action_{draft_action.pk}_due_date"] = (
            "" if draft_action.due_date is None else draft_action.due_date.isoformat()
        )
        data[f"action_{draft_action.pk}_topic"] = (
            "" if draft_action.matched_topic_id is None else str(draft_action.matched_topic_id)
        )
    return data


def publish_payload(*attendees):
    return {"attendees": [str(attendee.pk) for attendee in attendees]}


def test_small_team_completes_mvp_retrospective_workflow_smoke(client):
    facilitator = create_user("facilitator", first_name="Fran", last_name="Lead")
    member = create_user("member", first_name="Mira", last_name="Member")
    anonymous_author = create_user(
        "anon-author-secret",
        first_name="Hidden",
        last_name="Author",
        email="anon-author-secret@example.test",
    )
    outsider = create_user("outsider")
    project = Project.objects.create(name="Smoke MVP Project")
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    add_membership(member, project)
    add_membership(anonymous_author, project)

    assert Membership.objects.filter(
        project=project,
        role=Membership.Role.FACILITATOR,
    ).count() == 1
    assert Membership.objects.filter(
        project=project,
        role=Membership.Role.TEAM_MEMBER,
        user__is_active=True,
    ).count() == 2
    assert not Membership.objects.filter(project=project, user=outsider).exists()

    cycle = create_cycle_through_view(
        client,
        project,
        facilitator,
        label="Smoke Week 21",
    )
    dashboard = client.get(dashboard_path(project))
    assert dashboard.status_code == 200
    assert dashboard.context["collecting_cycle"]["pk"] == cycle.pk
    assert "Smoke Week 21" in content(dashboard)
    assert "Collecting feedback" in content(dashboard)

    member_start = submit_card_through_view(
        client,
        project,
        cycle,
        member,
        FeedbackCard.Category.START,
        "SMOKE_MEMBER_START_SECRET start pairing on risky releases",
    )
    member_stop = submit_card_through_view(
        client,
        project,
        cycle,
        member,
        FeedbackCard.Category.STOP,
        "SMOKE_MEMBER_STOP_SECRET stop changing launch scope late",
    )
    member_continue = submit_card_through_view(
        client,
        project,
        cycle,
        member,
        FeedbackCard.Category.CONTINUE,
        "SMOKE_MEMBER_CONTINUE_SECRET continue sharing customer context",
    )
    anonymous_start = submit_card_through_view(
        client,
        project,
        cycle,
        anonymous_author,
        FeedbackCard.Category.START,
        "SMOKE_ANON_START_SECRET start agenda previews",
        anonymous=True,
    )
    anonymous_stop = submit_card_through_view(
        client,
        project,
        cycle,
        anonymous_author,
        FeedbackCard.Category.STOP,
        "SMOKE_ANON_STOP_SECRET stop surprise priority changes",
        anonymous=True,
    )
    anonymous_continue = submit_card_through_view(
        client,
        project,
        cycle,
        anonymous_author,
        FeedbackCard.Category.CONTINUE,
        "SMOKE_ANON_CONTINUE_SECRET continue short demos",
        anonymous=True,
    )
    assert FeedbackCard.objects.filter(cycle=cycle).count() == 6
    assert FeedbackCard.objects.filter(cycle=cycle, is_anonymous=False).exists()
    assert FeedbackCard.objects.filter(cycle=cycle, is_anonymous=True).exists()

    client.force_login(member)
    member_feedback = client.get(feedback_path(project, cycle))
    assert member_feedback.status_code == 200
    assert "SMOKE_MEMBER_START_SECRET" in content(member_feedback)
    assert_not_rendered(
        member_feedback,
        [
            "SMOKE_ANON_START_SECRET",
            "SMOKE_ANON_STOP_SECRET",
            "SMOKE_ANON_CONTINUE_SECRET",
            "anon-author-secret",
        ],
    )

    client.force_login(anonymous_author)
    anonymous_feedback = client.get(feedback_path(project, cycle))
    assert anonymous_feedback.status_code == 200
    assert "SMOKE_ANON_STOP_SECRET" in content(anonymous_feedback)
    assert_not_rendered(
        anonymous_feedback,
        [
            "SMOKE_MEMBER_START_SECRET",
            "SMOKE_MEMBER_STOP_SECRET",
            "SMOKE_MEMBER_CONTINUE_SECRET",
            "Mira Member",
        ],
    )

    client.force_login(facilitator)
    progress_dashboard = client.get(dashboard_path(project))
    assert progress_dashboard.context["team_submission_progress"] == [
        {"user_label": "anon-author-secret", "has_submitted_feedback": True},
        {"user_label": "facilitator", "has_submitted_feedback": False},
        {"user_label": "member", "has_submitted_feedback": True},
    ]
    assert_not_rendered(
        progress_dashboard,
        [
            "SMOKE_MEMBER_START_SECRET",
            "SMOKE_ANON_STOP_SECRET",
            "Start feedback",
            "Stop feedback",
            "Continue feedback",
        ],
    )

    reveal_response = client.post(reveal_path(project, cycle))
    assert reveal_response.status_code == 302
    cycle.refresh_from_db()
    assert cycle.status == FeedbackCycle.Status.RETROSPECTIVE

    board = client.get(board_path(project, cycle))
    assert board.status_code == 200
    board_content = content(board)
    for card_text in [
        member_start.text,
        member_stop.text,
        member_continue.text,
        anonymous_start.text,
        anonymous_stop.text,
        anonymous_continue.text,
    ]:
        assert card_text in board_content
    assert "Mira Member" in board_content
    assert "Anonymous contributor" in board_content
    assert_not_rendered(
        board,
        [
            "anon-author-secret",
            "Hidden Author",
            "anon-author-secret@example.test",
        ],
    )

    accept_cluster_suggestions_through_view(
        client,
        project,
        cycle,
        facilitator,
        [
            ("Release readiness", [member_start, member_continue, anonymous_continue]),
            ("Priority clarity", [member_stop, anonymous_start, anonymous_stop]),
        ],
    )
    release_readiness = FeedbackCluster.objects.get(cycle=cycle, name="Release readiness")
    priority_clarity = FeedbackCluster.objects.get(cycle=cycle, name="Priority clarity")
    member_start.refresh_from_db()
    anonymous_stop.refresh_from_db()
    assert FeedbackCluster.objects.filter(cycle=cycle).count() == 2
    assert member_start.cluster == release_readiness
    assert anonymous_stop.cluster == priority_clarity

    clustered_board = client.get(board_path(project, cycle))
    assert "Stop - Anonymous contributor" in content(clustered_board)
    assert_not_rendered(
        clustered_board,
        [
            "anon-author-secret",
            "Hidden Author",
            "anon-author-secret@example.test",
        ],
    )

    open_response = client.post(voting_open_path(project, cycle))
    assert open_response.status_code == 302
    cycle.refresh_from_db()
    assert cycle.voting_status == FeedbackCycle.VotingStatus.OPEN

    submit_votes_through_view(
        client,
        project,
        cycle,
        facilitator,
        {release_readiness: 3, priority_clarity: 0},
    )
    submit_votes_through_view(
        client,
        project,
        cycle,
        member,
        {release_readiness: 2, priority_clarity: 1},
    )
    member_open_board = client.get(board_path(project, cycle))
    assert member_open_board.status_code == 200
    assert "Save votes" in content(member_open_board)
    assert_not_rendered(
        member_open_board,
        [
            "5 votes",
            "1 vote",
            "Ranked discussion agenda",
            "Voting progress",
            "Fran Lead",
            "anon-author-secret",
        ],
    )
    submit_votes_through_view(
        client,
        project,
        cycle,
        anonymous_author,
        {release_readiness: 1, priority_clarity: 2},
    )
    cycle.refresh_from_db()
    assert cycle.voting_status == FeedbackCycle.VotingStatus.CLOSED
    assert FeedbackClusterVote.objects.filter(cycle=cycle).count() == 6
    assert sum(
        FeedbackClusterVote.objects.filter(cycle=cycle, voter=facilitator).values_list(
            "vote_count",
            flat=True,
        )
    ) == 3
    assert sum(
        FeedbackClusterVote.objects.filter(cycle=cycle, voter=member).values_list(
            "vote_count",
            flat=True,
        )
    ) == 3
    assert sum(
        FeedbackClusterVote.objects.filter(
            cycle=cycle,
            voter=anonymous_author,
        ).values_list("vote_count", flat=True)
    ) == 3

    closed_board = client.get(board_path(project, cycle))
    closed_content = content(closed_board)
    assert closed_content.index("Release readiness") < closed_content.index(
        "Priority clarity"
    )
    assert "6 votes" in closed_content
    assert "3 votes" in closed_content
    assert closed_board.context["discussion_topics"][0]["name"] == "Release readiness"
    assert closed_board.context["discussion_topics"][0]["vote_total"] == 6
    assert closed_board.context["discussion_topics"][1]["vote_total"] == 3

    client.force_login(facilitator)
    discussion_response = client.post(
        discussion_update_path(project, cycle, release_readiness),
        {
            "discussion_status": FeedbackCluster.DiscussionStatus.DISCUSSED,
            "discussion_notes": "SMOKE_DISCUSSION_NOTES saved for release readiness.",
        },
    )
    assert discussion_response.status_code == 302
    release_readiness.refresh_from_db()
    assert release_readiness.discussion_status == FeedbackCluster.DiscussionStatus.DISCUSSED
    assert release_readiness.discussion_notes == (
        "SMOKE_DISCUSSION_NOTES saved for release readiness."
    )

    facilitator_discussion = client.get(board_path(project, cycle))
    assert "Save discussion topic" in content(facilitator_discussion)
    assert "SMOKE_DISCUSSION_NOTES" in content(facilitator_discussion)
    client.force_login(member)
    member_discussion = client.get(board_path(project, cycle))
    assert "SMOKE_DISCUSSION_NOTES" in content(member_discussion)
    assert "Save discussion topic" not in content(member_discussion)
    assert discussion_update_path(project, cycle, release_readiness) not in [
        form["action"] for form in parse(member_discussion).forms
    ]

    client.force_login(facilitator)
    manual_action_response = client.post(
        action_create_path(project, cycle),
        {
            "description": "SMOKE_MANUAL_ACTION update the release checklist",
            "owner": str(member.pk),
            "due_date": "2026-09-30",
            "topic": str(release_readiness.pk),
        },
    )
    assert manual_action_response.status_code == 302
    manual_decision_response = client.post(
        decision_create_path(project, cycle),
        {
            "text": "SMOKE_MANUAL_DECISION keep release readiness reviews",
            "topic": str(release_readiness.pk),
        },
    )
    assert manual_decision_response.status_code == 302
    manual_action = ActionItem.objects.get(description__startswith="SMOKE_MANUAL_ACTION")
    manual_decision = RetrospectiveDecision.objects.get(
        text__startswith="SMOKE_MANUAL_DECISION"
    )
    assert manual_action.owner == member
    assert manual_action.topic == release_readiness
    assert manual_decision.topic == release_readiness

    raw_transcript_secret = (
        "TRANSCRIPT_RAW_SECRET\n"
        "Summary: SMOKE_APPROVED_SUMMARY release readiness improved.\n"
        "Decision: SMOKE_EXTRACTED_DECISION keep Release readiness reviews.\n"
        "Action: SMOKE_EXTRACTED_ACTION member updates Release readiness checklist by 2026-10-15"
    )
    material_response = client.post(
        material_create_path(project, cycle),
        {"pasted_transcript": raw_transcript_secret},
    )
    assert material_response.status_code == 302
    material = MeetingMaterial.objects.get(cycle=cycle)
    assert material.processing_status == MeetingMaterial.ProcessingStatus.QUEUED

    processing_result = process_meeting_material(material.pk)
    assert processing_result.processed is True
    assert processing_result.status == MeetingMaterial.ProcessingStatus.SUCCEEDED
    material.refresh_from_db()
    assert material.processing_status == MeetingMaterial.ProcessingStatus.SUCCEEDED
    assert material.processed_transcript.text == raw_transcript_secret
    draft = material.extraction_draft
    assert draft.review_status == MeetingMaterialExtractionDraft.ReviewStatus.PENDING
    assert draft.retrospective_summary_text == (
        "SMOKE_APPROVED_SUMMARY release readiness improved."
    )
    draft_decision = MeetingMaterialDraftDecision.objects.get(extraction_draft=draft)
    draft_action = MeetingMaterialDraftActionItem.objects.get(extraction_draft=draft)
    assert draft_decision.matched_topic == release_readiness
    assert draft_action.matched_owner == member
    assert draft_action.matched_topic == release_readiness
    assert draft_action.due_date == date(2026, 10, 15)

    client.force_login(member)
    member_pending_draft_board = client.get(board_path(project, cycle))
    assert "Status: Succeeded" in content(member_pending_draft_board)
    assert_not_rendered(
        member_pending_draft_board,
        [
            "TRANSCRIPT_RAW_SECRET",
            "SMOKE_APPROVED_SUMMARY",
            "SMOKE_EXTRACTED_DECISION",
            "SMOKE_EXTRACTED_ACTION",
            "Owner candidate",
            "Topic candidate",
        ],
    )

    client.force_login(facilitator)
    approve_response = client.post(
        draft_approve_path(project, cycle, material, draft),
        review_payload(material, draft),
    )
    assert approve_response.status_code == 302
    draft.refresh_from_db()
    cycle.refresh_from_db()
    assert draft.review_status == MeetingMaterialExtractionDraft.ReviewStatus.APPROVED
    assert cycle.approved_retrospective_summary_text == (
        "SMOKE_APPROVED_SUMMARY release readiness improved."
    )
    assert ActionItem.objects.filter(cycle=cycle).count() == 2
    assert RetrospectiveDecision.objects.filter(cycle=cycle).count() == 2
    assert ActionItem.objects.filter(pk=manual_action.pk).exists()
    assert RetrospectiveDecision.objects.filter(pk=manual_decision.pk).exists()

    publish_response = client.post(
        publish_path(project, cycle),
        publish_payload(facilitator, member),
    )
    assert publish_response.status_code == 302
    assert publish_response["Location"] == summary_path(project, cycle)
    cycle.refresh_from_db()
    assert cycle.status == FeedbackCycle.Status.COMPLETED
    assert cycle.summary_active_member_count == 3
    assert set(
        RetrospectiveAttendance.objects.filter(cycle=cycle).values_list(
            "user_id",
            flat=True,
        )
    ) == {facilitator.pk, member.pk}

    client.force_login(member)
    summary = client.get(summary_path(project, cycle))
    assert summary.status_code == 200
    summary_content = content(summary)
    assert "SMOKE_APPROVED_SUMMARY release readiness improved." in summary_content
    assert summary_content.index("Release readiness") < summary_content.index(
        "Priority clarity"
    )
    assert "6 votes" in summary_content
    assert "SMOKE_DISCUSSION_NOTES" in summary_content
    assert "Fran Lead" in summary_content
    assert "Mira Member" in summary_content
    assert "Active project members at publication" in summary_content
    assert "<strong>3</strong>" in summary_content
    assert "Members who submitted feedback" in summary_content
    assert "Eligible members who completed voting" in summary_content
    for visible in [
        "SMOKE_MEMBER_START_SECRET",
        "SMOKE_ANON_STOP_SECRET",
        "Anonymous contributor",
        "SMOKE_MANUAL_DECISION",
        "SMOKE_EXTRACTED_DECISION",
        "SMOKE_MANUAL_ACTION",
        "SMOKE_EXTRACTED_ACTION",
    ]:
        assert visible in summary_content
    assert_not_rendered(
        summary,
        [
            "anon-author-secret",
            "Hidden Author",
            "anon-author-secret@example.test",
            "TRANSCRIPT_RAW_SECRET",
            "Meeting material",
            "Owner candidate",
            "Topic candidate",
        ],
    )

    assert client.get(board_path(project, cycle)).status_code == 404
    assert client.get(publish_path(project, cycle)).status_code == 404
    dashboard_after_publish = client.get(dashboard_path(project))
    dashboard_content = content(dashboard_after_publish)
    dashboard_links = parse(dashboard_after_publish).links
    assert "No feedback cycle has been started for this project yet." in dashboard_content
    assert "Smoke Week 21" in dashboard_content
    assert summary_path(project, cycle) in dashboard_links
    assert board_path(project, cycle) not in dashboard_links
    assert "Open retrospective board" not in dashboard_content


def test_mvp_smoke_guardrails_for_non_members_facilitator_actions_and_action_owners(
    client,
):
    facilitator = create_user("guard-facilitator")
    owner = create_user("guard-owner")
    coworker = create_user("guard-coworker")
    outsider = create_user("guard-outsider")
    project = Project.objects.create(name="Guardrail Project Secret")
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    add_membership(owner, project)
    add_membership(coworker, project)
    cycle = create_cycle_through_view(
        client,
        project,
        facilitator,
        label="Guardrail Week Secret",
    )
    card = submit_card_through_view(
        client,
        project,
        cycle,
        owner,
        FeedbackCard.Category.START,
        "GUARDRAIL_FEEDBACK_SECRET",
    )

    secrets = [
        "Guardrail Project Secret",
        "Guardrail Week Secret",
        "GUARDRAIL_FEEDBACK_SECRET",
        "GUARDRAIL_TOPIC_SECRET",
        "GUARDRAIL_OWNER_ACTION_SECRET",
        "GUARDRAIL_COWORKER_ACTION_SECRET",
    ]

    client.force_login(outsider)
    for response in [
        client.get(dashboard_path(project)),
        client.get(feedback_path(project, cycle)),
        client.post(reveal_path(project, cycle)),
    ]:
        assert response.status_code == 404
        assert_not_rendered(response, secrets)

    client.force_login(owner)
    member_reveal_response = client.post(reveal_path(project, cycle))
    assert member_reveal_response.status_code == 404
    assert_not_rendered(member_reveal_response, secrets)
    cycle.refresh_from_db()
    assert cycle.status == FeedbackCycle.Status.COLLECTING_FEEDBACK

    client.force_login(facilitator)
    assert client.post(reveal_path(project, cycle)).status_code == 302
    cluster_response = client.post(
        suggestions_accept_path(project, cycle),
        {
            "suggestion_count": "1",
            "suggestion-0-name": "GUARDRAIL_TOPIC_SECRET",
            f"card-{card.pk}-suggestion": "0",
        },
    )
    assert cluster_response.status_code == 302
    cluster = FeedbackCluster.objects.get(cycle=cycle)
    assert client.post(voting_open_path(project, cycle)).status_code == 302
    submit_votes_through_view(client, project, cycle, facilitator, {cluster: 3})
    submit_votes_through_view(client, project, cycle, owner, {cluster: 3})
    submit_votes_through_view(client, project, cycle, coworker, {cluster: 3})
    cycle.refresh_from_db()
    assert cycle.voting_status == FeedbackCycle.VotingStatus.CLOSED

    client.force_login(facilitator)
    owner_action_response = client.post(
        action_create_path(project, cycle),
        {
            "description": "GUARDRAIL_OWNER_ACTION_SECRET",
            "owner": str(owner.pk),
            "due_date": "",
            "topic": str(cluster.pk),
        },
    )
    coworker_action_response = client.post(
        action_create_path(project, cycle),
        {
            "description": "GUARDRAIL_COWORKER_ACTION_SECRET",
            "owner": str(coworker.pk),
            "due_date": "",
            "topic": str(cluster.pk),
        },
    )
    assert owner_action_response.status_code == 302
    assert coworker_action_response.status_code == 302
    owner_action = ActionItem.objects.get(description="GUARDRAIL_OWNER_ACTION_SECRET")
    coworker_action = ActionItem.objects.get(
        description="GUARDRAIL_COWORKER_ACTION_SECRET"
    )

    client.force_login(owner)
    member_mutation_responses = [
        client.post(voting_open_path(project, cycle)),
        client.post(
            discussion_update_path(project, cycle, cluster),
            {
                "discussion_status": FeedbackCluster.DiscussionStatus.DISCUSSED,
                "discussion_notes": "member should not mutate",
            },
        ),
        client.post(
            action_create_path(project, cycle),
            {
                "description": "member should not create",
                "owner": str(owner.pk),
                "due_date": "",
                "topic": str(cluster.pk),
            },
        ),
        client.post(
            decision_create_path(project, cycle),
            {"text": "member should not decide", "topic": str(cluster.pk)},
        ),
        client.post(
            material_create_path(project, cycle),
            {"pasted_transcript": "MEMBER_TRANSCRIPT_SCOPE_SECRET"},
        ),
        client.post(publish_path(project, cycle), publish_payload(owner, coworker)),
    ]
    for response in member_mutation_responses:
        assert response.status_code == 404
        assert_not_rendered(response, [*secrets, "MEMBER_TRANSCRIPT_SCOPE_SECRET"])
    assert not MeetingMaterial.objects.filter(
        pasted_transcript_text="MEMBER_TRANSCRIPT_SCOPE_SECRET"
    ).exists()

    own_complete_response = client.post(
        action_owner_complete_path(project, cycle, owner_action)
    )
    owner_action.refresh_from_db()
    assert own_complete_response.status_code == 302
    assert owner_action.status == ActionItem.Status.DONE

    coworker_complete_response = client.post(
        action_owner_complete_path(project, cycle, coworker_action)
    )
    coworker_action.refresh_from_db()
    assert coworker_complete_response.status_code == 404
    assert_not_rendered(coworker_complete_response, secrets)
    assert coworker_action.status == ActionItem.Status.OPEN

    client.force_login(facilitator)
    assert client.post(
        publish_path(project, cycle),
        publish_payload(facilitator, owner),
    ).status_code == 302
    cycle.refresh_from_db()
    assert cycle.status == FeedbackCycle.Status.COMPLETED

    client.force_login(outsider)
    for response in [
        client.get(summary_path(project, cycle)),
        client.get(board_path(project, cycle)),
        client.get(dashboard_path(project)),
    ]:
        assert response.status_code == 404
        assert_not_rendered(response, secrets)
