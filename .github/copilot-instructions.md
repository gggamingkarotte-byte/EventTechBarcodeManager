# GitHub synchronization

After completing a requested code change in this repository:

1. Run the smallest relevant tests and report their result.
2. Review the worktree and stage only files related to the requested change. Never include secrets, local databases, generated installers, build output, or unrelated user changes.
3. Create a descriptive commit for the completed task and push the current branch to `origin`. The default branch is `main`.
4. Never amend commits, force-push, or claim a push succeeded unless the remote confirms it. If authentication or another error blocks the push, explain the blocker and provide the exact recovery command without requesting credentials in chat.
