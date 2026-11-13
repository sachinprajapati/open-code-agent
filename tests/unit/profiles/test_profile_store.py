"""Focused persistence tests for durable secret-safe provider profiles."""

import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from open_code_agent.persistence import (
    DuplicateProfileError,
    ProfileStoreError,
    ProviderProfileStore,
)
from open_code_agent.providers import OpenAICompatibleProfile


def profile(profile_id: str = "local") -> OpenAICompatibleProfile:
    return OpenAICompatibleProfile(
        profile_id=profile_id,
        display_name=profile_id,
        base_url="http://localhost:1234/v1",
        default_model="test-model",
        api_key_secret_ref="env:TEST_PROVIDER_KEY",
    )


def test_empty_add_default_remove_and_deterministic_output(tmp_path: Path) -> None:
    path = tmp_path / "new" / "profiles.json"
    store = ProviderProfileStore(path)
    assert store.list() == ()
    assert not path.parent.exists()

    store.add(profile("z"))
    store.add(profile("a"))
    store.set_default("a")
    assert [item.profile_id for item in store.list()] == ["a", "z"]
    assert store.default_profile_id() == "a"
    serialized = path.read_text()
    assert "TEST_PROVIDER_KEY" in serialized
    assert "secret-value" not in serialized
    assert json.loads(serialized)["version"] == 1

    store.remove("a")
    assert store.default_profile_id() is None
    assert path.read_text() == path.read_text()
    if os.name != "nt":
        assert path.stat().st_mode & 0o077 == 0


def test_duplicate_invalid_and_symlinked_stores_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "profiles.json"
    store = ProviderProfileStore(path)
    store.add(profile())
    with pytest.raises(DuplicateProfileError):
        store.add(profile())

    path.write_text("{")
    with pytest.raises(ProfileStoreError, match="malformed"):
        store.list()

    target = tmp_path / "target.json"
    target.write_text("{}")
    link = tmp_path / "link.json"
    link.symlink_to(target)
    with pytest.raises(ProfileStoreError, match="symlink"):
        ProviderProfileStore(link).list()


def test_explicit_update_and_threaded_same_store_operations(tmp_path: Path) -> None:
    store = ProviderProfileStore(tmp_path / "profiles.json")
    store.add(profile("first"))
    updated = profile("first").model_copy(update={"display_name": "Updated"})
    store.update(updated)
    assert store.get("first").display_name == "Updated"

    def add(index: int) -> None:
        store.add(profile(f"thread-{index}"))

    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(add, range(8)))
    assert len(store.list()) == 9
