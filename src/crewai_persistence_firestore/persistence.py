from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional, Union

from pydantic import BaseModel, ConfigDict, PrivateAttr, Field
import google.cloud.firestore

from crewai.flow.persistence.base import FlowPersistence


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
        if isinstance(state_data, BaseModel):
            d: Dict[str, Any] = state_data.model_dump()
        else:
            d = dict(state_data)

        if self.reducer is not None and self.messages_key in d:
            result = self.reducer.reduce(existing=d[self.messages_key], new=[])
            d[self.messages_key] = result.surviving

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
