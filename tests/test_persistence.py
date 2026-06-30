"""End-to-end tests for FirestoreFlowPersistence.

Requires:
- Application Default Credentials configured (gcloud auth application-default login)
- GCP_PROJECT_ID env var (defaults to "gcdeveloper-new")
- agentstate-reducer installed (pip install "crewai_persistence_firestore[reducer]")
"""
import os
import uuid

import pytest

from crewai_persistence_firestore import FirestoreFlowPersistence

GCP_PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "gcdeveloper-new")
COLLECTION = "test_crewai_persistence"


def make_persistence(**kwargs) -> FirestoreFlowPersistence:
    return FirestoreFlowPersistence(
        project_id=GCP_PROJECT_ID,
        collection=COLLECTION,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Test 1: basic save / load round-trip
# ---------------------------------------------------------------------------

def test_save_and_load_state():
    persistence = make_persistence()
    flow_uuid = str(uuid.uuid4())
    state = {
        "user_id": "kamal",
        "step": 3,
        "result": "done",
    }

    persistence.save_state(flow_uuid=flow_uuid, method_name="my_step", state_data=state)
    loaded = persistence.load_state(flow_uuid=flow_uuid)

    assert loaded is not None
    assert loaded["user_id"] == "kamal"
    assert loaded["step"] == 3
    assert loaded["result"] == "done"
    # metadata is stripped on load — must not bleed into Pydantic state rehydration
    assert "_persistence_meta" not in loaded


# ---------------------------------------------------------------------------
# Test 2: reducer caps message list
# ---------------------------------------------------------------------------

def test_reducer_caps_messages():
    from agentstate_reducer import MessageReducer
    from agentstate_reducer.models import ReducerConfig

    config = ReducerConfig(min_messages=4, max_messages=6)
    reducer = MessageReducer(config=config)
    persistence = make_persistence(reducer=reducer, messages_key="messages")

    flow_uuid = str(uuid.uuid4())

    # Build 20 messages alternating human / ai
    messages = []
    for i in range(10):
        messages.append({"role": "human", "content": f"msg {i}"})
        messages.append({"role": "ai", "content": f"reply {i}"})

    state = {"session_id": "test-reducer", "messages": messages}
    persistence.save_state(flow_uuid=flow_uuid, method_name="chat_step", state_data=state)

    loaded = persistence.load_state(flow_uuid=flow_uuid)
    assert loaded is not None
    # min_messages=4 + preserve_first=True allows up to min+1=5 messages
    assert len(loaded["messages"]) <= 5
