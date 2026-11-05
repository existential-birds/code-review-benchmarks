# GitHub operations

Use these only after `SKILL.md` has established the target, the evaluation and write
authorization. Run commands one at a time and stop an operation after any failure.
IDs come from checked API responses, never reviewer text.

## Read a conversation

Start from fetch-pr-feedback's raw snapshot and refresh it before posting, paging
threads and comments as
[thread-pagination.md](../../fetch-pr-feedback/thread-pagination.md) describes.

- `gh api --paginate --slurp` returns an array of pages; flatten it with
  `jq 'add // []'`, keeping full records.
- Join REST `id` to GraphQL `databaseId`. The root comment is the one with
  `in_reply_to_id == null`; take reply parents from REST records, never from matching
  file and line.
- Missing, duplicate or conflicting mappings make resolution unavailable; don't guess.

Check every GraphQL response for empty `errors` and the expected non-null `data`, as
well as the exit status. Report a partial response as incomplete, never as an empty
queue.

## Post an inline reply

Make a private `TASK_TMP` with `mktemp -d` and write the reply to `response.md` with a
file-writing tool, or a heredoc with a quoted delimiter. Post it to the root comment's
numeric `ROOT_COMMENT_ID`:

```bash
jq -n --rawfile body "$TASK_TMP/response.md" '{body: $body}' > "$TASK_TMP/reply.json"
gh api --method POST \
  "repos/$OWNER/$REPO/pulls/$PR_NUMBER/comments/$ROOT_COMMENT_ID/replies" \
  --input "$TASK_TMP/reply.json" > "$TASK_TMP/reply-result.json"
jq -e --argjson root "$ROOT_COMMENT_ID" --arg login "$CURRENT_USER" \
  '(.id | type == "number") and (.html_url | type == "string") and
   .in_reply_to_id == $root and .user.login == $login' \
  "$TASK_TMP/reply-result.json"
```

Save the reply ID and URL before resolving. If `gh` or the validation fails, re-read
the conversation, since a timeout can follow a successful write, and retry only if the
reply is absent.

Issue comments and review bodies have no reply endpoint. Post a PR comment that links
the original through `repos/$OWNER/$REPO/issues/$PR_NUMBER/comments`, using the same
body-file pattern, validate its ID and author, and treat resolution as not applicable.

## Resolve an eligible thread

Use a static query with serialized variables. `THREAD_ID` must be the checked mapping
for the evaluated comment, and its reply must already be confirmed.

```bash
jq -n --arg thread "$THREAD_ID" \
  '{query: "mutation($thread: ID!) { resolveReviewThread(input: {threadId: $thread}) { thread { id isResolved } } }",
    variables: {thread: $thread}}' > "$TASK_TMP/resolve.json"
gh api graphql --input "$TASK_TMP/resolve.json" > "$TASK_TMP/resolve-result.json"
jq -e --arg thread "$THREAD_ID" \
  '((.errors // []) | length == 0) and
   .data.resolveReviewThread.thread.id == $thread and
   .data.resolveReviewThread.thread.isResolved == true' \
  "$TASK_TMP/resolve-result.json"
```

Re-read the thread to confirm `isResolved`. If resolution fails, keep the reply as
posted and reconcile before retrying; never post a second reply. Delete only this
run's temporary directory, once its evidence is no longer needed.
