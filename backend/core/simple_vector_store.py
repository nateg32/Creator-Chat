import json
import os
import numpy as np
from typing import List, Dict, Optional, Any
from pydantic import BaseModel

class OutputData(BaseModel):
    id: Optional[str]  # memory id
    score: Optional[float]  # distance
    payload: Optional[Dict]  # metadata

class SimpleJSONVectorStore:
    def __init__(self, path: str):
        self.path = path
        self.vectors = {}  # id -> vector (list)
        self.payloads = {} # id -> payload (dict)
        self.collection_name = "default"
        self._load()

    def _load(self):
        if os.path.exists(self.path):
            with open(self.path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                self.vectors = data.get("vectors", {})
                self.payloads = data.get("payloads", {})

    def _save(self):
        with open(self.path, 'w', encoding='utf-8') as f:
            json.dump({
                "vectors": self.vectors,
                "payloads": self.payloads
            }, f)

    def create_col(self, name, vector_size=None, distance=None):
        self.collection_name = name

    def insert(self, vectors, payloads=None, ids=None):
        for i, id_ in enumerate(ids):
            self.vectors[id_] = vectors[i]
            if payloads and i < len(payloads):
                self.payloads[id_] = payloads[i]
        self._save()

    def search(self, query, vectors, limit=5, filters=None):
        if not vectors:
            return []
        
        query_vec = np.array(vectors[0]) # Assuming single query vector
        results = []

        for id_, vec in self.vectors.items():
            # Apply filters
            if filters:
                payload = self.payloads.get(id_, {})
                if not self._check_filters(payload, filters):
                    continue

            # Cosine similarity
            vec_np = np.array(vec)
            score = np.dot(query_vec, vec_np) / (np.linalg.norm(query_vec) * np.linalg.norm(vec_np) + 1e-9)
            
            results.append(OutputData(
                id=id_,
                score=float(score),
                payload=self.payloads.get(id_, {})
            ))

        # Sort by score desc
        results.sort(key=lambda x: x.score, reverse=True)
        return results[:limit]

    def _check_filters(self, payload, filters):
        for k, v in filters.items():
            if k not in payload:
                return False
            # Simple equality check for now
            if payload[k] != v:
                return False
        return True

    def delete(self, vector_id):
        if vector_id in self.vectors:
            del self.vectors[vector_id]
        if vector_id in self.payloads:
            del self.payloads[vector_id]
        self._save()

    def update(self, vector_id, vector=None, payload=None):
        if vector_id in self.vectors:
            if vector:
                self.vectors[vector_id] = vector
            if payload:
                self.payloads[vector_id] = payload
            self._save()

    def get(self, vector_id):
        if vector_id in self.vectors:
            return OutputData(
                id=vector_id,
                score=1.0,
                payload=self.payloads.get(vector_id, {})
            )
        return None

    def list_cols(self):
        return [self.collection_name]

    def delete_col(self):
        self.vectors = {}
        self.payloads = {}
        self._save()

    def col_info(self):
        return {"count": len(self.vectors)}

    def list(self, filters=None, limit=100):
        results = []
        count = 0
        for id_, payload in self.payloads.items():
            if filters and not self._check_filters(payload, filters):
                continue
            
            results.append(OutputData(
                id=id_,
                score=1.0, # Dummy score
                payload=payload
            ))
            count += 1
            if count >= limit:
                break
        return [results] # Memory expects list of results (sometimes list of lists?)

    def reset(self):
        self.delete_col()
