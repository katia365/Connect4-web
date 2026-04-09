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


def ai_medium_with_seq(board, ai_player, sequence=""):
    if not sequence:
        reconstructed = _reconstruct_sequence_from_board(board)
        if reconstructed:
            sequence = reconstructed
            _ai_medium_log(f"sequence_reconstructed seq='{sequence}'")
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
    safe_valid = []
    for col in valid:
        tmp = _copy_board(board)
        drop_piece(tmp, col, ai_player)

        opp_can_win_next = False
        for opp_col in get_valid_cols(tmp):
            tmp_opp = _copy_board(tmp)
            drop_piece(tmp_opp, opp_col, opp)
            if check_winner(tmp_opp, opp):
                opp_can_win_next = True
                break

        if not opp_can_win_next:
            safe_valid.append(col)

    search_cols = safe_valid if safe_valid else valid

    # 3) Chercher dans la BDD le coup qui mène à une victoire
    #    le plus tôt possible parmi les parties connues
    winner_label = "ROUGE" if ai_player == RED else "JAUNE"

    conn = None
    cur = None
    best_col = None
    best_win_in = float('inf')
    best_source = None

    try:
        conn = get_db_connection()
        cur = conn.cursor()

        cur.execute(
            """
            SELECT sequence_coups, sequence_miroir
            FROM parties
            WHERE vainqueur = %s
              AND (sequence_coups LIKE %s OR sequence_miroir LIKE %s)
            ORDER BY LENGTH(sequence_coups) ASC, sequence_coups ASC
            """,
            (winner_label, sequence + '%', sequence + '%')
        )

        seen_candidates = set()
        for seq_coups, seq_miroir in cur.fetchall():
            candidate_col = None
            matched_seq = None
            source = None

            if seq_coups and seq_coups.startswith(sequence) and len(seq_coups) > len(sequence):
                candidate_col = int(seq_coups[len(sequence)]) - 1
                matched_seq = seq_coups
                source = "db_direct"
            elif seq_miroir and seq_miroir.startswith(sequence) and len(seq_miroir) > len(sequence):
                mirror_next_col = int(seq_miroir[len(sequence)]) - 1
                candidate_col = _mirror_col(mirror_next_col)
                matched_seq = seq_miroir
                source = "db_mirror"

            if candidate_col is None:
                continue
            if candidate_col not in search_cols:
                continue

            if candidate_col in seen_candidates:
                continue
            seen_candidates.add(candidate_col)

            win_in = len(matched_seq) - len(sequence)
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
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()

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


def _minimax(board, depth, alpha, beta, maximizing, ai_player):
    terminal = _terminal_value(board, ai_player, depth)
    if terminal is not None:
        return terminal
    if depth == 0:
        return _score_position(board, ai_player)

    valid = _order_cols(get_valid_cols(board))
    if maximizing:
        value = -math.inf
        for col in valid:
            tmp = _copy_board(board)
            drop_piece(tmp, col, ai_player)
            value = max(value, _minimax(tmp, depth - 1, alpha, beta, False, ai_player))
            alpha = max(alpha, value)
            if alpha >= beta:
                break
        return value

    opp = RED if ai_player == YELLOW else YELLOW
    value = math.inf
    for col in valid:
        tmp = _copy_board(board)
        drop_piece(tmp, col, opp)
        value = min(value, _minimax(tmp, depth - 1, alpha, beta, True, ai_player))
        beta = min(beta, value)
        if alpha >= beta:
            break
    return value


def ai_hard(board, ai_player, depth=4):
    valid = get_valid_cols(board)
    if not valid:
        return None

    depth = max(1, min(int(depth), 8))
    ordered = _order_cols(valid)

    best_col = ordered[0]
    best_val = -math.inf

    for col in ordered:
        tmp = _copy_board(board)
        drop_piece(tmp, col, ai_player)
        val = _minimax(tmp, depth - 1, -math.inf, math.inf, False, ai_player)
        if val > best_val:
            best_val = val
            best_col = col

    return best_col
