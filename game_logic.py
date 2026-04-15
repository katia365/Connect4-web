import random
import os
from functools import lru_cache

import psycopg2
from minimax import best_move, analyze_moves

MODEL_EMPTY = " "
MODEL_RED = "Rouge"
MODEL_YELLOW = "Jaune"

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
    for r in range(ROWS):
        for c in range(COLS - 3):
            if all(board[r][c + i] == player for i in range(4)):
                return True
    for r in range(ROWS - 3):
        for c in range(COLS):
            if all(board[r + i][c] == player for i in range(4)):
                return True
    for r in range(ROWS - 3):
        for c in range(COLS - 3):
            if all(board[r + i][c + i] == player for i in range(4)):
                return True
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


def _resolve_winner(stored_winner, seq_coups):
    """Résout le vainqueur, que ce soit stocké en BDD ou inféré depuis la séquence."""
    if stored_winner:
        return stored_winner
    return _infer_winner_label_from_sequence(seq_coups or "")


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
            ORDER BY LENGTH(sequence_coups) ASC, sequence_coups ASC
            """,
            (sequence + '%', sequence + '%')
        )

        for seq_coups, seq_miroir, stored_winner in cur.fetchall():
            resolved_winner = _resolve_winner(stored_winner, seq_coups)
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


def _db_next_move_stats(sequence, winner_label):
    conn = None
    cur = None
    stats = {}

    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT sequence_coups, sequence_miroir, vainqueur
            FROM parties
            WHERE (sequence_coups LIKE %s OR sequence_miroir LIKE %s)
            """,
            (sequence + '%', sequence + '%'),
        )

        for seq_coups, seq_miroir, stored_winner in cur.fetchall():
            next_col = None
            remaining = None
            used_seq = None

            if seq_coups and seq_coups.startswith(sequence) and len(seq_coups) > len(sequence):
                ch = seq_coups[len(sequence)]
                if ch.isdigit() and 1 <= int(ch) <= COLS:
                    next_col = int(ch) - 1
                    remaining = len(seq_coups) - len(sequence)
                    used_seq = seq_coups
            elif seq_miroir and seq_miroir.startswith(sequence) and len(seq_miroir) > len(sequence):
                ch = seq_miroir[len(sequence)]
                if ch.isdigit() and 1 <= int(ch) <= COLS:
                    next_col = _mirror_col(int(ch) - 1)
                    remaining = len(seq_miroir) - len(sequence)
                    used_seq = seq_coups  # on infère depuis la séquence originale

            if next_col is None or remaining is None:
                continue

            # Résolution robuste du vainqueur
            winner = _resolve_winner(stored_winner, used_seq)

            item = stats.setdefault(
                next_col,
                {
                    "samples": 0,
                    "wins": 0,
                    "win_in_sum": 0,
                    "min_win_in": None,
                },
            )
            item["samples"] += 1

            if winner == winner_label:
                item["wins"] += 1
                item["win_in_sum"] += remaining
                if item["min_win_in"] is None or remaining < item["min_win_in"]:
                    item["min_win_in"] = remaining
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()

    return stats


