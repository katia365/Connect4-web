import math
import random
try:
    from game_model import PLAYER_RED, PLAYER_YELLOW, EMPTY
except ModuleNotFoundError:
    PLAYER_RED = "Rouge"
    PLAYER_YELLOW = "Jaune"
    EMPTY = " "

WIN_LEN = 4


def _ordered_valid_cols(model):
    cols = model.valid_cols()
    center = model.cols // 2
    return sorted(cols, key=lambda c: abs(c - center))


def _board_key(model):
    """Clé unique pour la transposition table (plateau uniquement — le joueur courant
    est déterminé de façon unique par le plateau sur un jeu à 2 joueurs strictement alternés)."""
    return tuple(tuple(row) for row in model.grid)


def _score_window(window, ai_token, opp_token):
    score = 0
    ai_count = window.count(ai_token)
    opp_count = window.count(opp_token)
    empty_count = window.count(EMPTY)

    if ai_count > 0 and opp_count > 0:
        return 0

    if ai_count > 0:
        if ai_count == WIN_LEN:
            score += 1000
        elif ai_count == WIN_LEN - 1 and empty_count == 1:
            score += 200
        elif ai_count == WIN_LEN - 2 and empty_count == 2:
            score += 50
        elif ai_count == WIN_LEN - 3 and empty_count == 3:
            score += 10

    if opp_count > 0:
        if opp_count == WIN_LEN:
            score -= 1000
        elif opp_count == WIN_LEN - 1 and empty_count == 1:
            score -= 200
        elif opp_count == WIN_LEN - 2 and empty_count == 2:
            score -= 50
        elif opp_count == WIN_LEN - 3 and empty_count == 3:
            score -= 10

    return score


def evaluate(model, ai_token):
    opp_token = PLAYER_RED if ai_token == PLAYER_YELLOW else PLAYER_YELLOW

    if model.result.winner == ai_token:
        return 100_000
    if model.result.winner is not None:
        return -100_000
    if model.result.draw:
        return 0

    score = 0

    center_col = model.cols // 2
    center_array = [model.grid[r][center_col] for r in range(model.rows)]
    score += center_array.count(ai_token) * 6
    score -= center_array.count(opp_token) * 6

    for r in range(model.rows):
        for c in range(model.cols - WIN_LEN + 1):
            window = [model.grid[r][c + k] for k in range(WIN_LEN)]
            score += _score_window(window, ai_token, opp_token)

    for c in range(model.cols):
        for r in range(model.rows - WIN_LEN + 1):
            window = [model.grid[r + k][c] for k in range(WIN_LEN)]
            score += _score_window(window, ai_token, opp_token)

    for r in range(model.rows - WIN_LEN + 1):
        for c in range(model.cols - WIN_LEN + 1):
            window = [model.grid[r + k][c + k] for k in range(WIN_LEN)]
            score += _score_window(window, ai_token, opp_token)

    for r in range(WIN_LEN - 1, model.rows):
        for c in range(model.cols - WIN_LEN + 1):
            window = [model.grid[r - k][c + k] for k in range(WIN_LEN)]
            score += _score_window(window, ai_token, opp_token)

    return score


def minimax(model, depth, alpha, beta, maximizing, ai_token, tt=None):
    if tt is None:
        tt = {}

    cache_key = (_board_key(model), depth, maximizing)
    if cache_key in tt:
        return tt[cache_key]

    if model.result.finished:
        result = evaluate(model, ai_token)
        tt[cache_key] = result
        return result

    if depth == 0:
        result = evaluate(model, ai_token)
        tt[cache_key] = result
        return result

    valid_cols = _ordered_valid_cols(model)
    if not valid_cols:
        result = evaluate(model, ai_token)
        tt[cache_key] = result
        return result

    if maximizing:
        max_eval = -math.inf
        for col in valid_cols:
            row = model.play(col)
            if row is None:
                continue
            val = minimax(model, depth - 1, alpha, beta, False, ai_token, tt)
            model.undo(col, row)
            max_eval = max(max_eval, val)
            alpha = max(alpha, val)
            if beta <= alpha:
                break
        tt[cache_key] = max_eval
        return max_eval
    else:
        min_eval = math.inf
        for col in valid_cols:
            row = model.play(col)
            if row is None:
                continue
            val = minimax(model, depth - 1, alpha, beta, True, ai_token, tt)
            model.undo(col, row)
            min_eval = min(min_eval, val)
            beta = min(beta, val)
            if beta <= alpha:
                break
        tt[cache_key] = min_eval
        return min_eval


def best_move(model, depth, ai_token, randomize_ties=False):
    """
    Retourne (colonne, valeur) du meilleur coup pour ai_token.
    On suppose que c'est AU TOUR de ai_token de jouer (model.current_player == ai_token).
    """
    work_model = model.copy()

    # Vérifier la cohérence : si ce n'est pas le tour de l'IA, on ne peut pas bien jouer
    if work_model.current_player != ai_token:
        # Cas anormal — on joue quand même mais on log
        print(f"[MINIMAX WARNING] best_move appelé mais current_player={work_model.current_player} != ai_token={ai_token}")

    best_col = None
    best_val = -math.inf
    best_cols = []
    tt = {}

    valid_cols = _ordered_valid_cols(work_model)
    if not valid_cols:
        return None, 0

    for col in valid_cols:
        row = work_model.play(col)
        if row is None:
            continue

        # Victoire immédiate
        if work_model.result.finished and work_model.result.winner == ai_token:
            work_model.undo(col, row)
            return col, 100_000

        # Après play(), c'est au tour de l'adversaire → on minimise
        val = minimax(work_model, depth - 1, -math.inf, math.inf, False, ai_token, tt)
        work_model.undo(col, row)

        if val > best_val:
            best_val = val
            best_col = col
            best_cols = [col]
        elif val == best_val:
            best_cols.append(col)

    if randomize_ties and best_cols:
        best_col = random.choice(best_cols)
    elif best_cols and best_col is None:
        best_col = best_cols[0]

    return best_col, best_val
