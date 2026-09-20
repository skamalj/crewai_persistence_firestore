from __future__ import annotations

from datetime import date, datetime, timezone
import json
from decimal import Decimal
from typing import Any, Dict, Optional, Union

from pydantic import BaseModel, ConfigDict, PrivateAttr, Field
import google.cloud.firestore

from crewai.flow.persistence.base import FlowPersistence



def _memory_namespace(reducer: Any, state: Dict[str, Any], flow_uuid: str) -> str:
    """Resolve the long-term-memory namespace forwarded to reducer ``on_prune`` hooks.

    Looks up ``reducer.config.namespace_key`` (default ``"memory_namespace"``) in
    the flow state; the app sets it in its state model, e.g.
    ``memory_namespace = "/user/kamal"`` (a CrewAI Memory scope path). Falls back
    to ``"/flow/<flow_uuid>"`` so apps that never set it still get per-flow
    memory. The persistence layer never builds the namespace beyond that fallback.
    """
    key = getattr(getattr(reducer, "config", None), "namespace_key", "memory_namespace")
    ns = state.get(key)
    return ns if ns is not None else f"/flow/{flow_uuid}"


def _apply_reducer(reducer: Any, state: Dict[str, Any], messages_key: str, flow_uuid: str) -> None:
    """Prune ``state[messages_key]`` in place, forwarding the memory namespace.

    agentstate-reducer >= 0.4.0 accepts ``namespace=``; older reducers ignore it.
    """
    try:
        result = reducer.reduce(
            existing=state[messages_key], new=[], namespace=_memory_namespace(reducer, state, flow_uuid)
        )
    except TypeError:  # agentstate-reducer < 0.4.0
        result = reducer.reduce(existing=state[messages_key], new=[])
    state[messages_key] = result.surviving


def _json_default(value: Any) -> Any:
    """Make non-JSON state values serialisable (mirrors CrewAI's SQLite persistence, 1.15.22+)."""
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, (set, frozenset, tuple)):
        return list(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _jsonable_state(state_data: Union[Dict[str, Any], BaseModel]) -> Dict[str, Any]:
    """Dump flow state to a JSON-safe dict (datetime, set, tuple, nested BaseModel, Decimal...)."""
    if isinstance(state_data, BaseModel):
        d: Dict[str, Any] = state_data.model_dump(mode="json")
    else:
        d = dict(state_data)
    return json.loads(json.dumps(d, default=_json_default))

class FirestoreFlowPersistence(FlowPersistence):
    """Google Firestore persistence backend for CrewAI Flows.

    Stores one document per flow_uuid in a Firestore collection.
    Optionally applies a MessageReducer to prune message history before each save.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    persistence_type: str = Field(default="firestore")
    project_id: str
    collection_name: str = "flow_states"
    reducer: Optional[Any] = None
    messages_key: str = "messages"

    _client: Any = PrivateAttr()
    _collection: Any = PrivateAttr()

    def model_post_init(self, __context: Any) -> None:
        self._client = google.cloud.firestore.Client(project=self.project_id)
        self._collection = self._client.collection(self.collection_name)
        self.init_db()

    def init_db(self) -> None:
        """No-op — Firestore needs no schema setup."""
        pass

    def save_state(
        self,
        flow_uuid: str,
        method_name: str,
        state_data: Union[Dict[str, Any], BaseModel],
    ) -> None:
        d = _jsonable_state(state_data)

        if self.reducer is not None and self.messages_key in d:
            _apply_reducer(self.reducer, d, self.messages_key, flow_uuid)

        d["_persistence_meta"] = {
            "method_name": method_name,
            "saved_at": datetime.now(timezone.utc).isoformat(),
        }

        self._collection.document(flow_uuid).set(d)

    def load_state(self, flow_uuid: str) -> Optional[Dict[str, Any]]:
        doc = self._collection.document(flow_uuid).get()
        if not doc.exists:
            return None
        d = doc.to_dict()
        d.pop("_persistence_meta", None)
        return d
