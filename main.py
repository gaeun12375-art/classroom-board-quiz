"""Classroom board-game quiz. Run: uvicorn main:app --reload"""
from __future__ import annotations

import asyncio
import copy
import io
import json
import os
import random
import re
import secrets
import sqlite3
import time
import unicodedata
from collections import defaultdict
from contextlib import closing
from pathlib import Path
from typing import Any

import qrcode
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

ROOT = Path(__file__).parent
DB_FILE = Path(os.environ.get("GAME_DB_PATH", str(ROOT / "data" / "rooms.sqlite3")))
DB_FILE.parent.mkdir(parents=True, exist_ok=True)
lock = asyncio.Lock()
listeners: dict[str, set[asyncio.Queue]] = defaultdict(set)
SOUND_KEYS = ("move", "special", "portal", "reset", "swap")


def connect():
    conn = sqlite3.connect(DB_FILE, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE IF NOT EXISTS rooms (code TEXT PRIMARY KEY, admin_token TEXT NOT NULL, state TEXT NOT NULL, updated_at INTEGER NOT NULL)")
    return conn


with closing(connect()) as db:
    db.execute("PRAGMA journal_mode=WAL")


app = FastAPI(title="한 칸의 발견 | 형성평가 말판놀이")
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


class Payload(BaseModel):
    data: dict[str, Any] = {}


def err(message: str, code: int = 400):
    raise HTTPException(status_code=code, detail=message)


def get_room(code: str) -> dict:
    with closing(connect()) as db:
        row = db.execute("SELECT state FROM rooms WHERE code=?", (code.upper(),)).fetchone()
    if not row:
        err("게임방을 찾을 수 없습니다.", 404)
    return json.loads(row["state"])


def save_room(room):
    with closing(connect()) as db:
        db.execute("UPDATE rooms SET state=?, updated_at=? WHERE code=?", (json.dumps(room, ensure_ascii=False), int(time.time()), room["code"]))
        db.commit()
    for q in tuple(listeners[room["code"]]):
        try:
            if q.full():
                q.get_nowait()
            q.put_nowait("updated")
        except (asyncio.QueueFull, asyncio.QueueEmpty):
            pass


def build_tiles(goal: int) -> dict:
    """Goal is the final index. Reserve 4 distinct non-finish spaces."""
    ratios = (("portal_a", .21), ("reset", .38), ("swap", .59), ("portal_b", .78))
    used = set()
    tiles = {}
    for name, ratio in ratios:
        p = min(goal - 1, max(1, int(goal * ratio + 0.5)))
        while p in used:
            p = p + 1 if p < goal - 1 else 1
        tiles[str(p)] = name
        used.add(p)
    return tiles


def valid_questions(raw, n: int):
    if not isinstance(raw, list) or len(raw) != n:
        err(f"기본 문제를 정확히 {n}개 작성해 주세요.")
    clean = []
    for i, q in enumerate(raw, 1):
        if not isinstance(q, dict):
            err(f"{i}번 문제 형식이 올바르지 않습니다.")
        question = str(q.get("text", "")).strip()
        hint = str(q.get("hint", "")).strip()
        kind = str(q.get("type", "text"))
        answer = str(q.get("answer", "")).strip()
        if len(question) < 2 or len(question) > 500 or not answer or len(answer) > 300:
            err(f"{i}번 문제의 질문과 정답을 확인해 주세요.")
        if len(hint) > 300:
            err(f"{i}번 문제의 힌트가 너무 깁니다.")
        if kind not in ("text", "choice"):
            err(f"{i}번 문제의 유형이 잘못되었습니다.")
        options = [str(x).strip() for x in (q.get("options", []) if isinstance(q.get("options", []), list) else [])]
        if kind == "choice":
            if len(options) != 4 or not all(options) or len(set(options)) != 4:
                err(f"{i}번 객관식은 서로 다른 보기 4개가 필요합니다.")
            if answer not in ("1", "2", "3", "4"):
                err(f"{i}번 객관식 정답은 1~4번 중 하나여야 합니다.")
        else:
            options = []
        clean.append({"text": question, "hint": hint, "type": kind, "answer": answer, "options": options})
    return clean


def validate_config(raw):
    count = raw.get("questionCount", 10)
    teams = raw.get("teamCount", 3)
    retries = raw.get("retries", 1)
    if type(count) is not int or not 5 <= count <= 48:
        err("문제 수는 5~48개로 설정해 주세요.")
    if type(teams) is not int or not 2 <= teams <= 8:
        err("팀은 2~8개로 설정해 주세요.")
    if type(retries) is not int or not 0 <= retries <= 3:
        err("오답 재입력 기회는 0~3회입니다.")
    questions = valid_questions(raw.get("questions", []), count)
    goal = count + 7
    tiles = build_tiles(goal)
    specials = []
    used = set()
    for i, q in enumerate(raw.get("specials", [])):
        try:
            sq = int(q.get("square"))
        except (ValueError, TypeError):
            err(f"특별 카드 {i+1}의 칸 번호를 확인해 주세요.")
        if sq < 1 or sq >= goal or str(sq) in tiles or sq in used:
            err(f"특별 카드 {i+1}의 칸({sq})이 범위 밖이거나 다른 이벤트 칸과 겹칩니다.")
        text = str(q.get("text", "")).strip()
        answer = str(q.get("answer", "")).strip()
        if not text or len(text) > 500 or len(answer) > 300:
            err(f"특별 카드 {i+1}의 문제를 입력해 주세요.")
        specials.append({"square": sq, "text": text, "answer": answer})
        used.add(sq)
    if len(specials) > 12:
        err("특별 카드는 최대 12장입니다.")
    names = [str(x).strip() for x in raw.get("teamNames", [])]
    if len(names) != teams:
        names = [f"{i+1}팀" for i in range(teams)]
    if any(not x or len(x) > 15 for x in names) or len(set(names)) != len(names):
        err("팀 이름은 서로 다르게, 15자 이내로 작성해 주세요.")
    sounds = raw.get("sounds", {})
    return {"questionCount": count, "teamCount": teams, "retries": retries,
            "questions": questions, "specials": specials, "tiles": tiles, "goal": goal,
            "teamNames": names,
            "sounds": {"enabled": {k: bool(sounds.get("enabled", {}).get(k, True)) for k in SOUND_KEYS},
                       "volume": min(1., max(0., float(sounds.get("volume", .4))))}}


def append_log(room, message):
    room["history"].insert(0, {"time": int(time.time()), "text": message})
    room["history"] = room["history"][:35]


def add_effect(room, kind, message):
    room["effectSeq"] += 1
    room["effects"].append({"seq": room["effectSeq"], "type": kind, "message": message})
    room["effects"] = room["effects"][-12:]


def actor_ok(room, teacher_key, team_id, team_pin, current_only=True):
    if teacher_key and secrets.compare_digest(str(teacher_key), room["adminToken"]):
        return True
    if not isinstance(team_id, int) or not 0 <= team_id < len(room["teams"]):
        err("팀 정보를 확인해 주세요.", 403)
    team = room["teams"][team_id]
    if not team_pin or not secrets.compare_digest(str(team_pin), team["pin"]):
        err("팀 참가 코드가 올바르지 않습니다.", 403)
    if current_only and room["turnIndex"] != team_id:
        err("지금은 다른 팀의 차례입니다.", 403)
    return False


def teacher_ok(room, key):
    if not key or not secrets.compare_digest(str(key), room["adminToken"]):
        err("교사 인증이 필요합니다.", 403)


def public_state(room, teacher=False):
    result = {k: copy.deepcopy(room[k]) for k in (
        "code", "status", "goal", "tiles", "turnIndex", "teams", "phase", "dice", "current", "history", "effects", "effectSeq", "winner", "turnNumber", "message")}
    result["teams"] = [{k: v for k, v in t.items() if teacher or k != "pin"} for t in result["teams"]]
    result["config"] = {k: copy.deepcopy(room["config"][k]) for k in ("questionCount", "teamCount", "retries", "sounds")}
    result["specials"] = [{"square": s["square"]} for s in room["config"]["specials"]]
    # Never publish correct answers (including special card answers) to student endpoints.
    if result["current"] and not teacher:
        result["current"].pop("answer", None)
        result["current"].pop("optionsAnswer", None)
        if not result["current"].get("hintRevealed", False):
            result["current"].pop("hint", None)
    if teacher:
        result["config"]["teamNames"] = room["config"]["teamNames"]
        result["adminSpecials"] = room["config"]["specials"]
    return result


def advance(room):
    room["turnIndex"] = (room["turnIndex"] + 1) % len(room["teams"])
    room["turnNumber"] += 1
    room["phase"] = "roll"
    room["current"] = None
    room["dice"] = None
    room["message"] = f"{room['teams'][room['turnIndex']]['name']} 차례입니다."


def finish_landing(room, team):
    if team["position"] >= room["goal"]:
        room["status"] = "finished"
        room["winner"] = room["turnIndex"]
        room["phase"] = "finished"
        room["current"] = None
        room["message"] = f"🏆 {team['name']}이(가) 결승점에 도착했습니다!"
        append_log(room, room["message"])
    else:
        advance(room)


def new_question(room, origin):
    # Shuffle through every entered quiz before repeating it.
    if not room["questionBag"]:
        room["questionBag"] = list(range(len(room["config"]["questions"])))
        random.shuffle(room["questionBag"])
    n = room["questionBag"].pop()
    q = room["config"]["questions"][n]
    room["current"] = {"kind": "normal", "id": n, "text": q["text"], "hint": q["hint"],
                       "type": q["type"], "options": q["options"], "answer": q["answer"],
                       "attempts": 0, "hintRevealed": False, "origin": origin}
    room["phase"] = "question"
    room["message"] = "문제를 풀어 이동을 확정하세요."


def normalize_answer(s):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(s))).casefold()


