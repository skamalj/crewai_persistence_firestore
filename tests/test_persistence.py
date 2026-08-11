"""End-to-end tests for FirestoreFlowPersistence (persistence + reducer).

Requires:
- Application Default Credentials configured (gcloud auth application-default login)
- GCP_PROJECT_ID env var (defaults to "gcdeveloper-new")
- agentstate-reducer installed (pip install "crewai_persistence_firestore[reducer]")
"""
import os
import uuid

import pytest
from pydantic import BaseModel

from crewai_persistence_firestore import FirestoreFlowPersistence
from agentstate_reducer import MessageReducer
from agentstate_reducer.models import ReducerConfig

GCP_PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "gcdeveloper-new")
COLLECTION = "test_crewai_persistence"


def make_persistence(**kwargs) -> FirestoreFlowPersistence:
    return FirestoreFlowPersistence(
        project_id=GCP_PROJECT_ID,
        collection=COLLECTION,
        **kwargs,
    )


def build_messages(n_pairs: int) -> list:
    """Return 2*n_pairs alternating human/ai message dicts with deterministic content."""
    messages = []
    for i in range(n_pairs):
        messages.append({"role": "human", "content": f"msg {i}"})
        messages.append({"role": "ai", "content": f"reply {i}"})
    return messages


# ===========================================================================
# PERSISTENCE LAYER (no reducer)
# ===========================================================================

def test_save_and_load_dict_state():
    """A plain-dict state round-trips with all fields intact."""
    persistence = make_persistence()
    flow_uuid = str(uuid.uuid4())
    state = {"user_id": "kamal", "step": 3, "result": "done", "nested": {"a": 1}}

    persistence.save_state(flow_uuid=flow_uuid, method_name="my_step", state_data=state)
    loaded = persistence.load_state(flow_uuid=flow_uuid)

    assert loaded is not None
    assert loaded["user_id"] == "kamal"
    assert loaded["step"] == 3
    assert loaded["result"] == "done"
    assert loaded["nested"] == {"a": 1}


def test_save_and_load_pydantic_state():
    """A Pydantic BaseModel state is serialised via model_dump and round-trips."""
    class MyState(BaseModel):
        id: str
        counter: int
        label: str

    persistence = make_persistence()
    flow_uuid = str(uuid.uuid4())
    state = MyState(id=flow_uuid, counter=7, label="hello")

    persistence.save_state(flow_uuid=flow_uuid, method_name="step", state_data=state)
    loaded = persistence.load_state(flow_uuid=flow_uuid)

    assert loaded is not None
    assert loaded["counter"] == 7
    assert loaded["label"] == "hello"


def test_load_missing_returns_none():
    """Loading an unknown flow_uuid returns None, not an error."""
    persistence = make_persistence()
    assert persistence.load_state(flow_uuid=str(uuid.uuid4())) is None


def test_latest_save_wins():
    """Saving twice for the same flow_uuid returns the most recent state."""
    persistence = make_persistence()
    flow_uuid = str(uuid.uuid4())

    persistence.save_state(flow_uuid=flow_uuid, method_name="s1", state_data={"v": 1})
    persistence.save_state(flow_uuid=flow_uuid, method_name="s2", state_data={"v": 2})

    loaded = persistence.load_state(flow_uuid=flow_uuid)
    assert loaded["v"] == 2


def test_metadata_stripped_on_load():
    """Persistence metadata must not leak into the loaded state."""
    persistence = make_persistence()
    flow_uuid = str(uuid.uuid4())

    persistence.save_state(flow_uuid=flow_uuid, method_name="m", state_data={"x": 1})
    loaded = persistence.load_state(flow_uuid=flow_uuid)

    assert "_persistence_meta" not in loaded


def test_multiple_flows_isolated():
    """Distinct flow_uuids do not overwrite each other."""
    persistence = make_persistence()
    uuid_a, uuid_b = str(uuid.uuid4()), str(uuid.uuid4())

    persistence.save_state(flow_uuid=uuid_a, method_name="m", state_data={"who": "a"})
    persistence.save_state(flow_uuid=uuid_b, method_name="m", state_data={"who": "b"})

    assert persistence.load_state(uuid_a)["who"] == "a"
    assert persistence.load_state(uuid_b)["who"] == "b"


