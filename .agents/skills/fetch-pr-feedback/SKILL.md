---
name: fetch-pr-feedback
description: Use when fetching GitHub PR feedback for this benchmark repository, including an explicit PR number, author comments, or resolved threads.
---

# Fetch PR Feedback

Collect reviewer feedback for this repository's PR, preserving enough evidence to verify
each finding and later reply to its exact GitHub comment or thread. Fetching is
read-only on GitHub. After acquisition, continue directly into `receive-pr-feedback`
to verify the findings and present its fix-launch decision. An explicitly
fetch-only request ends with the feedback artifact.

Interpret these options from the request, rather than treating this skill name as
an installed executable:

```text
fetch-pr-feedback [--pr <number>] [--include-author] [--include-resolved]
```

`--pr` selects that PR, even when the current branch has another PR.
`--include-author` includes the PR author's comments; the authenticated user's
comments remain excluded. `--include-resolved` includes resolved inline threads.
The default excludes both authors and resolved inline threads.

## Resolve the target

Read [AGENTS.md](../../../AGENTS.md). Discover the repository from
the checkout rather than hard-coding an owner, fork, or branch. Commands below use
shell variables populated from successful JSON responses, not literal placeholders.

Use a fresh directory and separate fetching from parsing so a successful JSON
formatter cannot hide a failed `gh` command:

```bash
set -euo pipefail
FEEDBACK_DIR=$(mktemp -d "${TMPDIR:-/tmp}/code-review-benchmarks-pr-feedback.XXXXXX")
gh repo view --json owner,name > "$FEEDBACK_DIR/repository.json"
OWNER=$(jq -er '.owner.login' "$FEEDBACK_DIR/repository.json")
REPO=$(jq -er '.name' "$FEEDBACK_DIR/repository.json")
gh api user > "$FEEDBACK_DIR/user.json"
CURRENT_USER=$(jq -er '.login' "$FEEDBACK_DIR/user.json")

# Set PR_NUMBER to the explicit --pr value, or leave it empty for branch detection.
if [ -n "${PR_NUMBER:-}" ]; then
  gh pr view "$PR_NUMBER" --repo "$OWNER/$REPO" \
    --json number,url,author,headRefName,headRefOid,headRepository,headRepositoryOwner,isCrossRepository \
    > "$FEEDBACK_DIR/pr.json"
else
  gh pr view --repo "$OWNER/$REPO" \
    --json number,url,author,headRefName,headRefOid,headRepository,headRepositoryOwner,isCrossRepository \
    > "$FEEDBACK_DIR/pr.json"
fi
PR_NUMBER=$(jq -er '.number | select(. > 0)' "$FEEDBACK_DIR/pr.json")
PR_AUTHOR=$(jq -er '.author.login' "$FEEDBACK_DIR/pr.json")
```

Validate an explicit PR argument as a positive integer before executing these
commands. On a failure, retain the command error and report which acquisition
failed. Only report “No PR found for current branch; use `--pr`” when the error
actually establishes that there is no branch PR. Authentication, permission,
network, and parsing failures mean acquisition failed, not that feedback is empty.

## Acquire complete evidence

Keep raw API responses in `FEEDBACK_DIR`. Fetch all three REST collections: PR
conversation comments, submitted review bodies, and inline review comments.

```bash
gh api --paginate --slurp \
  "repos/$OWNER/$REPO/issues/$PR_NUMBER/comments?per_page=100" \
  > "$FEEDBACK_DIR/issue-comment-pages.json"
gh api --paginate --slurp \
  "repos/$OWNER/$REPO/pulls/$PR_NUMBER/reviews?per_page=100" \
  > "$FEEDBACK_DIR/review-pages.json"
gh api --paginate --slurp \
  "repos/$OWNER/$REPO/pulls/$PR_NUMBER/comments?per_page=100" \
  > "$FEEDBACK_DIR/review-comment-pages.json"

for COLLECTION in issue-comment review review-comment; do
  jq -e 'type == "array" and all(.[]; type == "array")' \
    "$FEEDBACK_DIR/$COLLECTION-pages.json" > /dev/null
  jq 'add // []' "$FEEDBACK_DIR/$COLLECTION-pages.json" \
    > "$FEEDBACK_DIR/${COLLECTION}s.json"
done
```

Also collect GraphQL review threads for resolution state and reply identity,
including when `--include-resolved` is selected. Read
[thread-pagination.md](thread-pagination.md) and complete **both** thread and
nested-comment pagination. A first page of 100 nodes is never proof of completion.
Treat GraphQL `errors`, a null target, missing connections, or an unadvanced cursor
as acquisition failures even when the process returned exit code zero.