@app.get("/")
async def home():
    return FileResponse(ROOT / "static" / "index.html")


@app.get("/health")
async def health():
    return {"ok": True}


def provision_room(config: dict, *, log_creation: bool = True, blocked_pins: set[str] | None = None) -> dict:
    """Create an independent classroom room with fresh teacher/team credentials.

    Called under the application lock; keeps configuration and progress separate.
    """
    for _ in range(20):
        code = "".join(secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(6))
        with closing(connect()) as db:
            if not db.execute("SELECT 1 FROM rooms WHERE code=?", (code,)).fetchone():
                break
    else:
        err("게임방 코드를 발급하지 못했습니다.", 500)

    token = secrets.token_urlsafe(30)
    pins: set[str] = set(blocked_pins or ())
    teams = []
    for name in config["teamNames"]:
        pin = f"{secrets.randbelow(1000000):06d}"
        while pin in pins:
            pin = f"{secrets.randbelow(1000000):06d}"
        pins.add(pin)
        teams.append({"name": name, "position": 0, "hintsUsed": 0, "pin": pin})

    room = {"code": code, "adminToken": token, "status": "waiting", "goal": config["goal"],
            "config": copy.deepcopy(config), "tiles": copy.deepcopy(config["tiles"]), "teams": teams,
            "turnIndex": 0, "turnNumber": 1, "phase": "waiting", "current": None,
            "dice": None, "history": [], "effects": [], "effectSeq": 0,
            "questionBag": [], "winner": None, "message": "교사가 게임을 시작하면 진행됩니다."}
    if log_creation:
        append_log(room, "게임방이 생성되었습니다.")
    with closing(connect()) as db:
        db.execute("INSERT INTO rooms VALUES (?, ?, ?, ?)",
                   (code, token, json.dumps(room, ensure_ascii=False), int(time.time())))
        db.commit()
    return {"code": code, "teacherKey": token, "state": public_state(room, True)}


