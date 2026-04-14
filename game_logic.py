import math
import random
import os
from functools import lru_cache

import psycopg2

ROWS = 9
COLS = 9
EMPTY = 0
RED = 1
YELLOW = 2


def create_board():
    return [[EMPTY for _ in range(COLS)] for _ in range(ROWS)]


def get_valid_cols(board):
    return [c for c in range(COLS) if board[0][c] == EMPTY]


def drop_piece(board, col, player):
    for r in range(ROWS - 1, -1, -1):
        if board[r][col] == EMPTY:
            board[r][col] = player
            return r
    return None


def _undo_piece(board, col, row):
    board[row][col] = EMPTY


def is_draw(board):
    return len(get_valid_cols(board)) == 0


def check_winner(board, player):
    # Horizontal
    for r in range(ROWS):
        for c in range(COLS - 3):
            if all(board[r][c + i] == player for i in range(4)):
                return True

    # Vertical
    for r in range(ROWS - 3):
        for c in range(COLS):
            if all(board[r + i][c] == player for i in range(4)):
                return True

    # Diagonal down-right
    for r in range(ROWS - 3):
        for c in range(COLS - 3):
            if all(board[r + i][c + i] == player for i in range(4)):
                return True

    # Diagonal up-right
    for r in range(3, ROWS):
        for c in range(COLS - 3):
            if all(board[r - i][c + i] == player for i in range(4)):
                return True

    return False


def _copy_board(board):
    return [row[:] for row in board]


def _order_cols(valid_cols):
    center = (COLS - 1) / 2
    return sorted(valid_cols, key=lambda c: abs(c - center))


def get_db_connection():
    database_url = os.getenv("DATABASE_URL")
    if database_url:
        if "sslmode=" in database_url:
            return psycopg2.connect(database_url)
        return psycopg2.connect(database_url, sslmode=os.getenv("DB_SSLMODE", "require"))

    return psycopg2.connect(
        dbname=os.getenv("DB_NAME", "postgres"),
        user=os.getenv("DB_USER", "postgres"),
        password=os.getenv("DB_PASSWORD", "12082004"),
        host=os.getenv("DB_HOST", "localhost"),
        port=os.getenv("DB_PORT", "5432"),
    )


def ai_easy(board):
    valid = get_valid_cols(board)
    return random.choice(valid) if valid else None


def _ai_medium_log(message):
    if os.getenv("AI_MEDIUM_DEBUG", "1") != "0":
        print(f"[AI_MEDIUM] {message}")


def _reconstruct_sequence_from_board(board):
    # Reconstruct one valid 1-indexed move sequence for the current board,
    # assuming RED starts and gravity is respected.
    reds = 0
    yellows = 0
    col_stacks = []

    for c in range(COLS):
        seen_empty = False
        stack = []
        for r in range(ROWS - 1, -1, -1):
            v = board[r][c]
            if v == EMPTY:
                seen_empty = True
            else:
                if seen_empty:
                    return ""
                if v == RED:
                    reds += 1
                elif v == YELLOW:
                    yellows += 1
                else:
                    return ""
                stack.append(v)
        col_stacks.append(stack)

    if yellows > reds or reds - yellows > 1:
        return ""

    total = reds + yellows
    if total == 0:
        return ""

    target_heights = tuple(len(s) for s in col_stacks)

    @lru_cache(maxsize=None)
    def dfs(filled, current):
        if filled == target_heights:
            return ""

        next_player = YELLOW if current == RED else RED
        for c in range(COLS):
            idx = filled[c]
            stack = col_stacks[c]
            if idx < len(stack) and stack[idx] == current:
                nf = list(filled)
                nf[c] += 1
                suffix = dfs(tuple(nf), next_player)
                if suffix is not None:
                    return str(c + 1) + suffix
        return None

    reconstructed = dfs((0,) * COLS, RED)
    return reconstructed or ""


def _mirror_col(col):
    return COLS - 1 - col


def _normalize_sequence_for_board(board, sequence):
    if sequence:
        return sequence
    return _reconstruct_sequence_from_board(board)


