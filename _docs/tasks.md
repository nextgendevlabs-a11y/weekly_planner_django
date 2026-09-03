# Weekly Team Feedback Tool - MVP Backlog

## 1. Set up an empty Django project with a passing test
Goal: Create the initial Django project structure and verify the test runner works.
Description: Initialize an empty Django project using the selected MVP stack direction: Django, PostgreSQL-ready settings, server-rendered templates, HTMX-friendly views, and background jobs later. Add a minimal placeholder test so the repository has a known-good baseline before any product behavior is added.

## 2. Add local configuration and base layout
Goal: Make the app runnable in local development with a coherent page shell.
Description: Configure environment-based settings for secret key, debug mode, allowed hosts, database URL, static files, media files, and upload limits. Add a base template, simple navigation, and a home page so later screens have a shared structure.

## 3. Add authentication and protected-page behavior
Goal: Allow users to sign up, sign in, sign out, and access only authenticated screens.
Description: Use Django's built-in authentication system with simple templates for account entry points. Add tests proving users can authenticate and that project workflow pages redirect anonymous users.

## 4. Add projects, memberships, and facilitator permissions
Goal: Represent project teams and distinguish facilitators from regular team members.
Description: Add project and membership models, including role information for facilitators and team members. Build reusable permission helpers and tests for viewing projects, facilitating projects, and preventing non-members from accessing project data.

## 5. Build project list and project dashboard foundation
Goal: Let signed-in users navigate to the projects they belong to.
Description: Add project list and project detail pages scoped to the signed-in user's memberships. The dashboard should reserve space for the current feedback cycle, submission status, retrospective link, previous retrospectives, and open action items, even if some sections are empty at first.

## 6. Add feedback cycles and facilitator creation flow
Goal: Let a facilitator create one active weekly feedback cycle for a project.
Description: Add a feedback cycle model with project, facilitator, label, status, opening time, and optional closing time. Add a facilitator-only form for creating a cycle and prevent duplicate active cycles for the same project.

## 7. Build private Start, Stop, Continue feedback submission
Goal: Let team members submit and edit their own feedback cards before reveal.
Description: Add feedback cards as separate Start, Stop, and Continue entries with an anonymous option per card. Build the feedback form so contributors can manage only their own cards while the cycle is collecting feedback, and add tests for category validation and pre-reveal privacy.

## 8. Show submission progress without exposing feedback
Goal: Help facilitators see who has submitted while keeping card content private.
Description: Add submission status to the project dashboard for the active cycle, showing whether each member has submitted at least one card. Ensure facilitators can track participation without seeing unrevealed feedback content.

## 9. Reveal feedback and display the retrospective board
Goal: Start the retrospective by revealing all submitted cards at once.
Description: Add a facilitator-only reveal action that moves the cycle into the retrospective flow. Build the first retrospective board view showing all revealed cards while preserving anonymous authorship rules.

## 10. Add manual clustering on the board
Goal: Let the team organize revealed cards into editable discussion themes.
Description: Add clusters for a feedback cycle and allow cards to be assigned to a cluster or left ungrouped. Build facilitator controls for creating clusters, renaming clusters, moving cards, merging clusters, splitting clusters, and keeping the board state understandable after each change.

## 11. Add editable AI cluster suggestions
Goal: Suggest thematic clusters while keeping the facilitator in control.
Description: Add a clustering service interface and an initial implementation that can group revealed feedback cards into draft clusters. The facilitator must be able to review, edit, accept, or ignore suggestions before they affect the board.

## 12. Add three-vote topic prioritization
Goal: Let participants vote on clusters and reveal ranked results after voting closes.
Description: Add voting tied to a cycle, voter, and cluster, with exactly three stackable votes available per member. Build the voting board mode, hide live totals while voting is open, and let the facilitator close voting to reveal ranked discussion topics.

## 13. Add discussion workflow, notes, and topic status
Goal: Support the live retrospective discussion after voting.
Description: Add discussion mode to the board with topics ordered by vote results. Let the facilitator mark each topic as discussed, skipped, or deferred and record notes for each topic.

## 14. Add action items and manual decision capture
Goal: Record the concrete outcomes of the retrospective.
Description: Add action items with description, owner, optional due date, open or done status, and related discussion topic. Add confirmed decisions connected to the retrospective or topic, and build facilitator forms for manually creating and editing both actions and decisions.

## 15. Let action owners complete their assigned actions
Goal: Allow team members to update the status of work assigned to them.
Description: Add a focused view or endpoint where assigned owners can mark their own open action items as done. Prevent users from updating unrelated action items unless they are project facilitators.

## 16. Add meeting material upload and processing status
Goal: Let facilitators upload or paste meeting records for post-meeting processing.
Description: Add a meeting record model and page where facilitators can upload audio, video, transcript files, or pasted transcript text. Store the source material and show processing states such as queued, processing, succeeded, and failed.

## 17. Add background jobs for transcription and extraction
Goal: Process meeting material outside the web request.
Description: Configure Celery and Redis for local development and add background tasks for transcript generation and outcome extraction. Use service interfaces so provider-specific transcription and AI extraction can be replaced without changing the review workflow.

## 18. Build facilitator review for extracted outcomes
Goal: Keep AI-generated results as drafts until a facilitator approves them.
Description: Add a review screen showing draft decisions, action items, owners, due dates, and summary text extracted from the meeting record. Let the facilitator edit, approve, or discard drafts, and only save confirmed outcomes after approval.

## 19. Build the retrospective summary page
Goal: Publish a completed retrospective record in one place.
Description: Add a summary page with top discussion topics, notes, confirmed decisions, confirmed action items, attendance and participation, and original feedback cards. Ensure anonymous feedback remains anonymous in all summary views and query paths.

## 20. Finish the project dashboard
Goal: Make the project page useful for repeated weekly operation.
Description: Fill the dashboard with current cycle state, submission progress, retrospective entry points, previous retrospective summaries, and open action items. Keep all data scoped to project membership and facilitator permissions.

## 21. Add end-to-end MVP smoke tests
Goal: Verify the complete first-version workflow works.
Description: Add tests that create a project, create a cycle, submit private feedback, reveal cards, cluster them, vote, discuss topics, add actions and decisions, process drafted outcomes, approve results, and view the final summary. Use a small fixture team and controlled fake transcription and extraction services.

## 22. Add anonymity and permission regression tests
Goal: Protect the most important trust and access boundaries in the MVP.
Description: Add focused tests for unrevealed feedback privacy, anonymous author hiding after reveal, facilitator-only actions, member-only project access, and action-owner status updates. These tests should exercise views and query helpers, not only model methods.

## 23. Add developer setup documentation
Goal: Help a new contributor run the MVP locally.
Description: Document Python setup, dependency installation, database setup, Redis, environment variables, migrations, test execution, and local worker processes. Keep the instructions short and current with the actual project commands.
