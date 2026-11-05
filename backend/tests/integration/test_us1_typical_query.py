"""Integration test for typical user query (T047).

Tests the full chain: request -> MockLLMProvider -> graph -> DB save.
Uses db_session fixture for database operations.
"""
import pytest


class TestUs1TypicalQuery:
    """Integration tests for typical user query workflow."""

    def test_full_query_lifecycle(self, db_session):
        """End-to-end: query -> LLM -> graph -> save result in DB."""
        from glist.llm.mock import MockLLMProvider

        provider = MockLLMProvider()
        # TODO: Implement full graph traversal and result saving
        assert provider is not None