def _winner_label(player):
    return "ROUGE" if player == RED else "JAUNE"


@lru_cache(maxsize=100000)
def _infer_winner_label_from_sequence(sequence):
    if not sequence:
        return None

    board = create_board()
    current = RED

    for ch in sequence:
        if ch < '1' or ch > str(COLS):
            return None
        col = int(ch) - 1
        if col < 0 or col >= COLS:
            return None

        row = drop_piece(board, col, current)
        if row is None:
            return None

        if check_winner(board, current):
            return _winner_label(current)

        current = YELLOW if current == RED else RED

    return None


def _safe_columns(board, player):
    valid = get_valid_cols(board)
    opp = RED if player == YELLOW else YELLOW

    safe = []
    for col in valid:
        tmp = _copy_board(board)
        drop_piece(tmp, col, player)

        opp_can_win_next = False
        for opp_col in get_valid_cols(tmp):
            tmp_opp = _copy_board(tmp)
            drop_piece(tmp_opp, opp_col, opp)
            if check_winner(tmp_opp, opp):
                opp_can_win_next = True
                break

        if not opp_can_win_next:
            safe.append(col)

    return safe if safe else valid


def _db_candidates_for_next_move(sequence, winner_label):
    conn = None
    cur = None
    out = []
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT sequence_coups, sequence_miroir, vainqueur
            FROM parties
            WHERE (sequence_coups LIKE %s OR sequence_miroir LIKE %s)
              AND (vainqueur = %s OR vainqueur IS NULL)
            ORDER BY LENGTH(sequence_coups) ASC, sequence_coups ASC
            """,
            (sequence + '%', sequence + '%', winner_label)
        )

        for seq_coups, seq_miroir, stored_winner in cur.fetchall():
            resolved_winner = stored_winner or _infer_winner_label_from_sequence(seq_coups or "")
            if resolved_winner != winner_label:
                continue

            if seq_coups and seq_coups.startswith(sequence) and len(seq_coups) > len(sequence):
                candidate_col = int(seq_coups[len(sequence)]) - 1
                win_in = len(seq_coups) - len(sequence)
                out.append((candidate_col, win_in, "db_direct"))
            elif seq_miroir and seq_miroir.startswith(sequence) and len(seq_miroir) > len(sequence):
                mirror_next_col = int(seq_miroir[len(sequence)]) - 1
                candidate_col = _mirror_col(mirror_next_col)
                win_in = len(seq_miroir) - len(sequence)
                out.append((candidate_col, win_in, "db_mirror"))
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()

    return out


def _db_min_remaining_moves(sequence, winner_label):
    conn = None
    cur = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT sequence_coups, sequence_miroir, vainqueur
            FROM parties
            WHERE (sequence_coups LIKE %s OR sequence_miroir LIKE %s)
              AND (vainqueur = %s OR vainqueur IS NULL)
            ORDER BY LENGTH(sequence_coups) ASC, sequence_coups ASC
            """,
            (sequence + '%', sequence + '%', winner_label)
        )

        for seq_coups, seq_miroir, stored_winner in cur.fetchall():
            resolved_winner = stored_winner or _infer_winner_label_from_sequence(seq_coups or "")
            if resolved_winner != winner_label:
                continue

            if seq_coups and seq_coups.startswith(sequence):
                return len(seq_coups) - len(sequence)
            if seq_miroir and seq_miroir.startswith(sequence):
                return len(seq_miroir) - len(sequence)

        return None
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


