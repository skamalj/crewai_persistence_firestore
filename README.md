# crewai-persistence-firestore

Google Firestore persistence backend for [CrewAI Flows](https://docs.crewai.com/concepts/flows). Persists flow state between runs so your flows can resume from any prior step.

**What makes this different:** it has message history pruning built in. Pass a `MessageReducer` and the persistence layer automatically caps your message list before writing to Firestore — no extra code in your flow, no state class changes required. This is the only CrewAI Firestore persistence backend with this capability.

## Features

- **Full flow state persistence** — save and load CrewAI flow state in Google Firestore
- **Built-in message pruning** — optional `MessageReducer` prunes message history at the persistence layer, keeping documents lean without changing your flow code
- **Pydantic and dict state** — works with both `BaseModel`-based and plain-dict flow states
- **Zero schema setup** — Firestore collections are created automatically on first write
- **Single document per flow** — one Firestore document per `flow_uuid`, easy to inspect and query

## Installation

```bash
pip install crewai-persistence-firestore
```

With optional message pruning support:

```bash
pip install "crewai-persistence-firestore[reducer]"
```

**Requires Python 3.10+**

## Firestore Setup

The saver uses Google Cloud Application Default Credentials. Set up auth with one of:

```bash
# Local development — authenticate with your Google account
gcloud auth application-default login

# Service account (CI / production)
export GOOGLE_APPLICATION_CREDENTIALS="/path/to/service-account-key.json"
```

Your Firestore instance must be in **Native mode** (not Datastore mode). Collections are created automatically on first write — no manual setup required.

## Quick Start

### Normal flow (dict or Pydantic state)

```python
from crewai.flow.flow import Flow, listen, start
from crewai.flow.persistence import persist_flow
from crewai_persistence_firestore import FirestoreFlowPersistence

persistence = FirestoreFlowPersistence(
    project_id="my-gcp-project",
    collection="flow_states",
)

@persist_flow(persistence=persistence)
class MyFlow(Flow):
    @start()
    def step_one(self):
        return {"status": "started", "counter": 1}

    @listen(step_one)
    def step_two(self, state):
        return {**state, "counter": state["counter"] + 1}

flow = MyFlow()
result = flow.kickoff()
```

### Conversational flow with message pruning

```python
from crewai.flow.flow import Flow, listen, start
from crewai.flow.persistence import persist_flow
from agentstate_reducer import MessageReducer
from agentstate_reducer.models import ReducerConfig
from crewai_persistence_firestore import FirestoreFlowPersistence

reducer = MessageReducer(config=ReducerConfig(min_messages=10, max_messages=20))

persistence = FirestoreFlowPersistence(
    project_id="my-gcp-project",
    collection="flow_states",
    reducer=reducer,
    messages_key="messages",   # key in state that holds the message list
)

@persist_flow(persistence=persistence)
class ChatFlow(Flow):
    @start()
    def chat(self):
        # messages accumulate here; pruning happens automatically at save time
        ...
```

## API Reference

### `FirestoreFlowPersistence(project_id, collection, reducer, messages_key)`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `project_id` | `str` | required | Google Cloud project ID |
| `collection` | `str` | `"flow_states"` | Firestore collection name |
| `reducer` | `MessageReducer` | `None` | Optional pruner — see [Message Pruning](#built-in-message-pruning) |
| `messages_key` | `str` | `"messages"` | Key in the state dict that holds the message list |

### Methods

| Method | Description |
|---|---|
| `init_db()` | No-op — Firestore needs no schema setup |
| `save_state(flow_uuid, method_name, state_data)` | Persist flow state; applies reducer if configured |
| `load_state(flow_uuid)` | Load state dict for the given flow UUID; returns `None` if not found |

## Built-in Message Pruning

Long-running conversational flows accumulate message history with every turn. Left unchecked this inflates Firestore document size and eventually blows past LLM context limits.

This persistence backend solves that at the storage layer: pass a `MessageReducer` and it automatically prunes the message list inside `save_state()` before writing to Firestore. **Your flow code, state class, and node logic stay untouched.**

### Install with reducer support

```bash
pip install "crewai-persistence-firestore[reducer]"
```

### Usage

```python
from agentstate_reducer import MessageReducer
from agentstate_reducer.models import ReducerConfig
from crewai_persistence_firestore import FirestoreFlowPersistence

reducer = MessageReducer(config=ReducerConfig(min_messages=10, max_messages=20))

persistence = FirestoreFlowPersistence(
    project_id="my-gcp-project",
    reducer=reducer,
    messages_key="messages",
)
```

When `len(messages) > max_messages`, the oldest `human`/`ai` messages are removed until `min_messages` remain. The following are **never** pruned:

- Index 0 (typically the system prompt) — controlled by `preserve_first=True`
- `system` and `function` messages
- `tool` messages — unless their parent `ai` message is pruned (cascade behaviour, configurable)

See [agentstate-reducer on PyPI](https://pypi.org/project/agentstate-reducer/) for full configuration: `preserve_first`, `cascade_tool_messages`, `summarize_fn`, and role alias support (`user`/`assistant`/`agent`).

## Data Model

Each flow run is stored as a single Firestore document:

```
{collection}/
  {flow_uuid}          ← one document per flow run
```

The document contains all state fields plus two metadata fields added by the persistence layer:

| Field | Description |
|---|---|
| `_method_name` | Name of the flow method that triggered the save |
| `_saved_at` | ISO 8601 UTC timestamp of the last save |

Example document:

```json
{
  "user_id": "kamal",
  "messages": [...],
  "step_output": "some result",
  "_method_name": "process_input",
  "_saved_at": "2025-06-30T10:15:00.123456+00:00"
}
```

## License

MIT
