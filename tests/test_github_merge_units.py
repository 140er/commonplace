"""PR URL parsing for the merge route: plain github.com pull URLs only."""

import pytest

from commonplace.github.merge import pull_request


@pytest.mark.parametrize(
    "pr_url",
    [
        "https://github.com.evil.example/example/project/pull/7",
        "http://github.com/example/project/pull/7",
        "https://github.com/example/project/pull/7/files",
        "https://github.com/example/project/pull/7?x=1",
        "https://github.com/../project/pull/7",
        "https://github.com/example/project/pull/0",
        "https://gitlab.com/example/project/merge_requests/7",
        "https://github.com@evil.example/example/project/pull/7",
        None,
    ],
)
def test_only_plain_github_pr_urls_are_parsed(pr_url):
    assert pull_request(pr_url) is None
    assert pull_request("https://github.com/example/project/pull/7") == ("example", "project", 7)