def bdd_hint_with_messages(board, player, sequence=""):
    sequence = _normalize_sequence_for_board(board, sequence)
    valid = get_valid_cols(board)

    red_in = _db_min_remaining_moves(sequence, "ROUGE")
    yellow_in = _db_min_remaining_moves(sequence, "JAUNE")

    messages = []
    if red_in is None:
        messages.append("Rouge : aucune ligne gagnante trouvée en BDD depuis cette position")
    else:
        messages.append(f"Rouge gagne en {red_in} coup(s) selon la BDD")

    if yellow_in is None:
        messages.append("Jaune : aucune ligne gagnante trouvée en BDD depuis cette position")
    else:
        messages.append(f"Jaune gagne en {yellow_in} coup(s) selon la BDD")

    if not valid:
        return {
            "col": None,
            "messages": messages,
            "sequence": sequence,
            "source": "none",
        }

    search_cols = _safe_columns(board, player)
    candidates = _db_candidates_for_next_move(sequence, _winner_label(player))

    best_col = None
    best_win_in = float('inf')
    best_source = None
    seen = set()

    for candidate_col, win_in, source in candidates:
        if candidate_col not in search_cols:
            continue
        if candidate_col in seen:
            continue
        seen.add(candidate_col)

        if win_in < best_win_in:
            best_win_in = win_in
            best_col = candidate_col
            best_source = source
        elif win_in == best_win_in and best_col is not None:
            center = (COLS - 1) / 2
            if abs(candidate_col - center) < abs(best_col - center):
                best_col = candidate_col
                best_source = source

    if best_col is not None:
        messages.append(
            f"Conseil BDD: jouer colonne {best_col + 1} (victoire estimée en {best_win_in} coup(s))"
        )
        return {
            "col": best_col,
            "messages": messages,
            "sequence": sequence,
            "source": best_source or "db",
        }

    messages.append("Conseil BDD: aucun coup trouvé pour ce préfixe")
    return {
        "col": None,
        "messages": messages,
        "sequence": sequence,
        "source": "fallback_none",
    }


def ai_medium_with_seq(board, ai_player, sequence=""):
    sequence = _normalize_sequence_for_board(board, sequence)
    if sequence:
        _ai_medium_log(f"sequence_used seq='{sequence}'")
    else:
        _ai_medium_log("sequence_missing_and_not_reconstructable")

    valid = get_valid_cols(board)
    if not valid:
        _ai_medium_log("source=none reason=no_valid_cols")
        return None

    opp = RED if ai_player == YELLOW else YELLOW

    # 1) Win now if possible
    for col in valid:
        tmp = _copy_board(board)
        drop_piece(tmp, col, ai_player)
        if check_winner(tmp, ai_player):
            _ai_medium_log(
                f"source=immediate_win player={ai_player} seq='{sequence}' col={col + 1}"
            )
            return col

    # 2) Block immediate opponent win
    for col in valid:
        tmp = _copy_board(board)
        drop_piece(tmp, col, opp)
        if check_winner(tmp, opp):
            _ai_medium_log(
                f"source=block player={ai_player} seq='{sequence}' col={col + 1}"
            )
            return col

    # Ne considérer en priorité que les coups qui ne donnent pas
    # une victoire immédiate à l'adversaire au coup suivant.
    search_cols = _safe_columns(board, ai_player)
    safe_valid = search_cols[:]

    # 3) Chercher dans la BDD le coup qui mène à une victoire
    #    le plus tôt possible parmi les parties connues
    winner_label = _winner_label(ai_player)

    best_col = None
    best_win_in = float('inf')
    best_source = None

    try:
        candidates = _db_candidates_for_next_move(sequence, winner_label)
        seen_candidates = set()
        for candidate_col, win_in, source in candidates:
            if candidate_col not in search_cols:
                continue

            if candidate_col in seen_candidates:
                continue
            seen_candidates.add(candidate_col)

            if win_in < best_win_in:
                best_win_in = win_in
                best_col = candidate_col
                best_source = source
            elif win_in == best_win_in and best_col is not None:
                center = (COLS - 1) / 2
                if abs(candidate_col - center) < abs(best_col - center):
                    best_col = candidate_col
                    best_source = source
    except Exception as e:
        print(f"Erreur BDD dans ai_medium_with_seq: {e}")

    if best_col is not None:
        _ai_medium_log(
            f"source={best_source or 'db'} player={ai_player} seq='{sequence}' col={best_col + 1} win_in={best_win_in} "
            f"searched={len(search_cols)} safe={len(safe_valid)}"
        )
        return best_col

    # 4) Fallback : colonnes centrales
    preferred = _order_cols(search_cols)
    seed = sum(ord(ch) for ch in sequence) if sequence else 0
    rnd = random.Random(seed)
    choice = rnd.choice(preferred[:min(3, len(preferred))])
    _ai_medium_log(
        f"source=fallback player={ai_player} seq='{sequence}' col={choice + 1} "
        f"searched={len(search_cols)} safe={len(safe_valid)}"
    )
    return choice


