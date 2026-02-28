from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import json, random, string, asyncio
from game_logic import (
    create_board, drop_piece, check_winner, is_draw,
    get_valid_cols, ai_easy, ai_medium_with_seq, ai_hard,
    RED, YELLOW, EMPTY, ROWS, COLS
)

app = FastAPI()

rooms = {}
queue = []

def gen_id(n=6):
    while True:
        code = "".join(random.choices(string.ascii_uppercase + string.digits, k=n))
        if code not in rooms:
            return code

class RoomState:
    def __init__(self, rid, mode, ai_level="hard"):
        self.id = rid
        self.mode = mode            # "pvp" | "ai"
        self.ai_level = ai_level    # "easy" | "medium" | "hard"
        self.board = create_board()
        self.current = RED
        self.players = {}
        self.status = "waiting"
        self.winner = None
        self.history = []           # liste de coups joués [(col, player), ...]
        self.sequence = ""          # séquence BGA style (colonnes 1-indexed)

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
    """Retourne la couleur de l'IA (celle qui n'est pas le joueur)."""
    player_colors = list(room.players.keys())
    if "red" in player_colors:
        return YELLOW, "yellow"
    else:
        return RED, "red"

async def do_ai_move(room):
    """Joue le coup de l'IA selon le niveau."""
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
        col = ai_hard(room.board, ai_val, depth=4)

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

# ══════════════════════════════════════════════════
# WEBSOCKET
# ══════════════════════════════════════════════════
@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket):
    await websocket.accept()
    try:
        init = json.loads(await websocket.receive_text())
        action = init.get("action")
        ai_level = init.get("ai_level", "hard")

        if action == "ai":
            rid = gen_id()
            room = RoomState(rid, "ai", ai_level)
            rooms[rid] = room
            preferred = init.get("preferred_color", "red")
            player_color = preferred if preferred in ("red", "yellow") else "red"
            room.players[player_color] = websocket
            room.status = "playing"
            await websocket.send_text(json.dumps(room.state(player_color)))
            # Si le joueur est jaune, l'IA (rouge) joue en premier
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
            await websocket.send_text(json.dumps({
                "type": "invite_created",
                "room_id": rid,
                **room.state("red")
            }))
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

                # Tour IA
                ai_v, _ = get_ai_color(room)
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
                await broadcast(room)

            elif msg.get("type") == "set_ai_level" and room.mode == "ai":
                room.ai_level = msg.get("level", "hard")
                await broadcast(room)

    except WebSocketDisconnect:
        room.players.pop(color, None)
        if room.status == "playing" and room.mode == "pvp":
            room.status = "finished"
            room.winner = "yellow" if color == "red" else "red"
            await broadcast(room)
        if not room.players:
            rooms.pop(room.id, None)

# ══════════════════════════════════════════════════
# STATIC
# ══════════════════════════════════════════════════
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
async def root():
    return FileResponse("static/index.html")

@app.get("/{path:path}")
async def catch_all(path: str):
    return FileResponse("static/index.html")