import math
import random

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


def ai_easy(board):
    valid = get_valid_cols(board)
    return random.choice(valid) if valid else None


def ai_medium_with_seq(board, ai_player, sequence=""):
    valid = get_valid_cols(board)
    if not valid:
        return None

    opp = RED if ai_player == YELLOW else YELLOW

    # 1) Win now if possible
    for col in valid:
        tmp = _copy_board(board)
        drop_piece(tmp, col, ai_player)
        if check_winner(tmp, ai_player):
            return col

    # 2) Block immediate opponent win
    for col in valid:
        tmp = _copy_board(board)
        drop_piece(tmp, col, opp)
        if check_winner(tmp, opp):
            return col

    # 3) Prefer center-ish columns, with deterministic tie-break from sequence
    preferred = _order_cols(valid)
    if len(preferred) == 1:
        return preferred[0]

    seed = sum(ord(ch) for ch in sequence) if sequence else 0
    rnd = random.Random(seed)
    best_band = preferred[: min(3, len(preferred))]
    return rnd.choice(best_band)


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