# ===========================================================================
# PERSISTENCE + REDUCER
# ===========================================================================

def test_no_reducer_keeps_all_messages():
    """Without a reducer, the full message list is persisted unchanged."""
    persistence = make_persistence()
    flow_uuid = str(uuid.uuid4())
    messages = build_messages(10)  # 20 messages

    persistence.save_state(flow_uuid=flow_uuid, method_name="chat", state_data={"messages": messages})
    loaded = persistence.load_state(flow_uuid=flow_uuid)

    assert len(loaded["messages"]) == 20
    assert loaded["messages"] == messages


def test_reducer_caps_message_count():
    """With a reducer, the persisted message list is capped to min_messages (+1 for preserve_first)."""
    reducer = MessageReducer(config=ReducerConfig(min_messages=4, max_messages=6))
    persistence = make_persistence(reducer=reducer, messages_key="messages")
    flow_uuid = str(uuid.uuid4())

    persistence.save_state(flow_uuid=flow_uuid, method_name="chat", state_data={"messages": build_messages(10)})
    loaded = persistence.load_state(flow_uuid=flow_uuid)

    # min_messages=4, preserve_first=True allows up to min+1 = 5
    assert len(loaded["messages"]) <= 5


def test_reducer_preserves_recent_content_and_order():
    """The surviving messages must be the most-recent ones, in original order."""
    reducer = MessageReducer(config=ReducerConfig(min_messages=4, max_messages=6))
    persistence = make_persistence(reducer=reducer, messages_key="messages")
    flow_uuid = str(uuid.uuid4())
    messages = build_messages(10)  # last message is {"role": "ai", "content": "reply 9"}

    persistence.save_state(flow_uuid=flow_uuid, method_name="chat", state_data={"messages": messages})
    loaded = persistence.load_state(flow_uuid=flow_uuid)
    surviving = loaded["messages"]

    # preserve_first=True keeps index 0 plus the most-recent tail
    assert surviving[0] == messages[0]                       # first preserved
    assert surviving[-1] == {"role": "ai", "content": "reply 9"}  # most recent survives
    # Everything after the preserved first is a contiguous tail (order preserved)
    tail = surviving[1:]
    assert tail == messages[-len(tail):]


def test_reducer_below_threshold_no_pruning():
    """When message count is at/under max_messages, nothing is pruned."""
    reducer = MessageReducer(config=ReducerConfig(min_messages=4, max_messages=6))
    persistence = make_persistence(reducer=reducer, messages_key="messages")
    flow_uuid = str(uuid.uuid4())
    messages = build_messages(2)  # 4 messages, under max=6

    persistence.save_state(flow_uuid=flow_uuid, method_name="chat", state_data={"messages": messages})
    loaded = persistence.load_state(flow_uuid=flow_uuid)

    assert loaded["messages"] == messages


def test_reducer_custom_messages_key():
    """The reducer targets the configured messages_key, leaving other lists alone."""
    reducer = MessageReducer(config=ReducerConfig(min_messages=4, max_messages=6))
    persistence = make_persistence(reducer=reducer, messages_key="history")
    flow_uuid = str(uuid.uuid4())
    state = {"history": build_messages(10), "other_list": [1, 2, 3]}

    persistence.save_state(flow_uuid=flow_uuid, method_name="chat", state_data=state)
    loaded = persistence.load_state(flow_uuid=flow_uuid)

    assert len(loaded["history"]) <= 5      # pruned
    assert loaded["other_list"] == [1, 2, 3]  # untouched


def test_reducer_no_messages_key_is_safe():
    """A state without the messages_key is persisted without error."""
    reducer = MessageReducer(config=ReducerConfig(min_messages=4, max_messages=6))
    persistence = make_persistence(reducer=reducer, messages_key="messages")
    flow_uuid = str(uuid.uuid4())

    persistence.save_state(flow_uuid=flow_uuid, method_name="step", state_data={"foo": "bar"})
    loaded = persistence.load_state(flow_uuid=flow_uuid)

    assert loaded["foo"] == "bar"
