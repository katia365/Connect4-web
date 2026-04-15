import math

try:
    from game_model import PLAYER_RED, PLAYER_YELLOW, EMPTY
except ModuleNotFoundError:
    PLAYER_RED = "Rouge"
    PLAYER_YELLOW = "Jaune"
    EMPTY = " "

WIN_LEN = 4


# =========================
# UTILITAIRES
# =========================

def opponent(player):
    return PLAYER_RED if player == PLAYER_YELLOW else PLAYER_YELLOW


def _board_key(model):
    # IMPORTANT: inclure le joueur courant
    return (tuple(tuple(row) for row in model.grid), model.current_player)


def _ordered_valid_cols(model):
    cols = model.valid_cols()
    center = model.cols // 2

    # Ordre déterministe: priorité centre, puis colonne.
    return sorted(cols, key=lambda c: (abs(c - center), c))


# =========================
# ÉVALUATION FORTE
# =========================

def _score_window(window, ai, opp):
    score = 0
    ai_count = window.count(ai)
    opp_count = window.count(opp)
    empty = window.count(EMPTY)

    if ai_count > 0 and opp_count > 0:
        return 0

    # IA
    if ai_count == 4:
        return 100000
    if ai_count == 3 and empty == 1:
        score += 1000
    elif ai_count == 2 and empty == 2:
        score += 100
    elif ai_count == 1 and empty == 3:
        score += 10

    # ADVERSAIRE (plus important !)
    if opp_count == 3 and empty == 1:
        score -= 1200
    elif opp_count == 2 and empty == 2:
        score -= 120
    elif opp_count == 1 and empty == 3:
        score -= 10

    return score


def evaluate(model, ai):
    opp = opponent(ai)

    # états terminaux
    if model.result.winner == ai:
        return 1_000_000
    if model.result.winner == opp:
        return -1_000_000
    if model.result.draw:
        return 0

    score = 0

    # centre (très important)
    center = model.cols // 2
    center_col = [model.grid[r][center] for r in range(model.rows)]
    score += center_col.count(ai) * 8
    score -= center_col.count(opp) * 8

    # horizontal
    for r in range(model.rows):
        for c in range(model.cols - 3):
            window = [model.grid[r][c+i] for i in range(4)]
            score += _score_window(window, ai, opp)

    # vertical
    for c in range(model.cols):
        for r in range(model.rows - 3):
            window = [model.grid[r+i][c] for i in range(4)]
            score += _score_window(window, ai, opp)

    # diag ↘
    for r in range(model.rows - 3):
        for c in range(model.cols - 3):
            window = [model.grid[r+i][c+i] for i in range(4)]
            score += _score_window(window, ai, opp)

    # diag ↗
    for r in range(3, model.rows):
        for c in range(model.cols - 3):
            window = [model.grid[r-i][c+i] for i in range(4)]
            score += _score_window(window, ai, opp)

    return score


# =========================
# COUPS FORCÉS (CRUCIAL)
# =========================

def immediate_win(model, player):
    for col in model.valid_cols():
        row = model.play(col)
        if row is None:
            continue
        if model.result.winner == player:
            model.undo(col, row)
            return col
        model.undo(col, row)
    return None


# =========================
# MINIMAX ALPHA-BETA OPTIMISÉ
# =========================

def minimax(model, depth, alpha, beta, maximizing, ai, tt):

    key = (_board_key(model), depth, maximizing, ai)
    if key in tt:
        return tt[key]

    # terminal
    if model.result.finished or depth == 0:
        val = evaluate(model, ai)
        tt[key] = val
        return val

    valid_cols = _ordered_valid_cols(model)

    if maximizing:
        value = -math.inf

        for col in valid_cols:
            row = model.play(col)
            if row is None:
                continue

            val = minimax(model, depth-1, alpha, beta, False, ai, tt)
            model.undo(col, row)

            value = max(value, val)
            alpha = max(alpha, value)

            if alpha >= beta:
                break  # alpha-beta cut

        tt[key] = value
        return value

    else:
        value = math.inf

        for col in valid_cols:
            row = model.play(col)
            if row is None:
                continue

            val = minimax(model, depth-1, alpha, beta, True, ai, tt)
            model.undo(col, row)

            value = min(value, val)
            beta = min(beta, value)

            if alpha >= beta:
                break  # cut

        tt[key] = value
        return value


# =========================
# MEILLEUR COUP
# =========================

def best_move(model, depth, ai):

    #  1. coup gagnant immédiat
    win = immediate_win(model, ai)
    if win is not None:
        return win, 1_000_000

    #2. bloquer adversaire
    opp = opponent(ai)
    block = immediate_win(model, opp)
    if block is not None:
        return block, 900_000

    best_val = -math.inf
    best_col = None
    tt = {}

    for col in _ordered_valid_cols(model):
        row = model.play(col)
        if row is None:
            continue

        val = minimax(model, depth-1, -math.inf, math.inf, False, ai, tt)
        model.undo(col, row)

        if val > best_val:
            best_val = val
            best_col = col

    return best_col, best_val


def analyze_moves(model, depth, ai):
    """Retourne un dictionnaire {colonne: score} pour tous les coups valides."""
    work_model = model.copy()
    scores = {}
    tt = {}

    # Même logique prioritaire que best_move pour éviter les écarts UI/coup joué.
    win = immediate_win(work_model, ai)
    if win is not None:
        for col in work_model.valid_cols():
            scores[col] = 1_000_000 if col == win else -1_000_000
        return scores

    opp = opponent(ai)
    block = immediate_win(work_model, opp)
    if block is not None:
        for col in work_model.valid_cols():
            scores[col] = 900_000 if col == block else -900_000
        return scores

    for col in _ordered_valid_cols(work_model):
        row = work_model.play(col)
        if row is None:
            continue

        if work_model.result.finished and work_model.result.winner == ai:
            score = 1_000_000
        else:
            score = minimax(work_model, depth - 1, -math.inf, math.inf, False, ai, tt)

        work_model.undo(col, row)
        scores[col] = score

    return scores
