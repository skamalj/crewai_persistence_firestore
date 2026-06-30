from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional, Union

from pydantic import BaseModel

from crewai.flow.persistence.base import FlowPersistence
import google.cloud.firestore


class FirestoreFlowPersistence(FlowPersistence):
    """Google Firestore persistence backend for CrewAI Flows.

    Stores one document per flow_uuid in a Firestore collection.
    Optionally applies a MessageReducer to prune message history before each save.
    """

    def __init__(
        self,
        project_id: str,
        collection: str = "flow_states",
        reducer=None,
        messages_key: str = "messages",
    ) -> None:
        self.project_id = project_id
        self.collection_name = collection
        self.reducer = reducer
        self.messages_key = messages_key

        self._client = google.cloud.firestore.Client(project=project_id)
        self._collection = self._client.collection(collection)
        self.init_db()

    # ------------------------------------------------------------------
    # FlowPersistence interface
    # ------------------------------------------------------------------

    def init_db(self) -> None:
        """No-op — Firestore needs no schema setup."""
        pass

    def save_state(
        self,
        flow_uuid: str,
        method_name: str,
        state_data: Union[Dict[str, Any], BaseModel],
    ) -> None:
        """Persist flow state to Firestore.

        Converts state_data to a plain dict, optionally prunes the message list
        with the configured reducer, then writes the document.
        """
        if isinstance(state_data, BaseModel):
            d: Dict[str, Any] = state_data.model_dump()
        else:
            d = dict(state_data)

        # Apply reducer if configured and message key exists
        if self.reducer is not None and self.messages_key in d:
            result = self.reducer.reduce(existing=d[self.messages_key], new=[])
            d[self.messages_key] = result.surviving

        d["_method_name"] = method_name
        d["_saved_at"] = datetime.now(timezone.utc).isoformat()

        self._collection.document(flow_uuid).set(d)

    def load_state(self, flow_uuid: str) -> Optional[Dict[str, Any]]:
        """Load the most recent flow state from Firestore.

        Returns the document as a dict, or None if no state has been saved yet.
        """
        doc = self._collection.document(flow_uuid).get()
        return doc.to_dict() if doc.exists else None