def _pick_move_from_db_stats(stats, search_cols):
    center = (COLS - 1) / 2
    min_samples = 10
    best_col = None
    best_meta = None
    best_metric = None
    fallback_col = None
    fallback_meta = None
    fallback_metric = None

    for col in search_cols:
        meta = stats.get(col)
        if not meta or meta["samples"] <= 0:
            continue

        samples = meta["samples"]
        wins = meta["wins"]
        win_rate = wins / samples
        avg_win_in = (meta["win_in_sum"] / wins) if wins > 0 else float("inf")
        min_win_in = meta["min_win_in"] if meta["min_win_in"] is not None else float("inf")

        metric = (
            win_rate,
            wins,
            samples,
            -avg_win_in,
            -min_win_in,
            -abs(col - center),
        )

        candidate_meta = {
            "samples": samples,
            "wins": wins,
            "win_rate": win_rate,
            "avg_win_in": avg_win_in,
            "min_win_in": meta["min_win_in"],
        }

        if samples >= min_samples and (best_metric is None or metric > best_metric):
            best_metric = metric
            best_col = col
            best_meta = candidate_meta
        elif fallback_metric is None or metric > fallback_metric:
            fallback_metric = metric
            fallback_col = col
            fallback_meta = candidate_meta

    if best_col is None:
        best_col = fallback_col
        best_meta = fallback_meta

    return best_col, best_meta


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
            ORDER BY LENGTH(sequence_coups) ASC, sequence_coups ASC
            """,
            (sequence + '%', sequence + '%')
        )

        for seq_coups, seq_miroir, stored_winner in cur.fetchall():
            resolved_winner = _resolve_winner(stored_winner, seq_coups)
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
    winner_label = _winner_label(player)

    stats = _db_next_move_stats(sequence, winner_label)
    best_col, best_meta = _pick_move_from_db_stats(stats, search_cols)
    best_source = "db_stats" if best_col is not None else None

    if best_col is None:
        candidates = _db_candidates_for_next_move(sequence, winner_label)
        best_win_in = float('inf')
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
        if best_meta is not None:
            messages.append(
                f"Analyse BDD: coup le plus fiable = colonne {best_col + 1} "
                f"({best_meta['wins']}/{best_meta['samples']} victoires, {round(best_meta['win_rate'] * 100)}%)"
            )
            estimated_in = best_meta["min_win_in"] if best_meta["min_win_in"] is not None else "?"
        else:
            estimated_in = "?"
        messages.append(
            f"Conseil BDD: jouer colonne {best_col + 1} (victoire estimée en {estimated_in} coup(s))"
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
            _ai_medium_log(f"source=immediate_win player={ai_player} seq='{sequence}' col={col + 1}")
            return col

    # 2) Block immediate opponent win
    for col in valid:
        tmp = _copy_board(board)
        drop_piece(tmp, col, opp)
        if check_winner(tmp, opp):
            _ai_medium_log(f"source=block player={ai_player} seq='{sequence}' col={col + 1}")
            return col

    search_cols = _safe_columns(board, ai_player)
    safe_valid = search_cols[:]

    winner_label = _winner_label(ai_player)

    best_col = None
    best_meta = None
    try:
        stats = _db_next_move_stats(sequence, winner_label)
        best_col, best_meta = _pick_move_from_db_stats(stats, search_cols)
    except Exception as e:
        print(f"Erreur BDD stats dans ai_medium_with_seq: {e}")

    if best_col is not None and best_meta is not None and best_meta["wins"] > 0:
        _ai_medium_log(
            f"source=db_stats player={ai_player} seq='{sequence}' col={best_col + 1} "
            f"win_rate={round(best_meta['win_rate'] * 100)}% wins={best_meta['wins']}/{best_meta['samples']} "
            f"searched={len(search_cols)} safe={len(safe_valid)}"
        )
        return best_col

    try:
        candidates = _db_candidates_for_next_move(sequence, winner_label)
        seen_candidates = set()
        best_win_in = float('inf')
        best_source = None
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

        if best_col is not None:
            _ai_medium_log(
                f"source={best_source or 'db'} player={ai_player} seq='{sequence}' col={best_col + 1} win_in={best_win_in} "
                f"searched={len(search_cols)} safe={len(safe_valid)}"
            )
            return best_col
    except Exception as e:
        print(f"Erreur BDD fallback dans ai_medium_with_seq: {e}")

    preferred = _order_cols(search_cols)
    seed = sum(ord(ch) for ch in sequence) if sequence else 0
    rnd = random.Random(seed)
    choice = rnd.choice(preferred[:min(3, len(preferred))])
    _ai_medium_log(
        f"source=fallback player={ai_player} seq='{sequence}' col={choice + 1} "
        f"searched={len(search_cols)} safe={len(safe_valid)}"
    )
    return choice


def _to_model_token(cell_value):
    if cell_value == RED:
        return MODEL_RED
    if cell_value == YELLOW:
        return MODEL_YELLOW
    return MODEL_EMPTY


class _ResultState:
    def __init__(self):
        self.winner = None
        self.draw = False
        self.finished = False


class _MiniModelAdapter:
    def __init__(self, grid, current_player):
        self.grid = [row[:] for row in grid]
        self.rows = len(self.grid)
        self.cols = len(self.grid[0]) if self.rows else 0
        self.current_player = current_player
        self.result = _ResultState()
        self.recompute_result()

    def valid_cols(self):
        return [c for c in range(self.cols) if self.grid[0][c] == MODEL_EMPTY]

    def play(self, col):
        for r in range(self.rows - 1, -1, -1):
            if self.grid[r][col] == MODEL_EMPTY:
                self.grid[r][col] = self.current_player
                row = r
                # Alterner le joueur AVANT de recalculer le résultat
                self.current_player = MODEL_RED if self.current_player == MODEL_YELLOW else MODEL_YELLOW
                self.recompute_result()
                return row
        return None

    def undo(self, col, row):
        self.grid[row][col] = MODEL_EMPTY
        # Revenir au joueur précédent
        self.current_player = MODEL_RED if self.current_player == MODEL_YELLOW else MODEL_YELLOW
        self.recompute_result()

    def copy(self):
        """Copie profonde complète de l'état."""
        model = _MiniModelAdapter.__new__(_MiniModelAdapter)
        model.grid = [row[:] for row in self.grid]
        model.rows = self.rows
        model.cols = self.cols
        model.current_player = self.current_player
        model.result = _ResultState()
        model.result.winner = self.result.winner
        model.result.draw = self.result.draw
        model.result.finished = self.result.finished
        return model

    def recompute_result(self):
        self.result.winner = None
        self.result.draw = False
        self.result.finished = False

        has_empty = False
        for r in range(self.rows):
            for c in range(self.cols):
                if self.grid[r][c] == MODEL_EMPTY:
                    has_empty = True
                    continue
                if self._check_victory(r, c):
                    self.result.winner = self.grid[r][c]
                    self.result.finished = True
                    return

        if not has_empty:
            self.result.draw = True
            self.result.finished = True

    def _check_victory(self, row, col):
        token = self.grid[row][col]
        if token == MODEL_EMPTY:
            return False
        directions = [(1, 0), (0, 1), (1, 1), (1, -1)]
        for dr, dc in directions:
            count = 1
            for sign in (1, -1):
                r = row + dr * sign
                c = col + dc * sign
                while 0 <= r < self.rows and 0 <= c < self.cols and self.grid[r][c] == token:
                    count += 1
                    r += dr * sign
                    c += dc * sign
            if count >= 4:
                return True
        return False


def _best_move_from_minimax(board, ai_player, depth):
    model_player = MODEL_RED if ai_player == RED else MODEL_YELLOW
    model_grid = [[_to_model_token(cell) for cell in row] for row in board]
    model = _MiniModelAdapter(model_grid, model_player)

    # Vérifier que le modèle est dans l'état correct (pas déjà terminé)
    if model.result.finished:
        valid = get_valid_cols(board)
        return valid[0] if valid else None

    col, score = best_move(model, max(1, int(depth)), model_player)

    if col is None:
        valid = get_valid_cols(board)
        return valid[0] if valid else None

    return col




def ai_hard(board, ai_player, depth=4):
    return _best_move_from_minimax(board, ai_player, depth)


def ai_hard_scores(board, ai_player, depth=4):
    model_player = MODEL_RED if ai_player == RED else MODEL_YELLOW
    model_grid = [[_to_model_token(cell) for cell in row] for row in board]
    model = _MiniModelAdapter(model_grid, model_player)
    return analyze_moves(model, max(1, int(depth)), model_player)

