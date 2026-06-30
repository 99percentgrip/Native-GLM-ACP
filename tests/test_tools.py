"""Tests for glm_acp.tools — sandbox, gitignore, tool execution."""

import asyncio
import os
import pytest
from pathlib import Path

from glm_acp.tools import (
    Sandbox,
    ToolError,
    execute_tool,
    _is_ignored,
    _load_gitignore_patterns,
)


class TestSandbox:
    def test_relative_path_resolves(self, tmp_path):
        sandbox = Sandbox(str(tmp_path))
        resolved = sandbox.resolve("test.py")
        assert resolved == tmp_path / "test.py"

    def test_absolute_path_inside_root(self, tmp_path):
        sandbox = Sandbox(str(tmp_path))
        resolved = sandbox.resolve(str(tmp_path / "src" / "main.py"))
        assert resolved == tmp_path / "src" / "main.py"

    def test_path_outside_root_blocked(self, tmp_path):
        sandbox = Sandbox(str(tmp_path))
        with pytest.raises(ToolError, match="outside the workspace"):
            sandbox.resolve("/etc/passwd")

    def test_additional_dirs_allowed(self, tmp_path):
        other = tmp_path / "other"
        other.mkdir()
        sandbox = Sandbox(str(tmp_path), [str(other)])
        resolved = sandbox.resolve(str(other / "file.txt"))
        assert resolved == other / "file.txt"

    def test_symlink_escape_blocked(self, tmp_path):
        link = tmp_path / "escape"
        link.symlink_to("/etc")
        sandbox = Sandbox(str(tmp_path))
        with pytest.raises(ToolError):
            sandbox.resolve(str(link / "passwd"))


class TestGitignorePatterns:
    def test_direct_match(self):
        assert _is_ignored(".env", [".env"])

    def test_parent_directory_match(self):
        assert _is_ignored(".git/HEAD", [".git"])
        assert _is_ignored(".git/refs/heads/main", [".git"])

    def test_trailing_slash_pattern(self):
        assert _is_ignored(".venv/lib/x.py", [".venv/"])
        assert _is_ignored(".venv", [".venv/"])

    def test_wildcard_pattern(self):
        assert _is_ignored("app.egg-info/PKG-INFO", ["*.egg-info/"])

    def test_non_ignored_path(self):
        assert not _is_ignored("src/main.py", [".git", ".venv/"])
        assert not _is_ignored("README.md", ["*.pyc"])

    def test_node_modules(self):
        assert _is_ignored("node_modules/react/index.js", ["node_modules"])

    def test_load_gitignore(self, tmp_path):
        (tmp_path / ".gitignore").write_text(".venv/\n*.pyc\n# comment\n\nbuild/")
        patterns = _load_gitignore_patterns(tmp_path)
        assert ".venv/" in patterns
        assert "*.pyc" in patterns
        assert "build/" in patterns
        assert "# comment" not in patterns

    def test_load_gitignore_missing(self, tmp_path):
        assert _load_gitignore_patterns(tmp_path) == []


class TestToolExecution:
    @pytest.mark.asyncio
    async def test_read_file(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("hello world")
        sandbox = Sandbox(str(tmp_path))
        result = await execute_tool("read_file", {"path": "test.txt"}, sandbox)
        assert result == "hello world"

    @pytest.mark.asyncio
    async def test_read_file_not_found(self, tmp_path):
        sandbox = Sandbox(str(tmp_path))
        with pytest.raises(ToolError, match="File not found"):
            await execute_tool("read_file", {"path": "nope.txt"}, sandbox)

    @pytest.mark.asyncio
    async def test_write_file(self, tmp_path):
        sandbox = Sandbox(str(tmp_path))
        await execute_tool("write_file", {"path": "out.txt", "content": "data"}, sandbox)
        assert (tmp_path / "out.txt").read_text() == "data"

    @pytest.mark.asyncio
    async def test_edit_file(self, tmp_path):
        f = tmp_path / "code.py"
        f.write_text("old line\nnew line")
        sandbox = Sandbox(str(tmp_path))
        await execute_tool("edit_file", {
            "path": "code.py",
            "old_text": "old line",
            "new_text": "replaced",
        }, sandbox)
        assert f.read_text() == "replaced\nnew line"

    @pytest.mark.asyncio
    async def test_edit_file_not_found_text(self, tmp_path):
        f = tmp_path / "code.py"
        f.write_text("hello")
        sandbox = Sandbox(str(tmp_path))
        with pytest.raises(ToolError, match="old_text not found"):
            await execute_tool("edit_file", {
                "path": "code.py",
                "old_text": "missing",
                "new_text": "x",
            }, sandbox)

    @pytest.mark.asyncio
    async def test_edit_file_ambiguous(self, tmp_path):
        f = tmp_path / "code.py"
        f.write_text("dup\ndup")
        sandbox = Sandbox(str(tmp_path))
        with pytest.raises(ToolError, match="appears 2 times"):
            await execute_tool("edit_file", {
                "path": "code.py",
                "old_text": "dup",
                "new_text": "x",
            }, sandbox)

    @pytest.mark.asyncio
    async def test_list_directory(self, tmp_path):
        (tmp_path / "file.py").write_text("x")
        (tmp_path / "subdir").mkdir()
        sandbox = Sandbox(str(tmp_path))
        result = await execute_tool("list_directory", {"path": "."}, sandbox)
        assert "file.py" in result
        assert "dir subdir" in result

    @pytest.mark.asyncio
    async def test_grep(self, tmp_path):
        (tmp_path / "a.py").write_text("import os\nimport sys")
        (tmp_path / "b.py").write_text("print('hello')")
        sandbox = Sandbox(str(tmp_path))
        result = await execute_tool("grep", {"pattern": "import"}, sandbox)
        assert "a.py" in result
        assert "import os" in result
        assert "b.py" not in result

    @pytest.mark.asyncio
    async def test_search_files(self, tmp_path):
        (tmp_path / "a.py").write_text("x")
        (tmp_path / "b.txt").write_text("x")
        sandbox = Sandbox(str(tmp_path))
        result = await execute_tool("search_files", {"pattern": "*.py"}, sandbox)
        assert "a.py" in result
        assert "b.txt" not in result

    @pytest.mark.asyncio
    async def test_unknown_tool(self, tmp_path):
        sandbox = Sandbox(str(tmp_path))
        with pytest.raises(ToolError, match="Unknown tool"):
            await execute_tool("nonexistent", {}, sandbox)
