import os, time
from typing import Any, Dict, List, Optional
from pymongo import MongoClient, ASCENDING, DESCENDING
from pymongo.collection import Collection
from bson import ObjectId

MONGODB_URI = os.environ.get("MONGODB_URI", "mongodb://localhost:27017")
MONGODB_DB = os.environ.get("MONGODB_DB", "ratherbe")

_client = MongoClient(MONGODB_URI, socketTimeoutMS=20000, serverSelectionTimeoutMS=20000)
db = _client[MONGODB_DB]

sessions: Collection = db["sessions"]
questions: Collection = db["questions"]
batches: Collection = db["question_batches"]
votes: Collection = db["votes"]

def init_indexes():
    sessions.create_index([("createdAt", DESCENDING)])
    questions.create_index([("sessionId", ASCENDING)])
    questions.create_index([("globalScore", DESCENDING)])
    batches.create_index([("sessionId", ASCENDING), ("createdAt", DESCENDING)])
    votes.create_index([("sessionId", ASCENDING), ("createdAt", DESCENDING)])

def oid(x: str) -> ObjectId:
    return ObjectId(x)

def ins(col: Collection, doc: Dict[str, Any]) -> str:
    res = col.insert_one(doc)
    return str(res.inserted_id)

def get_session(session_id: str) -> Optional[Dict[str, Any]]:
    return sessions.find_one({"_id": oid(session_id)})

def push_deck(session_id: str, question_ids: List[str]):
    sessions.update_one(
        {"_id": oid(session_id)},
        {"$push": {"deck": {"$each": question_ids}}, "$set": {"updatedAt": time.time()}}
    )

def pop_next(session_id: str) -> Optional[str]:
    s = sessions.find_one({"_id": oid(session_id)}, {"deck": 1})
    deck = (s or {}).get("deck") or []
    if not deck: return None
    first = deck[0]
    sessions.update_one({"_id": oid(session_id)}, {"$pop": {"deck": -1}})
    return first

def remaining_count(session_id: str) -> int:
    s = sessions.find_one({"_id": oid(session_id)}, {"deck": 1})
    return len((s or {}).get("deck") or [])

def get_question(qid: str) -> Optional[Dict[str, Any]]:
    return questions.find_one({"_id": oid(qid)})

def upsert_questions(session_id: str, qs: List[Dict[str, Any]]) -> List[str]:
    ids = []
    for q in qs:
        q["sessionId"] = session_id
        q.setdefault("globalScore", 0)
        q["pairHash"] = "|".join(sorted([q["x_norm"], q["y_norm"]]))
        existing = questions.find_one({"sessionId": session_id, "pairHash": q["pairHash"]})
        if existing:
            ids.append(str(existing["_id"]))
            continue
        ids.append(ins(questions, q))
    return ids

def record_vote(v: Dict[str, Any]):
    ins(votes, v)
    if v.get("like"):
        questions.update_one({"_id": oid(v["questionId"])}, {"$inc": {"globalScore": 1}})

def store_batch(session_id: str, candidate_ids: List[str], meta: Dict[str, Any]):
    ins(batches, {"sessionId": session_id, "candidates": candidate_ids, **meta})

init_indexes()