@app.post("/api/rooms")
async def create_room(payload: Payload):
    config = validate_config(payload.data)
    async with lock:
        return provision_room(config)


@app.post("/api/rooms/{code}/clone")
async def clone_room(code: str, x_teacher_key: str | None = Header(None)):
    """Reuse questions, hints, rules and sounds for another class.

    The previous class's progress stays in its own room. New code, new PINs
    and a fresh teacher key prevent old students from controlling the new game.
    """
    async with lock:
        original = get_room(code)
        teacher_ok(original, x_teacher_key)
        return provision_room(
            original["config"], log_creation=False,
            blocked_pins={team["pin"] for team in original["teams"]},
        )


@app.get("/api/rooms/{code}")
async def room_info(code: str, x_teacher_key: str | None = Header(None)):
    room = get_room(code)
    teacher = bool(x_teacher_key and secrets.compare_digest(str(x_teacher_key), room["adminToken"]))
    return public_state(room, teacher)


@app.post("/api/rooms/{code}/join")
async def join_room(code: str, payload: Payload):
    room = get_room(code)
    try:
        team_id = int(payload.data.get("teamId", -1))
    except (TypeError, ValueError):
        err("팀을 선택해 주세요.")
    pin = str(payload.data.get("pin", ""))
    actor_ok(room, None, team_id, pin, current_only=False)
    return {"ok": True, "teamId": team_id, "teamName": room["teams"][team_id]["name"]}


