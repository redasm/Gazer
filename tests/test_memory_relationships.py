"""Tests for memory.relationships -- RelationshipGraph."""

import os
import pytest
from memory.relationships import RelationshipGraph, Person


@pytest.fixture
def graph(tmp_dir):
    path = str(tmp_dir / "RELATIONSHIPS.md")
    return RelationshipGraph(storage_path=path)


class TestPerson:
    def test_defaults(self):
        p = Person(name="Alice")
        assert p.relationship == "unknown"
        assert p.mention_count == 1
        assert p.sentiment == 0.0

    def test_update_mention(self):
        p = Person(name="Bob")
        p.update_mention("We had dinner", sentiment_delta=0.5)
        assert p.mention_count == 2
        assert len(p.context_snippets) == 1
        assert p.sentiment != 0.0

    def test_context_limit(self):
        p = Person(name="Carol")
        for i in range(10):
            p.update_mention(f"context {i}")
        assert len(p.context_snippets) == 5  # Max 5


class TestRelationshipGraph:
    def test_add_person(self, graph):
        person = graph.add_or_update_person("小红", relationship="friend", context="老朋友")
        assert person.name == "小红"
        assert person.relationship == "friend"
        assert "小红" in graph.people

    def test_update_existing_person(self, graph):
        graph.add_or_update_person("小红", relationship="friend")
        graph.add_or_update_person("小红", context="went shopping")
        assert graph.people["小红"].mention_count == 2

    def test_get_person(self, graph):
        graph.add_or_update_person("Alice")
        assert graph.get_person("Alice") is not None
        assert graph.get_person("Nobody") is None

    def test_find_by_alias(self, graph):
        graph.add_or_update_person("小红", alias="闺蜜")
        found = graph.find_by_alias("闺蜜")
        assert found is not None
        assert found.name == "小红"

    def test_find_by_name_as_alias(self, graph):
        graph.add_or_update_person("Alice")
        found = graph.find_by_alias("Alice")
        assert found is not None

    def test_extract_known_people(self, graph):
        graph.add_or_update_person("小红")
        graph.add_or_update_person("小明")
        extracted = graph.extract_people_from_text("今天和小红一起吃饭了")
        names = [p["name"] for p in extracted]
        assert "小红" in names

    def test_to_context_empty(self, graph):
        assert graph.to_context() == ""

    def test_to_context_with_people(self, graph):
        graph.add_or_update_person("Alice", relationship="friend")
        graph.add_or_update_person("Bob", relationship="colleague")
        ctx = graph.to_context()
        assert "Alice" in ctx
        assert "Bob" in ctx

    def test_persistence(self, tmp_dir):
        path = str(tmp_dir / "REL.md")
        g1 = RelationshipGraph(storage_path=path)
        g1.add_or_update_person("Alice", relationship="friend")

        # Reload
        g2 = RelationshipGraph(storage_path=path)
        assert "Alice" in g2.people
        assert g2.people["Alice"].relationship == "friend"

    def test_update_from_message(self, graph):
        graph.add_or_update_person("小红")
        graph.update_from_message("今天小红来找我玩", sentiment=0.5)
        assert graph.people["小红"].mention_count >= 2
