from datetime import date

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from django.utils import timezone

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
    MeetingMaterialTranscript,
    Membership,
    Project,
    RetrospectiveDecision,
)


pytestmark = pytest.mark.django_db


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


def create_material(
    cycle,
    submitter,
    *,
    processing_status=MeetingMaterial.ProcessingStatus.SUCCEEDED,
    pasted_transcript_text="Decision: keep release reviews",
):
    material = MeetingMaterial.objects.create(
        cycle=cycle,
        submitted_by=submitter,
        source_type=MeetingMaterial.SourceType.PASTED_TRANSCRIPT,
        processing_status=processing_status,
        pasted_transcript_text=pasted_transcript_text,
        text_character_count=len(pasted_transcript_text),
    )
    if processing_status == MeetingMaterial.ProcessingStatus.SUCCEEDED:
        MeetingMaterialTranscript.objects.create(
            meeting_material=material,
            text=f"Processed {pasted_transcript_text}",
            character_count=len(f"Processed {pasted_transcript_text}"),
        )
    return material


def create_draft(
    material,
    *,
    summary_text="Draft summary",
    review_status=MeetingMaterialExtractionDraft.ReviewStatus.PENDING,
):
    return MeetingMaterialExtractionDraft.objects.create(
        meeting_material=material,
        retrospective_summary_text=summary_text,
        review_status=review_status,
    )


def create_draft_decision(
    draft,
    *,
    text="Keep release readiness reviews",
    topic_candidate="Release readiness",
    matched_topic=None,
):
    return MeetingMaterialDraftDecision.objects.create(
        extraction_draft=draft,
        text=text,
        topic_candidate=topic_candidate,
        matched_topic=matched_topic,
    )


def create_draft_action(
    draft,
    *,
    description="Update the release checklist",
    owner_candidate="owner",
    matched_owner=None,
    due_date=date(2026, 9, 30),
    topic_candidate="Release readiness",
    matched_topic=None,
):
    return MeetingMaterialDraftActionItem.objects.create(
        extraction_draft=draft,
        description=description,
        owner_candidate=owner_candidate,
        matched_owner=matched_owner,
        due_date=due_date,
        topic_candidate=topic_candidate,
        matched_topic=matched_topic,
    )


def board_path(project, cycle):
    return reverse(
        "retrospective_board",
        kwargs={"project_id": project.pk, "cycle_id": cycle.pk},
    )


def approve_path(project, cycle, material, draft):
    return reverse(
        "meeting_material_extraction_draft_approve",
        kwargs={
            "project_id": project.pk,
            "cycle_id": cycle.pk,
            "meeting_material_id": material.pk,
            "extraction_draft_id": draft.pk,
        },
    )


def discard_path(project, cycle, material, draft):
    return reverse(
        "meeting_material_extraction_draft_discard",
        kwargs={
            "project_id": project.pk,
            "cycle_id": cycle.pk,
            "meeting_material_id": material.pk,
            "extraction_draft_id": draft.pk,
        },
    )


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
            ""
            if draft_decision.matched_topic_id is None
            else str(draft_decision.matched_topic_id)
        )
    for draft_action in draft.draft_action_items.all():
        data[f"action_{draft_action.pk}_description"] = draft_action.description
        data[f"action_{draft_action.pk}_owner"] = (
            ""
            if draft_action.matched_owner_id is None
            else str(draft_action.matched_owner_id)
        )
        data[f"action_{draft_action.pk}_due_date"] = (
            "" if draft_action.due_date is None else draft_action.due_date.isoformat()
        )
        data[f"action_{draft_action.pk}_topic"] = (
            "" if draft_action.matched_topic_id is None else str(draft_action.matched_topic_id)
        )
    return data


def assert_no_secret_leak(response, secrets):
    content = response.content.decode()
    for secret in secrets:
        assert secret not in content


