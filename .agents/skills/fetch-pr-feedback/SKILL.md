---
name: fetch-pr-feedback
description: Use when fetching GitHub PR feedback for this benchmark repository, including an explicit PR number, author comments, or resolved threads.
---

# Fetch PR Feedback

Collect reviewer feedback on a PR in this repository, keeping enough evidence to
verify each finding and reply to its exact comment or thread. Fetching is read-only.
Afterwards, continue straight into [receive-pr-feedback](../receive-pr-feedback/SKILL.md)
unless the request is fetch-only.

Read these options from the request:

```text
fetch-pr-feedback [--pr <number>] [--include-author] [--include-resolved]
```

By default the PR author's comments, the current user's comments and resolved inline
threads are left out. `--include-author` adds the author's comments; the current
user's stay out. `--include-resolved` adds resolved threads. `--pr` selects that PR
even when the current branch has another.

## Resolve the target

Read [AGENTS.md](../../../AGENTS.md) and discover the repository from the checkout.
Fetch into a fresh directory and parse separately, so a successful `jq` can't hide a
failed `gh`:

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

Check that `--pr` is a positive integer first. Report "No PR found for current branch;
use `--pr`" only when the error shows there is no branch PR. Authentication,
permission, network and parsing errors are failed fetches, not empty feedback.

## Fetch everything

Fetch all three REST collections: conversation comments, review bodies and inline
review comments.

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

Also fetch GraphQL review threads for resolution state and reply identity, paging
both threads and their comments as [thread-pagination.md](thread-pagination.md)
describes. Treat GraphQL `errors`, a null target, a missing connection or a cursor
that doesn't advance as failures, even when `gh` exits 0.

Join REST inline comments to thread comments by `databaseId` (REST `id`). If a comment
has no thread, fetch once more; if it's still missing, report an incomplete fetch.
Never assume an unmatched comment is unresolved.

The fetch is complete when every REST collection parsed, every GraphQL connection
reached `hasNextPage: false` and every inline comment has a thread. Then confirm the
head didn't move, and refresh before evaluating if it did:

```bash
gh pr view "$PR_NUMBER" --repo "$OWNER/$REPO" --json headRefOid \
  > "$FEEDBACK_DIR/pr-final.json"
jq -e --slurpfile initial "$FEEDBACK_DIR/pr.json" \
  '.headRefOid == $initial[0].headRefOid' "$FEEDBACK_DIR/pr-final.json" > /dev/null
```

## Format the handoff

Apply the author and resolution filters to the handoff view only; keep the raw
evidence unfiltered. Group by reviewer into Conversation, Submitted Reviews and
Line-Specific sections, skipping empty review bodies. Record the fetch time,
repository, PR number and URL, head SHA, fork identity, filters and counts.

For each item keep its source type, REST and node IDs, URL, reviewer, timestamps and
review state. For inline comments also keep the thread ID, `isResolved`, `isOutdated`,
`in_reply_to_id`, review ID, path, side, current and original lines, commit IDs and
diff hunk. Label original lines as original; a null current line means outdated.

```markdown
# PR #123 Review Feedback
Repository: existential-birds/code-review-benchmarks | PR: https://github.com/existential-birds/code-review-benchmarks/pull/123
Head: <captured SHA> | Acquired: <timestamp> | Raw evidence: <directory>
Filters: author excluded; current user excluded; resolved threads excluded

## Reviewer: reviewer-login

### Line-Specific Comments
**File:** `benchmarks/aacr/convert.py:42` (RIGHT)
**Comment:** <REST ID> | **Node:** <node ID> | **Thread:** <thread ID>
**URL:** <exact comment URL> | **Resolved:** false | **Outdated:** false
**Reply parent:** <ID or null> | **Review:** <review ID>
<Original comment body, or labeled excerpt with raw-record pointer>
```

Collapse bot boilerplate only in the display copy, and mark shortened bodies as
excerpts; read the full body before judging it. Deduplicate findings by meaning while
keeping every source ID.

If nothing matches the filters, say "No reviewer feedback matches the selected
filters" with the filters and raw counts. Report a failed or partial fetch as such,
never as empty.

## Hand off

After a complete fetch with matching feedback, load
[receive-pr-feedback](../receive-pr-feedback/SKILL.md) in the same workflow with the
artifact, raw-evidence location, IDs, head SHA and the user's scope. No separate
confirmation is needed. A fetch-only request ends with the artifact. Fetching never
authorizes replying to or resolving threads.
