from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import json, random, string, asyncio
import os
from datetime import datetime
import psycopg2
from game_logic import (
    create_board, drop_piece, check_winner, is_draw,
    get_valid_cols, ai_easy, ai_medium_with_seq, ai_hard, ai_hard_scores, bdd_hint_with_messages,
    RED, YELLOW, ROWS, COLS
)

app = FastAPI()

rooms = {}
queue = []


def get_db_connection():
    database_url = os.getenv("DATABASE_URL")
    if database_url:
        if "sslmode=" in database_url:
            return psycopg2.connect(database_url)
        return psycopg2.connect(database_url, sslmode=os.getenv("DB_SSLMODE", "require"))


def mirror_sequence(sequence, cols=9):
    return "".join(str(cols + 1 - int(c)) for c in sequence)


def _save_room_to_db_sync(room):
    if not room.sequence:
        return False

    winner_map = {
        "red": "ROUGE",
        "yellow": "JAUNE",
        "draw": "MATCH_NUL",
    }
    mode_map = {
        "ai": 1,
        "pvp": 2,
    }

    vainqueur = winner_map.get(room.winner)
    mode_jeu = mode_map.get(room.mode, 2)
    dimensions = f"{ROWS}x{COLS}"
    sequence = room.sequence
    sequence_miroir = mirror_sequence(sequence, COLS)

    conn = None
    cur = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO parties (
                date_fin, sequence_coups, sequence_miroir,
                mode_jeu, dimensions, statut, vainqueur, source
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (sequence_coups) DO NOTHING
            """,
            (
                datetime.utcnow(),
                sequence,
                sequence_miroir,
                mode_jeu,
                dimensions,
                "TERMINEE",
                vainqueur,
                "SITE",
            ),
        )
        conn.commit()
        return True
    except Exception as e:
        print(f"DB save error for room {room.id}: {e}")
        return False
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


async def persist_finished_room(room):
    if room.status != "finished" or room.saved_to_db:
        return

    async with room.save_lock:
        if room.status != "finished" or room.saved_to_db:
            return
        ok = await asyncio.to_thread(_save_room_to_db_sync, room)
        if ok:
            room.saved_to_db = True

def gen_id(n=6):
    while True:
        code = "".join(random.choices(string.ascii_uppercase + string.digits, k=n))
        if code not in rooms:
            return code

class RoomState:
    def __init__(self, rid, mode, ai_level="hard", minimax_depth=4):
        self.id = rid
        self.mode = mode
        self.ai_level = ai_level
        self.minimax_depth = minimax_depth
        self.board = create_board()
        self.current = RED
        self.players = {}
        self.status = "waiting"
        self.winner = None
        self.history = []
        self.sequence = ""
        self.saved_to_db = False
        self.save_lock = asyncio.Lock()

    def flat(self):
        out = []
        for row in self.board:
            out.extend(row)
        return out

    def state(self, color=None):
        return {
            "type": "state",
            "board": self.flat(),
            "current": self.current,
            "status": self.status,
            "winner": self.winner,
            "mode": self.mode,
            "ai_level": self.ai_level,
            "minimax_depth": self.minimax_depth,
            "room_id": self.id,
            "your_color": color,
            "players_count": len(self.players),
            "history": self.history,
            "move_count": len(self.history),
        }

async def broadcast(room):
    for c, ws in list(room.players.items()):
        try:
            await ws.send_text(json.dumps(room.state(c)))
        except Exception:
            pass

def get_ai_color(room):
    player_colors = list(room.players.keys())
    if "red" in player_colors:
        return YELLOW, "yellow"
    else:
        return RED, "red"

async def do_ai_move(room):
    print(f"do_ai_move appelé: level={room.ai_level} depth={room.minimax_depth} seq='{room.sequence}'")
    valid = get_valid_cols(room.board)
    if not valid:
        return

    ai_val, ai_str = get_ai_color(room)
    level = room.ai_level
    if level == "easy":
        col = ai_easy(room.board)
    elif level == "medium":
        col = ai_medium_with_seq(room.board, ai_val, room.sequence)
    else:
        col = ai_hard(room.board, ai_val, depth=room.minimax_depth)

    if col is None or col not in valid:
        col = random.choice(valid)

    drop_piece(room.board, col, ai_val)
    room.history.append(col)
    room.sequence += str(col + 1)

    if check_winner(room.board, ai_val):
        room.status = "finished"
        room.winner = ai_str
    elif is_draw(room.board):
        room.status = "finished"
        room.winner = "draw"
    else:
        room.current = RED if room.current == YELLOW else YELLOW

    await broadcast(room)
    if room.status == "finished":
        await persist_finished_room(room)

async def handle_hint(websocket: WebSocket, init: dict):
    flat = init.get("board", [])
    player_val = init.get("player", RED)
    level = init.get("ai_level", "hard")
    depth = int(init.get("minimax_depth", 4))
    sequence = init.get("sequence", "")

    if not isinstance(flat, list) or len(flat) != ROWS * COLS:
        await websocket.send_text(json.dumps({"type": "ai_hint", "col": -1}))
        return

    board = []
    for r in range(ROWS):
        board.append(flat[r * COLS:(r + 1) * COLS])

    valid = get_valid_cols(board)
    if not valid:
        await websocket.send_text(json.dumps({"type": "ai_hint", "col": -1}))
        return

    if level == "easy":
        col = ai_easy(board)
    elif level == "medium":
        col = ai_medium_with_seq(board, player_val, sequence)
    else:
        col = ai_hard(board, player_val, depth=depth)

    if col is None or col not in valid:
        col = random.choice(valid)

    await websocket.send_text(json.dumps({"type": "ai_hint", "col": col}))


async def handle_bdd_hint(websocket: WebSocket, init: dict):
    flat = init.get("board", [])
    player_val = init.get("player", RED)
    sequence = init.get("sequence", "")

    if not isinstance(flat, list) or len(flat) != ROWS * COLS:
        await websocket.send_text(json.dumps({"type": "ai_bdd_hint", "col": -1, "messages": []}))
        return

    board = []
    for r in range(ROWS):
        board.append(flat[r * COLS:(r + 1) * COLS])

    try:
        info = bdd_hint_with_messages(board, player_val, sequence)
    except Exception as e:
        print(f"BDD hint error: {e}")
        await websocket.send_text(json.dumps({"type": "ai_bdd_hint", "col": -1, "messages": []}))
        return

    col = info.get("col")
    await websocket.send_text(
        json.dumps(
            {
                "type": "ai_bdd_hint",
                "col": col if col is not None else -1,
                "messages": info.get("messages", []),
                "source": info.get("source", "none"),
                "sequence": info.get("sequence", sequence),
            }
        )
    )


async def handle_minimax_scores(websocket: WebSocket, init: dict):
    flat = init.get("board", [])
    player_val = init.get("player", RED)
    depth = int(init.get("minimax_depth", 4))

    if not isinstance(flat, list) or len(flat) != ROWS * COLS:
        await websocket.send_text(json.dumps({"type": "ai_minimax_scores", "scores": {}, "best_col": None}))
        return

    board = []
    for r in range(ROWS):
        board.append(flat[r * COLS:(r + 1) * COLS])

    # Source unique pour le coup joué: ai_hard -> best_move.
    try:
        best_col = ai_hard(board, player_val, depth=depth)
    except Exception as e:
        print(f"Minimax best_col error: {e}")
        best_col = None

    # Les scores servent à l'affichage; si ça casse on renvoie quand même best_col.
    try:
        scores = ai_hard_scores(board, player_val, depth=depth)
    except Exception as e:
        print(f"Minimax score error: {e}")
        scores = {}

    best_score = scores.get(best_col) if best_col is not None else None

    if best_col is None and scores:
        best_col = max(scores, key=lambda c: scores[c])
        best_score = scores[best_col]

    await websocket.send_text(
        json.dumps(
            {
                "type": "ai_minimax_scores",
                "scores": scores,
                "best_col": best_col,
                "best_score": best_score,
            }
        )
    )


async def handle_best_col(websocket: WebSocket, init: dict):
    flat = init.get("board", [])
    player_val = init.get("player", RED)
    depth = int(init.get("minimax_depth", 4))

    if not isinstance(flat, list) or len(flat) != ROWS * COLS:
        await websocket.send_text(json.dumps({"type": "ai_best_col", "best_col": None}))
        return

    board = []
    for r in range(ROWS):
        board.append(flat[r * COLS:(r + 1) * COLS])

    try:
        best_col = ai_hard(board, player_val, depth=depth)
    except Exception as e:
        print(f"Best col error: {e}")
        best_col = None

    valid = get_valid_cols(board)
    if best_col is None or best_col not in valid:
        best_col = valid[0] if valid else None

    await websocket.send_text(json.dumps({"type": "ai_best_col", "best_col": best_col}))

@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket):
    await websocket.accept()
    try:
        init = json.loads(await websocket.receive_text())
        action = init.get("action")
        ai_level = init.get("ai_level", "hard")
        minimax_depth = int(init.get("minimax_depth", 4))

        if action == "ai_hint":
            await handle_hint(websocket, init)
            return

        if action == "ai_bdd_hint":
            await handle_bdd_hint(websocket, init)
            return

        if action == "ai_minimax_scores":
            await handle_minimax_scores(websocket, init)
            return

        if action == "ai_best_col":
            await handle_best_col(websocket, init)
            return

        if action == "ai":
            rid = gen_id()
            room = RoomState(rid, "ai", ai_level, minimax_depth)
            rooms[rid] = room
            preferred = init.get("preferred_color", "red")
            player_color = preferred if preferred in ("red", "yellow") else "red"
            room.players[player_color] = websocket
            room.status = "playing"
            await websocket.send_text(json.dumps(room.state(player_color)))
            if player_color == "yellow":
                await asyncio.sleep(0.6)
                await do_ai_move(room)
            await handle_game(websocket, room, player_color)

        elif action == "queue":
            future = asyncio.get_event_loop().create_future()
            await try_match(websocket, future)
            await websocket.send_text(json.dumps({"type": "waiting"}))
            try:
                color, rid = await asyncio.wait_for(future, timeout=300)
            except asyncio.TimeoutError:
                queue[:] = [(ws, f) for ws, f in queue if ws is not websocket]
                await websocket.send_text(json.dumps({"type": "error", "msg": "Timeout"}))
                return
            room = rooms[rid]
            room.players[color] = websocket
            if len(room.players) == 2:
                room.status = "playing"
            await broadcast(room)
            await handle_game(websocket, room, color)

        elif action == "invite":
            rid = gen_id()
            room = RoomState(rid, "pvp")
            rooms[rid] = room
            room.players["red"] = websocket
            state = room.state("red")
            state["type"] = "invite_created"
            await websocket.send_text(json.dumps(state))
            await handle_game(websocket, room, "red")

        elif action == "join":
            rid = init.get("room_id", "").upper()
            if rid not in rooms:
                await websocket.send_text(json.dumps({"type": "error", "msg": "Partie introuvable"}))
                return
            room = rooms[rid]
            if len(room.players) >= 2:
                await websocket.send_text(json.dumps({"type": "error", "msg": "Partie pleine"}))
                return
            room.players["yellow"] = websocket
            room.status = "playing"
            await broadcast(room)
            await handle_game(websocket, room, "yellow")

    except WebSocketDisconnect:
        queue[:] = [(ws, f) for ws, f in queue if ws is not websocket]
    except Exception as e:
        print(f"WS error: {e}")

async def try_match(ws, future):
    for i, (other_ws, other_future) in enumerate(queue):
        if not other_future.done():
            queue.pop(i)
            rid = gen_id()
            room = RoomState(rid, "pvp")
            rooms[rid] = room
            other_future.set_result(("red", rid))
            future.set_result(("yellow", rid))
            return
    queue.append((ws, future))

async def handle_game(websocket, room, color):
    try:
        while True:
            data = await websocket.receive_text()
            msg = json.loads(data)

            if msg.get("type") == "move" and room.status == "playing":
                col = int(msg.get("col", -1))
                player_val = RED if color == "red" else YELLOW

                if room.current != player_val:
                    continue
                if col not in get_valid_cols(room.board):
                    continue

                drop_piece(room.board, col, player_val)
                room.history.append(col)
                room.sequence += str(col + 1)

                if check_winner(room.board, player_val):
                    room.status = "finished"
                    room.winner = color
                elif is_draw(room.board):
                    room.status = "finished"
                    room.winner = "draw"
                else:
                    room.current = YELLOW if room.current == RED else RED

                await broadcast(room)
                if room.status == "finished":
                    await persist_finished_room(room)

                ai_v, _ = get_ai_color(room)
                print(f"après coup: mode={room.mode} status={room.status} current={room.current} ai_v={ai_v}")
                if room.mode == "ai" and room.status == "playing" and room.current == ai_v:
                    delay = 0.3 if room.ai_level == "easy" else (0.6 if room.ai_level == "medium" else 1.0)
                    await asyncio.sleep(delay)
                    await do_ai_move(room)

            elif msg.get("type") == "restart":
                room.board = create_board()
                room.current = RED
                room.status = "playing"
                room.winner = None
                room.history = []
                room.sequence = ""
                room.saved_to_db = False
                await broadcast(room)

            elif msg.get("type") == "set_ai_level" and room.mode == "ai":
                room.ai_level = msg.get("level", "hard")
                room.minimax_depth = int(msg.get("minimax_depth", room.minimax_depth))
                await broadcast(room)

    except WebSocketDisconnect:
        room.players.pop(color, None)
        if room.status == "playing" and room.mode == "pvp":
            room.status = "finished"
            room.winner = "yellow" if color == "red" else "red"
            await broadcast(room)
            await persist_finished_room(room)
        if not room.players:
            rooms.pop(room.id, None)

app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
async def root():
    return FileResponse("static/index.html")


@app.get("/api/parties")
async def api_parties(
    limit: int = Query(default=1000, ge=1, le=5000),
    offset: int = Query(default=0, ge=0),
):
    conn = None
    cur = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, mode_jeu, dimensions, statut, vainqueur, source, sequence_coups, sequence_miroir, date_fin
            FROM parties
            ORDER BY id DESC
            LIMIT %s OFFSET %s
            """
            ,
            (limit, offset)
        )
        rows = cur.fetchall()

        items = []
        for row in rows:
            items.append(
                {
                    "id": row[0],
                    "mode_jeu": row[1],
                    "dimensions": row[2],
                    "statut": row[3],
                    "vainqueur": row[4],
                    "source": row[5],
                    "sequence_coups": row[6],
                    "sequence_miroir": row[7],
                    "date_fin": row[8].isoformat() if row[8] else None,
                }
            )
        return items
    except Exception as e:
        print(f"DB read error /api/parties: {e}")
        return []
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()

@app.get("/{path:path}")
async def catch_all(path: str):
    return FileResponse("static/index.html")