def test_facilitator_review_visibility_and_member_draft_restrictions(client):
    facilitator = create_user("facilitator")
    member = create_user("member")
    owner = create_user("owner")
    project = create_project("Review Visibility Project")
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    add_membership(member, project)
    add_membership(owner, project)
    cycle = create_cycle(project, facilitator, label="Review Visibility Week")
    topic = create_cluster(cycle, "Release readiness")
    material = create_material(cycle, facilitator, pasted_transcript_text="Secret transcript body")
    draft = create_draft(material, summary_text="Secret draft summary")
    create_draft_decision(draft, text="Secret draft decision", matched_topic=topic)
    create_draft_action(
        draft,
        description="Secret draft action",
        matched_owner=owner,
        matched_topic=topic,
    )
    approved_material = create_material(
        cycle,
        facilitator,
        pasted_transcript_text="Already reviewed transcript",
    )
    approved_draft = create_draft(
        approved_material,
        summary_text="Already reviewed draft",
        review_status=MeetingMaterialExtractionDraft.ReviewStatus.APPROVED,
    )
    failed_material = create_material(
        cycle,
        facilitator,
        processing_status=MeetingMaterial.ProcessingStatus.FAILED,
        pasted_transcript_text="Failed secret transcript",
    )
    failed_draft = create_draft(failed_material, summary_text="Failed draft summary")
    failed_material.failure_message = "Review failure status"
    failed_material.save(update_fields=["failure_message", "updated_at"])

    client.force_login(facilitator)
    response = client.get(board_path(project, cycle))
    content = response.content.decode()

    assert response.status_code == 200
    assert "Extracted outcome review" in content
    assert "Processed Secret transcript body" in content
    assert "Secret draft summary" in content
    assert "Secret draft decision" in content
    assert "Secret draft action" in content
    assert "Topic candidate: Release readiness" in content
    assert "Matched owner: owner" in content
    assert "Review state: Pending review" in content
    assert "Review state: Approved" in content
    assert "Review state: Pending review" in content
    assert approve_path(project, cycle, material, draft) in content
    assert discard_path(project, cycle, material, draft) in content
    assert approve_path(project, cycle, approved_material, approved_draft) not in content
    assert discard_path(project, cycle, failed_material, failed_draft) not in content

    client.force_login(member)
    member_response = client.get(board_path(project, cycle))
    member_content = member_response.content.decode()

    assert member_response.status_code == 200
    assert "Meeting material" in member_content
    assert "Status: Succeeded" in member_content
    assert "Secret transcript body" not in member_content
    assert "Processed Secret transcript body" not in member_content
    assert "Secret draft summary" not in member_content
    assert "Secret draft decision" not in member_content
    assert "Secret draft action" not in member_content
    assert "Release readiness" in member_content
    assert "Approve extracted outcomes" not in member_content
    assert "Discard extracted outcomes" not in member_content


