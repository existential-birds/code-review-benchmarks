# Review-thread pagination

Page threads and each thread's comments separately, saving every page as its own
file. Repeat each query with `-F cursor="$NEXT_CURSOR"` for later pages.

## Threads

```bash
gh api graphql -F owner="$OWNER" -F repo="$REPO" -F pr="$PR_NUMBER" \
  -f query='
  query($owner: String!, $repo: String!, $pr: Int!, $cursor: String) {
    repository(owner: $owner, name: $repo) {
      pullRequest(number: $pr) {
        id
        headRefOid
        reviewThreads(first: 100, after: $cursor) {
          pageInfo { hasNextPage endCursor }
          nodes {
            id
            isResolved
            isOutdated
            path
            line
            startLine
            diffSide
            startDiffSide
            comments(first: 100) {
              pageInfo { hasNextPage endCursor }
              nodes {
                id
                databaseId
                url
                body
                createdAt
                updatedAt
                author { login }
              }
            }
          }
        }
      }
    }
  }' > "$FEEDBACK_DIR/thread-page-001.json"

jq -e '
  (.errors == null or (.errors | length == 0)) and
  (.data.repository.pullRequest.reviewThreads.nodes | type == "array") and
  (.data.repository.pullRequest.reviewThreads.pageInfo.hasNextPage | type == "boolean")
' "$FEEDBACK_DIR/thread-page-001.json" > /dev/null
```

After each page, read `reviewThreads.pageInfo`. If `hasNextPage` is true, require a
nonempty `endCursor` that differs from the last one, and fetch the next page into a
new file. Merge thread nodes by `id` only after the last page, keeping each thread's
resolution and outdated state.

## Comments within a thread

For each thread whose first `comments` page has `hasNextPage: true`, fetch the rest,
starting from that page's `endCursor`. `THREAD_INDEX` and `PAGE_INDEX` are local
counters for file names.

```bash
gh api graphql -f thread="$THREAD_ID" -F cursor="$NEXT_CURSOR" \
  -f query='
  query($thread: ID!, $cursor: String) {
    node(id: $thread) {
      ... on PullRequestReviewThread {
        id
        comments(first: 100, after: $cursor) {
          pageInfo { hasNextPage endCursor }
          nodes {
            id
            databaseId
            url
            body
            createdAt
            updatedAt
            author { login }
          }
        }
      }
    }
  }' > "$FEEDBACK_DIR/thread-$THREAD_INDEX-comment-page-$PAGE_INDEX.json"

jq -e --arg thread "$THREAD_ID" '
  (.errors == null or (.errors | length == 0)) and
  (.data.node.id == $thread) and
  (.data.node.comments.nodes | type == "array") and
  (.data.node.comments.pageInfo.hasNextPage | type == "boolean")
' "$FEEDBACK_DIR/thread-$THREAD_INDEX-comment-page-$PAGE_INDEX.json" > /dev/null
```

Apply the same cursor checks. Merge comments by node `id`, and map every `databaseId`
to its thread's `id`, `isResolved` and `isOutdated`. Paging is done when every
thread's last comment page reports `hasNextPage: false`. Report null `databaseId`s as
unmatched; never drop a page or assume 100 is enough.