def _evaluate_window(window, player):
    opp = RED if player == YELLOW else YELLOW
    mine = window.count(player)
    theirs = window.count(opp)
    empties = window.count(EMPTY)

    score = 0
    if mine == 4:
        score += 100000
    elif mine == 3 and empties == 1:
        score += 120
    elif mine == 2 and empties == 2:
        score += 12

    if theirs == 3 and empties == 1:
        score -= 150
    elif theirs == 2 and empties == 2:
        score -= 10

    return score


def _score_position(board, player):
    score = 0

    # Slight center bias for stronger play and faster tie-breaks
    center_col = COLS // 2
    center_count = sum(1 for r in range(ROWS) if board[r][center_col] == player)
    score += center_count * 8

    # Horizontal windows
    for r in range(ROWS):
        for c in range(COLS - 3):
            window = [board[r][c + i] for i in range(4)]
            score += _evaluate_window(window, player)

    # Vertical windows
    for c in range(COLS):
        for r in range(ROWS - 3):
            window = [board[r + i][c] for i in range(4)]
            score += _evaluate_window(window, player)

    # Diagonal down-right windows
    for r in range(ROWS - 3):
        for c in range(COLS - 3):
            window = [board[r + i][c + i] for i in range(4)]
            score += _evaluate_window(window, player)

    # Diagonal up-right windows
    for r in range(3, ROWS):
        for c in range(COLS - 3):
            window = [board[r - i][c + i] for i in range(4)]
            score += _evaluate_window(window, player)

    return score


def _terminal_value(board, ai_player, depth):
    opp = RED if ai_player == YELLOW else YELLOW
    if check_winner(board, ai_player):
        return 1_000_000 + depth
    if check_winner(board, opp):
        return -1_000_000 - depth
    if is_draw(board):
        return 0
    return None


def _board_key(board):
    return tuple(tuple(row) for row in board)


def _minimax(board, depth, alpha, beta, maximizing, ai_player, tt=None):
    if tt is None:
        tt = {}

    key = (_board_key(board), depth, maximizing, ai_player)
    if key in tt:
        return tt[key]

    terminal = _terminal_value(board, ai_player, depth)
    if terminal is not None:
        tt[key] = terminal
        return terminal
    if depth == 0:
        score = _score_position(board, ai_player)
        tt[key] = score
        return score

    valid = _order_cols(get_valid_cols(board))
    if maximizing:
        value = -math.inf
        for col in valid:
            row = drop_piece(board, col, ai_player)
            if row is None:
                continue
            value = max(value, _minimax(board, depth - 1, alpha, beta, False, ai_player, tt))
            _undo_piece(board, col, row)
            alpha = max(alpha, value)
            if alpha >= beta:
                break
        tt[key] = value
        return value

    opp = RED if ai_player == YELLOW else YELLOW
    value = math.inf
    for col in valid:
        row = drop_piece(board, col, opp)
        if row is None:
            continue
        value = min(value, _minimax(board, depth, alpha, beta, True, ai_player, tt))
        _undo_piece(board, col, row)
        beta = min(beta, value)
        if alpha >= beta:
            break
    tt[key] = value
    return value


def ai_hard(board, ai_player, depth=4):
    valid = get_valid_cols(board)
    if not valid:
        return None

    depth = max(1, min(int(depth), 8))
    ordered = _order_cols(valid)
    tt = {}

    best_col = ordered[0]
    best_val = -math.inf

    for col in ordered:
        row = drop_piece(board, col, ai_player)
        if row is None:
            continue

        if check_winner(board, ai_player):
            _undo_piece(board, col, row)
            return col

        val = _minimax(board, depth - 1, -math.inf, math.inf, False, ai_player, tt)
        _undo_piece(board, col, row)

        if val > best_val:
            best_val = val
            best_col = col

    return best_col