def test_approving_reviewed_draft_creates_confirmed_outcomes_and_preserves_scope(client):
    facilitator = create_user("facilitator")
    owner = create_user("owner")
    next_owner = create_user("next-owner")
    member = create_user("member")
    project = create_project("Approve Review Project")
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    add_membership(owner, project)
    add_membership(next_owner, project)
    add_membership(member, project)
    cycle = create_cycle(
        project,
        facilitator,
        label="Approve Review Week",
    )
    topic = create_cluster(cycle, "Release readiness", discussion_notes="Keep notes")
    next_topic = create_cluster(cycle, "Planning quality")
    card = FeedbackCard.objects.create(
        cycle=cycle,
        author=member,
        category=FeedbackCard.Category.START,
        text="Do not mutate feedback",
        cluster=topic,
    )
    vote = FeedbackClusterVote.objects.create(
        cycle=cycle,
        voter=member,
        cluster=topic,
        vote_count=3,
    )
    existing_action = ActionItem.objects.create(
        cycle=cycle,
        owner=owner,
        topic=topic,
        description="Existing action",
    )
    existing_decision = RetrospectiveDecision.objects.create(
        cycle=cycle,
        topic=topic,
        text="Existing decision",
    )
    material = create_material(cycle, facilitator, pasted_transcript_text="Review source")
    draft = create_draft(material, summary_text="Original summary")
    draft_decision = create_draft_decision(draft, matched_topic=topic)
    draft_action = create_draft_action(draft, matched_owner=owner, matched_topic=topic)
    other_material = create_material(cycle, facilitator, pasted_transcript_text="Other pending")
    other_draft = create_draft(other_material, summary_text="Other pending summary")
    client.force_login(facilitator)

    data = review_payload(material, draft, summary_text="  Edited approved summary  ")
    data[f"decision_{draft_decision.pk}_text"] = "  Keep Friday release reviews  "
    data[f"decision_{draft_decision.pk}_topic"] = str(next_topic.pk)
    data[f"action_{draft_action.pk}_description"] = "  Update the readiness checklist  "
    data[f"action_{draft_action.pk}_owner"] = str(next_owner.pk)
    data[f"action_{draft_action.pk}_due_date"] = ""
    data[f"action_{draft_action.pk}_topic"] = str(next_topic.pk)

    response = client.post(approve_path(project, cycle, material, draft), data)

    assert response.status_code == 302
    draft.refresh_from_db()
    other_draft.refresh_from_db()
    cycle.refresh_from_db()
    material.refresh_from_db()
    assert draft.review_status == MeetingMaterialExtractionDraft.ReviewStatus.APPROVED
    assert other_draft.review_status == MeetingMaterialExtractionDraft.ReviewStatus.PENDING
    assert cycle.approved_retrospective_summary_text == "Edited approved summary"
    assert material.processing_status == MeetingMaterial.ProcessingStatus.SUCCEEDED
    assert material.pasted_transcript_text == "Review source"
    assert material.processed_transcript.text == "Processed Review source"

    created_decision = RetrospectiveDecision.objects.get(
        text="Keep Friday release reviews"
    )
    created_action = ActionItem.objects.get(
        description="Update the readiness checklist"
    )
    assert created_decision.cycle == cycle
    assert created_decision.topic == next_topic
    assert created_action.cycle == cycle
    assert created_action.owner == next_owner
    assert created_action.due_date is None
    assert created_action.topic == next_topic
    assert created_action.status == ActionItem.Status.OPEN

    existing_action.refresh_from_db()
    existing_decision.refresh_from_db()
    topic.refresh_from_db()
    card.refresh_from_db()
    vote.refresh_from_db()
    assert existing_action.description == "Existing action"
    assert existing_decision.text == "Existing decision"
    assert topic.name == "Release readiness"
    assert topic.discussion_notes == "Keep notes"
    assert card.text == "Do not mutate feedback"
    assert card.cluster == topic
    assert vote.vote_count == 3

    board_response = client.get(board_path(project, cycle))
    board_content = board_response.content.decode()
    assert "Update the readiness checklist" in board_content
    assert "Owner: next-owner" in board_content
    assert "Status: Open" in board_content
    assert "Due: No due date" in board_content
    assert "Keep Friday release reviews" in board_content
    assert "Topic: Planning quality" in board_content
    assert approve_path(project, cycle, material, draft) not in board_content
    assert approve_path(project, cycle, other_material, other_draft) in board_content

    client.force_login(member)
    member_response = client.get(board_path(project, cycle))
    member_content = member_response.content.decode()
    assert "Update the readiness checklist" in member_content
    assert "Keep Friday release reviews" in member_content
    assert "Edited approved summary" not in member_content
    assert "Original summary" not in member_content
    assert "Processed Review source" not in member_content
    assert "Owner candidate" not in member_content


def test_approving_empty_draft_marks_reviewed_without_confirmed_outcomes(client):
    facilitator = create_user("facilitator")
    project = create_project("Empty Review Project")
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    cycle = create_cycle(project, facilitator)
    material = create_material(cycle, facilitator, pasted_transcript_text="No outcomes")
    draft = create_draft(material, summary_text="")
    client.force_login(facilitator)

    board_response = client.get(board_path(project, cycle))
    content = board_response.content.decode()
    assert "No draft summary was extracted." in content
    assert "No draft decisions were extracted." in content
    assert "No draft action items were extracted." in content
    assert "No extracted outcomes are waiting in this draft." in content
    assert discard_path(project, cycle, material, draft) in content

    response = client.post(
        approve_path(project, cycle, material, draft),
        review_payload(material, draft, summary_text="   "),
    )

    assert response.status_code == 302
    draft.refresh_from_db()
    cycle.refresh_from_db()
    assert draft.review_status == MeetingMaterialExtractionDraft.ReviewStatus.APPROVED
    assert cycle.approved_retrospective_summary_text == ""
    assert ActionItem.objects.count() == 0
    assert RetrospectiveDecision.objects.count() == 0