@app.post("/api/rooms/{code}/action")
async def action(code: str, payload: Payload, x_teacher_key: str | None = Header(None)):
    a = payload.data.get("action")
    async with lock:
        room = get_room(code)
        team_id = payload.data.get("teamId", -1)
        team_pin = payload.data.get("pin", "")
        if a in ("start", "skip", "special_correct", "special_wrong", "sound", "restart"):
            teacher_ok(room, x_teacher_key)
        else:
            # A valid teacher may act on behalf of the active team.
            actor_ok(room, x_teacher_key, team_id, team_pin)
        if a == "start":
            if room["status"] != "waiting":
                err("이미 시작한 게임입니다.")
            room["status"] = "playing"
            room["phase"] = "roll"
            room["message"] = f"{room['teams'][0]['name']} 차례입니다. 주사위를 던져 주세요."
            append_log(room, "게임이 시작되었습니다.")
        elif a == "roll":
            if room["status"] != "playing" or room["phase"] != "roll":
                err("지금은 주사위를 던질 수 없습니다.")
            team = room["teams"][room["turnIndex"]]
            origin = team["position"]
            d1, d2 = random.randint(1, 6), random.randint(1, 6)
            op = random.choice(["+", "−"])
            steps = d1 + d2 if op == "+" else abs(d1 - d2)
            landing = min(room["goal"], origin + steps)
            team["position"] = landing
            room["dice"] = {"a": d1, "b": d2, "op": op, "steps": steps}
            append_log(room, f"{team['name']}: {d1} {op} {d2} = {steps}, {origin}→{landing}칸")
            add_effect(room, "move", f"{team['name']}이(가) {steps}칸 이동했어요!")
            tile = room["tiles"].get(str(landing)) if steps > 0 else None
            special = next((s for s in room["config"]["specials"] if s["square"] == landing), None) if steps > 0 else None
            if special:
                room["current"] = {"kind": "special", "text": special["text"], "answer": special["answer"],
                                   "square": landing, "origin": origin}
                room["phase"] = "special"
                room["message"] = "특별 퀴즈! 구두로 답하고 교사의 판정을 받으세요."
                add_effect(room, "special", f"{landing}칸 특별 퀴즈 카드!")
            elif tile == "reset":
                team["position"] = 0
                add_effect(room, "reset", f"{team['name']}, 출발점으로 돌아갑니다!")
                append_log(room, f"{team['name']}이(가) 처음으로 돌아갔습니다.")
                advance(room)
            elif tile == "swap":
                candidates = [i for i in range(len(room["teams"])) if i != room["turnIndex"]]
                leader = max(candidates, key=lambda i: room["teams"][i]["position"])
                if room["teams"][leader]["position"] > team["position"]:
                    other = room["teams"][leader]
                    team["position"], other["position"] = other["position"], team["position"]
                    msg = f"{team['name']}과 선두 {other['name']}의 위치가 바뀌었어요!"
                else:
                    msg = f"{team['name']}은(는) 선두라서 위치를 유지해요."
                add_effect(room, "swap", msg)
                append_log(room, msg)
                if team["position"] >= room["goal"]:
                    finish_landing(room, team)
                else:
                    advance(room)
            elif tile in ("portal_a", "portal_b"):
                target_type = "portal_b" if tile == "portal_a" else "portal_a"
                destination = int(next(p for p, t in room["tiles"].items() if t == target_type))
                team["position"] = destination
                add_effect(room, "portal", f"포털 이동! {landing}칸 → {destination}칸")
                append_log(room, f"{team['name']}이(가) 포털로 {destination}칸에 이동했습니다.")
                advance(room)
            else:
                new_question(room, origin)
        elif a == "hint":
            if room["status"] != "playing" or room["phase"] != "question":
                err("지금은 힌트를 사용할 수 없습니다.")
            team = room["teams"][room["turnIndex"]]
            question = room["current"]
            if not question.get("hint"):
                err("이 문제에는 힌트가 없습니다.")
            if question["hintRevealed"]:
                err("이번 문제에는 이미 힌트를 사용했습니다.")
            if team["hintsUsed"] >= 5:
                err("팀당 힌트 5회를 모두 사용했습니다.")
            team["hintsUsed"] += 1
            question["hintRevealed"] = True
            room["message"] = f"힌트 공개! {team['name']}의 남은 힌트: {5-team['hintsUsed']}회"
            append_log(room, f"{team['name']}이(가) 힌트를 사용했습니다.")
        elif a == "answer":
            if room["status"] != "playing" or room["phase"] != "question":
                err("지금은 답안을 제출할 수 없습니다.")
            q = room["current"]
            incoming = normalize_answer(payload.data.get("answer", ""))
            if not incoming:
                err("답을 입력해 주세요.")
            right = any(incoming == normalize_answer(ans) for ans in q["answer"].split("|") if ans.strip())
            team = room["teams"][room["turnIndex"]]
            if right:
                room["message"] = f"정답! {team['name']}의 이동이 확정되었습니다."
                append_log(room, f"{team['name']} 정답! {team['position']}칸에 안착했습니다.")
                finish_landing(room, team)
            else:
                q["attempts"] += 1
                if q["attempts"] > room["config"]["retries"]:
                    previous = q["origin"]
                    team["position"] = previous
                    append_log(room, f"{team['name']} 오답. {previous}칸으로 돌아갔습니다.")
                    room["message"] = "아쉽지만 오답입니다. 이동 전 위치로 돌아갑니다."
                    advance(room)
                else:
                    remaining = 1 + room["config"]["retries"] - q["attempts"]
                    room["message"] = f"오답입니다. 남은 답안 제출 기회: {remaining}회"
        elif a in ("special_correct", "special_wrong"):
            if room["status"] != "playing" or room["phase"] != "special":
                err("판정할 특별 퀴즈가 없습니다.")
            team = room["teams"][room["turnIndex"]]
            q = room["current"]
            if a == "special_correct":
                append_log(room, f"{team['name']} 특별 퀴즈 정답! {team['position']}칸 유지")
                finish_landing(room, team)
            else:
                team["position"] = q["origin"]
                append_log(room, f"{team['name']} 특별 퀴즈 오답. {q['origin']}칸으로 복귀")
                advance(room)
        elif a == "skip":
            if room["status"] != "playing":
                err("진행 중인 게임이 아닙니다.")
            team = room["teams"][room["turnIndex"]]
            if room["current"]:
                team["position"] = room["current"]["origin"]
            append_log(room, f"교사가 {team['name']} 차례를 건너뛰었습니다.")
            advance(room)
        elif a == "sound":
            sounds = payload.data.get("sounds", {})
            if not isinstance(sounds, dict):
                err("효과음 설정을 확인해 주세요.")
            try:
                volume = float(sounds.get("volume", .4))
            except (TypeError, ValueError):
                err("음량을 확인해 주세요.")
            room["config"]["sounds"] = {
                "enabled": {k: bool(sounds.get("enabled", {}).get(k, True)) for k in SOUND_KEYS},
                "volume": min(1., max(0., volume))}
        elif a == "restart":
            # Same room and PINs, but erase *all* classroom progress.
            for team in room["teams"]:
                team["position"] = 0
                team["hintsUsed"] = 0
            room.update(status="waiting", turnIndex=0, turnNumber=1, phase="waiting",
                        current=None, dice=None, winner=None, questionBag=[],
                        history=[], effects=[],
                        message="모든 게임 기록이 초기화되었습니다. 새 게임을 시작할 수 있습니다.")
            # Preserve effectSeq: connected students must not confuse new effects
            # with already-played ones after an in-place reset.
        else:
            err("지원하지 않는 동작입니다.")
        save_room(room)
    return {"ok": True, "state": public_state(room, bool(x_teacher_key and secrets.compare_digest(str(x_teacher_key), room["adminToken"])))}