Join REST inline comments to GraphQL thread comments by `databaseId` (REST `id`).
Every inline comment must have its thread identified before filtering by
resolution. If a join is missing, refresh the collections once to account for
concurrent review activity; if it remains missing, report incomplete acquisition
and retain the evidence. Never guess that an unmatched comment is unresolved.

Completion requires every REST collection to have parsed successfully, every
GraphQL connection to have reached `hasNextPage: false`, and every inline comment
to have a known thread. Record acquisition time, owner/repo, PR number and URL,
head SHA, fork-head repository identity, filters, and collection counts in the
artifact. Check the head again after the final acquisition page:

```bash
gh pr view "$PR_NUMBER" --repo "$OWNER/$REPO" --json headRefOid \
  > "$FEEDBACK_DIR/pr-final.json"
jq -e --slurpfile initial "$FEEDBACK_DIR/pr.json" \
  '.headRefOid == $initial[0].headRefOid' "$FEEDBACK_DIR/pr-final.json" > /dev/null
```

If this comparison fails, mark the snapshot as changed and refresh before
evaluating code against it. A head check binds evidence to a commit; discussion
can still change, so a reply workflow must refresh reviewer activity before
posting.

## Format the handoff

Apply author/current-user exclusions to each collection. Filter inline comments
by their thread's `isResolved` unless `--include-resolved` was requested. Preserve
the unfiltered raw evidence; filtering affects the handoff view only.

Group feedback by reviewer with separate Conversation, Submitted Reviews, and
Line-Specific sections. Omit empty review bodies from the prose view, retaining
their review state in raw evidence. For every displayed item preserve:

- Source type, REST database ID, GraphQL/node ID when available, and GitHub URL.
- Reviewer, created/updated or submitted timestamps, and review state if relevant.
- For inline comments: thread node ID, `isResolved`, `isOutdated`, reply parent
  `in_reply_to_id`, associated review ID, path, side, line/start line, original
  line/start line, commit/original commit IDs, and diff hunk when available.
- Full original body or an explicit pointer to its raw record. Derived excerpts
  must never replace the original evidence used for evaluation or reply matching.

Use a null current line as “outdated” or “line unavailable”; label original lines
as original rather than presenting them as current checkout locations. Example:

```markdown
# PR #123 Review Feedback
Repository: existential-birds/code-review-benchmarks | PR: https://github.com/existential-birds/code-review-benchmarks/pull/123
Head: <captured SHA> | Acquired: <timestamp> | Raw evidence: <directory>
Filters: author excluded; current user excluded; resolved threads excluded

## Reviewer: reviewer-login

### Line-Specific Comments
**File:** `benchmarks/aacr/converter.py:42` (RIGHT; illustrative path)
**Comment:** <REST ID> | **Node:** <node ID> | **Thread:** <thread ID>
**URL:** <exact comment URL> | **Resolved:** false | **Outdated:** false
**Reply parent:** <ID or null> | **Review:** <review ID>
<Original comment body, or labeled excerpt with raw-record pointer>
```

For readability, collapse known bot boilerplate in a display-only copy. Preserve
suggested fixes, code blocks, hidden findings, and full bodies in raw evidence.
If a display body is shortened, mark it as an excerpt and read the full body
before deciding whether it is actionable. Deduplicate findings by meaning while
retaining all source IDs; several findings may share one comment and one finding
may have several comments.

When the fully fetched, filtered set is empty, report “No reviewer feedback
matches the selected filters,” naming those filters and the raw counts. A failed
or partial fetch must be reported as such instead of producing an empty verdict.

## Continue directly to receive-pr-feedback

After a complete fetch with matching feedback, immediately load and execute
[receive-pr-feedback](../receive-pr-feedback/SKILL.md) in the same workflow. Pass the
grouped artifact, full bodies and raw-evidence location, source identities,
captured head SHA, and user scope. No separate request or confirmation is needed
to begin verification; returning the artifact alone does not complete the default
workflow. That skill owns the fix-launch decision, subagent fixes, validation,
push decision, and response handoff after publication.

For an explicitly fetch-only request, return the grouped artifact and raw-evidence
location. For an empty filtered set, report the filters and counts as above; for
incomplete acquisition, report the failure before evaluating partial evidence.

Preserve any existing GitHub reply authorization for receive-pr-feedback's response
handoff to [respond-pr-feedback](../respond-pr-feedback/SKILL.md), retaining
comment/thread IDs throughout. Fetching itself grants no authority to post a
response or resolve a thread.