def test_review_validation_errors_do_not_create_or_consume_draft(client):
    facilitator = create_user("facilitator")
    owner = create_user("owner")
    project = create_project("Review Validation Project")
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    add_membership(owner, project)
    cycle = create_cycle(project, facilitator)
    cycle.approved_retrospective_summary_text = "Existing approved summary"
    cycle.save(update_fields=["approved_retrospective_summary_text", "updated_at"])
    topic = create_cluster(cycle, "Validation topic")
    material = create_material(cycle, facilitator, pasted_transcript_text="Validation source")
    draft = create_draft(material, summary_text="Validation summary")
    draft_decision = create_draft_decision(draft, matched_topic=topic)
    draft_action = create_draft_action(
        draft,
        matched_owner=None,
        matched_topic=None,
        due_date=None,
    )
    client.force_login(facilitator)

    data = review_payload(material, draft, summary_text="  Should not save  ")
    data[f"decision_{draft_decision.pk}_text"] = "   "
    data[f"action_{draft_action.pk}_description"] = "   "
    data[f"action_{draft_action.pk}_owner"] = ""
    data[f"action_{draft_action.pk}_due_date"] = "not-a-date"
    data[f"action_{draft_action.pk}_topic"] = ""

    response = client.post(approve_path(project, cycle, material, draft), data)
    content = response.content.decode()

    assert response.status_code == 200
    assert "Decision text cannot be empty." in content
    assert "Action item description cannot be empty." in content
    assert "Choose an active project member as the action item owner." in content
    assert "Choose a discussion topic from this cycle." in content
    assert "Enter a valid due date." in content
    draft.refresh_from_db()
    cycle.refresh_from_db()
    assert draft.review_status == MeetingMaterialExtractionDraft.ReviewStatus.PENDING
    assert cycle.approved_retrospective_summary_text == "Existing approved summary"
    assert ActionItem.objects.count() == 0
    assert RetrospectiveDecision.objects.count() == 0


def test_review_tampering_is_rejected_without_leakage_or_mutation(client):
    facilitator = create_user("facilitator")
    owner = create_user("owner")
    inactive = create_user("inactive-owner", is_active=False)
    non_member = create_user("non-member")
    other_owner = create_user("other-owner")
    project = create_project("Secret Review Project")
    other_project = create_project("Other Secret Review Project")
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    add_membership(owner, project)
    add_membership(inactive, project)
    add_membership(other_owner, other_project)
    cycle = create_cycle(project, facilitator, label="Secret Review Week")
    other_cycle = create_cycle(other_project, facilitator, label="Other Secret Week")
    topic = create_cluster(cycle, "Secret review topic")
    other_topic = create_cluster(other_cycle, "Other secret topic")
    material = create_material(cycle, facilitator, pasted_transcript_text="Secret source")
    draft = create_draft(material, summary_text="Secret draft summary")
    draft_decision = create_draft_decision(
        draft,
        text="Secret draft decision",
        matched_topic=topic,
    )
    draft_action = create_draft_action(
        draft,
        description="Secret draft action",
        matched_owner=owner,
        matched_topic=topic,
    )
    other_material = create_material(other_cycle, facilitator, pasted_transcript_text="Other source")
    other_draft = create_draft(other_material, summary_text="Other draft summary")
    other_draft_decision = create_draft_decision(other_draft, text="Other decision")
    other_draft_action = create_draft_action(other_draft, description="Other action")
    path = approve_path(project, cycle, material, draft)
    secrets = [
        "Secret Review Project",
        "Secret Review Week",
        "Secret review topic",
        "Secret source",
        "Secret draft summary",
        "Secret draft decision",
        "Secret draft action",
        "Other Secret Review Project",
        "Other Secret Week",
        "Other secret topic",
        "Other draft summary",
        "Other decision",
        "Other action",
        "other-owner",
        "inactive-owner",
        "non-member",
    ]
    client.force_login(facilitator)

    tampered_payloads = []
    bad_material = review_payload(material, draft)
    bad_material["material_id"] = str(other_material.pk)
    tampered_payloads.append(bad_material)
    bad_draft = review_payload(material, draft)
    bad_draft["extraction_draft_id"] = str(other_draft.pk)
    tampered_payloads.append(bad_draft)
    bad_decision_count = review_payload(material, draft)
    bad_decision_count["draft_decision_count"] = "0"
    tampered_payloads.append(bad_decision_count)
    bad_action_count = review_payload(material, draft)
    bad_action_count["draft_action_item_count"] = "0"
    tampered_payloads.append(bad_action_count)
    bad_decision_id = review_payload(material, draft)
    bad_decision_id[f"decision_{other_draft_decision.pk}_text"] = "Tamper"
    tampered_payloads.append(bad_decision_id)
    bad_action_id = review_payload(material, draft)
    bad_action_id[f"action_{other_draft_action.pk}_description"] = "Tamper"
    tampered_payloads.append(bad_action_id)
    bad_decision_topic = review_payload(material, draft)
    bad_decision_topic[f"decision_{draft_decision.pk}_topic"] = str(other_topic.pk)
    tampered_payloads.append(bad_decision_topic)
    bad_action_topic = review_payload(material, draft)
    bad_action_topic[f"action_{draft_action.pk}_topic"] = str(other_topic.pk)
    tampered_payloads.append(bad_action_topic)
    for bad_owner in [inactive, non_member, other_owner]:
        bad_owner_payload = review_payload(material, draft)
        bad_owner_payload[f"action_{draft_action.pk}_owner"] = str(bad_owner.pk)
        tampered_payloads.append(bad_owner_payload)
    nonexistent_owner = review_payload(material, draft)
    nonexistent_owner[f"action_{draft_action.pk}_owner"] = "999999"
    tampered_payloads.append(nonexistent_owner)
    cross_cycle_url_payload = review_payload(material, draft)
    tampered_payloads.append(cross_cycle_url_payload)

    responses = [client.post(path, payload) for payload in tampered_payloads[:-1]]
    responses.append(
        client.post(
            approve_path(project, other_cycle, material, draft),
            tampered_payloads[-1],
        )
    )
    responses.append(
        client.post(
            approve_path(other_project, cycle, material, draft),
            review_payload(material, draft),
        )
    )

    for response in responses:
        assert response.status_code == 404
        assert_no_secret_leak(response, secrets)

    draft.refresh_from_db()
    cycle.refresh_from_db()
    assert draft.review_status == MeetingMaterialExtractionDraft.ReviewStatus.PENDING
    assert cycle.approved_retrospective_summary_text == ""
    assert ActionItem.objects.count() == 0
    assert RetrospectiveDecision.objects.count() == 0


