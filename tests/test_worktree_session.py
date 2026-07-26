"""Offline coverage for user-facing managed worktree sessions."""

from __future__ import annotations

import subprocess

import pytest

from glm_acp.worktree_session import (
    WorktreeSessionError,
    create_worktree_session,
    list_worktree_sessions,
    remove_worktree_session,
)


def _run(repo, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


@pytest.fixture
def git_repo(tmp_path):
    _run(tmp_path, "init", "-q")
    _run(tmp_path, "config", "user.email", "tests@example.invalid")
    _run(tmp_path, "config", "user.name", "Tests")
    (tmp_path / "README.md").write_text("seed\n", encoding="utf-8")
    _run(tmp_path, "add", "README.md")
    _run(tmp_path, "commit", "-qm", "seed")
    return tmp_path


def test_create_worktree_session_returns_managed_path(git_repo):
    path = create_worktree_session("HEAD", "feature-a", git_repo)

    assert path == git_repo / ".glm-acp-worktrees" / "feature-a"
    assert (path / ".git").exists()


def test_list_worktree_sessions_parses_managed_entries(git_repo):
    path = create_worktree_session("HEAD", "feature-a", git_repo)

    sessions = list_worktree_sessions(git_repo)

    assert len(sessions) == 1
    assert sessions[0].path == path
    assert sessions[0].name == "feature-a"
    assert sessions[0].detached is True


def test_remove_worktree_session_removes_clean_worktree(git_repo):
    path = create_worktree_session("HEAD", "feature-a", git_repo)

    remove_worktree_session("feature-a", git_repo)

    assert not path.exists()
    assert list_worktree_sessions(git_repo) == []


def test_remove_worktree_session_refuses_dirty_without_force(git_repo):
    path = create_worktree_session("HEAD", "feature-a", git_repo)
    (path / "dirty.txt").write_text("uncommitted\n", encoding="utf-8")

    with pytest.raises(WorktreeSessionError, match="dirty"):
        remove_worktree_session("feature-a", git_repo)

    remove_worktree_session("feature-a", git_repo, force=True)
    assert not path.exists()


def test_worktree_session_reports_missing_git_binary(monkeypatch, tmp_path):
    def missing(*_args, **_kwargs):
        raise FileNotFoundError

    monkeypatch.setattr("glm_acp.worktree_session.subprocess.run", missing)

    with pytest.raises(WorktreeSessionError, match="Git is unavailable"):
        list_worktree_sessions(tmp_path)


def test_create_worktree_session_refuses_name_collision(git_repo):
    create_worktree_session("HEAD", "feature-a", git_repo)

    with pytest.raises(WorktreeSessionError, match="already exists"):
        create_worktree_session("HEAD", "feature-a", git_repo)


def test_create_worktree_session_validates_base_ref_and_name(git_repo):
    with pytest.raises(WorktreeSessionError, match="base ref"):
        create_worktree_session("does-not-exist", "feature-a", git_repo)
    with pytest.raises(WorktreeSessionError, match="Session names"):
        create_worktree_session("HEAD", "../escape", git_repo)


def test_list_worktree_sessions_is_empty_when_none_managed(git_repo):
    assert list_worktree_sessions(git_repo) == []