@app.get("/api/rooms/{code}/events")
async def events(code: str, request: Request):
    get_room(code)
    q: asyncio.Queue = asyncio.Queue(maxsize=1)
    listeners[code.upper()].add(q)

    async def stream():
        try:
            yield "event: connected\ndata: {}\n\n"
            while True:
                if await request.is_disconnected():
                    return
                try:
                    await asyncio.wait_for(q.get(), timeout=17)
                    yield "event: change\ndata: {}\n\n"
                except asyncio.TimeoutError:
                    yield ": alive\n\n"
        finally:
            listeners[code.upper()].discard(q)
    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control":"no-cache", "X-Accel-Buffering":"no"})


@app.get("/api/rooms/{code}/qr")
async def qr_image(code: str, request: Request, view: str = "student"):
    get_room(code)
    if view not in ("student", "display"):
        err("화면 유형이 올바르지 않습니다.")
    link = str(request.base_url).rstrip("/") + "/?room=" + code.upper()
    if view == "display":
        link += "&display=1"
    img = qrcode.make(link, box_size=8, border=2)
    output = io.BytesIO()
    img.save(output, format="PNG")
    return Response(content=output.getvalue(), media_type="image/png", headers={"Cache-Control":"no-store"})


@app.exception_handler(HTTPException)
async def readable_error(_, exc: HTTPException):
    return JSONResponse({"error": exc.detail}, status_code=exc.status_code)