def test_duplicate_approve_and_discard_are_protected_and_do_not_duplicate(client):
    facilitator = create_user("facilitator")
    owner = create_user("owner")
    project = create_project("Duplicate Review Project")
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    add_membership(owner, project)
    cycle = create_cycle(project, facilitator)
    topic = create_cluster(cycle, "Duplicate topic")
    material = create_material(cycle, facilitator, pasted_transcript_text="Duplicate source")
    draft = create_draft(material, summary_text="First approved summary")
    create_draft_decision(draft, matched_topic=topic)
    create_draft_action(draft, matched_owner=owner, matched_topic=topic)
    discard_material = create_material(cycle, facilitator, pasted_transcript_text="Discard source")
    discard_draft = create_draft(discard_material, summary_text="Discard summary")
    other_material = create_material(cycle, facilitator, pasted_transcript_text="Other pending source")
    other_draft = create_draft(other_material, summary_text="Other pending summary")
    client.force_login(facilitator)

    approve_response = client.post(
        approve_path(project, cycle, material, draft),
        review_payload(material, draft),
    )
    duplicate_response = client.post(
        approve_path(project, cycle, material, draft),
        review_payload(material, draft, summary_text="Unexpected overwrite"),
    )
    discard_response = client.post(
        discard_path(project, cycle, discard_material, discard_draft)
    )
    duplicate_discard_response = client.post(
        discard_path(project, cycle, discard_material, discard_draft)
    )

    assert approve_response.status_code == 302
    assert duplicate_response.status_code == 404
    assert discard_response.status_code == 302
    assert duplicate_discard_response.status_code == 404
    draft.refresh_from_db()
    discard_draft.refresh_from_db()
    other_draft.refresh_from_db()
    cycle.refresh_from_db()
    assert draft.review_status == MeetingMaterialExtractionDraft.ReviewStatus.APPROVED
    assert discard_draft.review_status == MeetingMaterialExtractionDraft.ReviewStatus.DISCARDED
    assert other_draft.review_status == MeetingMaterialExtractionDraft.ReviewStatus.PENDING
    assert cycle.approved_retrospective_summary_text == "First approved summary"
    assert ActionItem.objects.count() == 1
    assert RetrospectiveDecision.objects.count() == 1

    board_content = client.get(board_path(project, cycle)).content.decode()
    assert "Review state: Approved" in board_content
    assert "Review state: Discarded" in board_content
    assert approve_path(project, cycle, other_material, other_draft) in board_content


def test_review_routes_are_post_only_csrf_protected_and_facilitator_only(client):
    facilitator = create_user("facilitator")
    member = create_user("member")
    outsider = create_user("outsider")
    admin = create_user("admin", is_staff=True, is_superuser=True)
    inactive = create_user("inactive", is_active=False)
    project = create_project("Protected Review Project")
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    add_membership(member, project)
    add_membership(inactive, project, Membership.Role.FACILITATOR)
    cycle = create_cycle(project, facilitator, label="Protected Review Week")
    topic = create_cluster(cycle, "Protected topic")
    material = create_material(cycle, facilitator, pasted_transcript_text="Protected source")
    draft = create_draft(material, summary_text="Protected draft summary")
    create_draft_decision(draft, text="Protected draft decision", matched_topic=topic)
    create_draft_action(
        draft,
        description="Protected draft action",
        matched_owner=member,
        matched_topic=topic,
    )
    approve = approve_path(project, cycle, material, draft)
    discard = discard_path(project, cycle, material, draft)
    secrets = [
        "Protected Review Project",
        "Protected Review Week",
        "Protected topic",
        "Protected source",
        "Protected draft summary",
        "Protected draft decision",
        "Protected draft action",
        "member",
    ]

    for path in [approve, discard]:
        response = client.post(path, review_payload(material, draft))
        assert response.status_code == 302
        assert response["Location"] == f"{reverse('login')}?next={path}"

    client.force_login(facilitator)
    assert client.get(approve).status_code == 405
    assert client.get(discard).status_code == 405

    csrf_client = Client(enforce_csrf_checks=True)
    csrf_client.force_login(facilitator)
    assert csrf_client.post(approve, review_payload(material, draft)).status_code == 403
    assert csrf_client.post(discard).status_code == 403

    for user in [member, outsider, admin, inactive]:
        client.force_login(user)
        for response in [
            client.post(approve, review_payload(material, draft)),
            client.post(discard),
        ]:
            assert response.status_code in {302, 404}
            assert_no_secret_leak(response, secrets)

    draft.refresh_from_db()
    assert draft.review_status == MeetingMaterialExtractionDraft.ReviewStatus.PENDING
    assert ActionItem.objects.count() == 0
    assert RetrospectiveDecision.objects.count() == 0


@pytest.mark.parametrize(
    ("status", "voting_status"),
    [
        (FeedbackCycle.Status.COLLECTING_FEEDBACK, FeedbackCycle.VotingStatus.CLOSED),
        (FeedbackCycle.Status.RETROSPECTIVE, FeedbackCycle.VotingStatus.CLUSTERING),
        (FeedbackCycle.Status.RETROSPECTIVE, FeedbackCycle.VotingStatus.OPEN),
        (FeedbackCycle.Status.COMPLETED, FeedbackCycle.VotingStatus.CLOSED),
    ],
)
def test_review_visibility_and_mutation_require_closed_voting_retrospective_stage(
    client,
    status,
    voting_status,
):
    facilitator = create_user(f"facilitator-{status}-{voting_status}")
    owner = create_user(f"owner-{status}-{voting_status}")
    project = create_project(f"Gated Review {status} {voting_status}")
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    add_membership(owner, project)
    cycle = create_cycle(
        project,
        facilitator,
        status=status,
        voting_status=voting_status,
    )
    topic = create_cluster(cycle, "Hidden review topic")
    material = create_material(
        cycle,
        facilitator,
        pasted_transcript_text="Hidden review source",
    )
    draft = create_draft(material, summary_text="Hidden review summary")
    create_draft_decision(draft, text="Hidden review decision", matched_topic=topic)
    create_draft_action(
        draft,
        description="Hidden review action",
        matched_owner=owner,
        matched_topic=topic,
    )
    client.force_login(facilitator)

    board_response = client.get(board_path(project, cycle))
    approve_response = client.post(
        approve_path(project, cycle, material, draft),
        review_payload(material, draft),
    )
    discard_response = client.post(discard_path(project, cycle, material, draft))

    if status == FeedbackCycle.Status.RETROSPECTIVE:
        assert board_response.status_code == 200
        content = board_response.content.decode()
        assert "Extracted outcome review" not in content
        assert "Hidden review summary" not in content
        assert "Hidden review decision" not in content
        assert "Hidden review action" not in content
    else:
        assert board_response.status_code == 404
    assert approve_response.status_code == 404
    assert discard_response.status_code == 404
    draft.refresh_from_db()
    cycle.refresh_from_db()
    assert draft.review_status == MeetingMaterialExtractionDraft.ReviewStatus.PENDING
    assert cycle.approved_retrospective_summary_text == ""
    assert ActionItem.objects.count() == 0
    assert RetrospectiveDecision.objects.count() == 0
