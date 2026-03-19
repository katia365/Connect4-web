import math, random, copy, os

# Charge le .env en local (ignoré sur Render qui a ses propres variables)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # python-dotenv pas installé -> pas grave sur Render

try:
    import psycopg2
except ImportError:
    psycopg2 = None

DATABASE_URL = os.environ.get("DATABASE_URL")
print(f"DATABASE_URL chargé: {DATABASE_URL is not None} → {DATABASE_URL[:20] if DATABASE_URL else 'None'}")

ROWS = 9
COLS = 9
EMPTY = 0
RED = 1
YELLOW = 2

def create_board():
    return [[EMPTY]*COLS for _ in range(ROWS)]

def is_valid_col(board, col):
    return 0 <= col < COLS and board[0][col] == EMPTY

def get_valid_cols(board):
    return [c for c in range(COLS) if is_valid_col(board, c)]

def drop_piece(board, col, player):
    for r in range(ROWS-1, -1, -1):
        if board[r][col] == EMPTY:
            board[r][col] = player
            return r
    return -1

def check_winner(board, player):
    for r in range(ROWS):
        for c in range(COLS-3):
            if all(board[r][c+i]==player for i in range(4)):
                return True
    for r in range(ROWS-3):
        for c in range(COLS):
            if all(board[r+i][c]==player for i in range(4)):
                return True
    for r in range(3, ROWS):
        for c in range(COLS-3):
            if all(board[r-i][c+i]==player for i in range(4)):
                return True
    for r in range(ROWS-3):
        for c in range(COLS-3):
            if all(board[r+i][c+i]==player for i in range(4)):
                return True
    return False

def is_draw(board):
    return all(board[0][c] != EMPTY for c in range(COLS))

def score_window(window, player):
    opp = RED if player==YELLOW else YELLOW
    s = 0
    if window.count(player)==4: s+=100
    elif window.count(player)==3 and window.count(EMPTY)==1: s+=5
    elif window.count(player)==2 and window.count(EMPTY)==2: s+=2
    if window.count(opp)==3 and window.count(EMPTY)==1: s-=4
    return s

def score_board(board, player):
    score = 0
    mid = COLS//2
    center = [board[r][mid] for r in range(ROWS)]
    score += center.count(player)*3
    for r in range(ROWS):
        for c in range(COLS-3):
            score += score_window([board[r][c+i] for i in range(4)], player)
    for r in range(ROWS-3):
        for c in range(COLS):
            score += score_window([board[r+i][c] for i in range(4)], player)
    for r in range(3, ROWS):
        for c in range(COLS-3):
            score += score_window([board[r-i][c+i] for i in range(4)], player)
    for r in range(ROWS-3):
        for c in range(COLS-3):
            score += score_window([board[r+i][c+i] for i in range(4)], player)
    return score

def minimax(board, depth, alpha, beta, maximizing, player):
    opp = RED if player==YELLOW else YELLOW
    valid = get_valid_cols(board)
    if check_winner(board, player): return None, 1000000
    if check_winner(board, opp):   return None, -1000000
    if not valid or depth==0:      return None, score_board(board, player)

    if maximizing:
        best, best_col = -math.inf, random.choice(valid)
        for col in valid:
            b = copy.deepcopy(board)
            drop_piece(b, col, player)
            _, sc = minimax(b, depth-1, alpha, beta, False, player)
            if sc > best: best, best_col = sc, col
            alpha = max(alpha, best)
            if alpha >= beta: break
        return best_col, best
    else:
        best, best_col = math.inf, random.choice(valid)
        for col in valid:
            b = copy.deepcopy(board)
            drop_piece(b, col, opp)
            _, sc = minimax(b, depth-1, alpha, beta, True, player)
            if sc < best: best, best_col = sc, col
            beta = min(beta, best)
            if alpha >= beta: break
        return best_col, best

def ai_easy(board):
    valid = get_valid_cols(board)
    return random.choice(valid) if valid else None

def ai_medium_with_seq(board, player, sequence_played):
    valid = get_valid_cols(board)
    if not valid:
        return None

    try:
        if psycopg2 is None or DATABASE_URL is None:
            print("MEDIUM: pas de BDD, random")
            return random.choice(valid)
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        prefix = sequence_played
        cur.execute("""
            SELECT sequence_coups FROM parties
            WHERE sequence_coups LIKE %s AND statut = 'TERMINEE'
            LIMIT 500
        """, (prefix + '%',))
        rows = cur.fetchall()
        conn.close()

        if not rows:
            print(f"MEDIUM: aucune partie trouvée pour séquence '{prefix}', random")
            return random.choice(valid)

        next_col_counts = {}
        for (seq,) in rows:
            if len(seq) > len(prefix):
                next_move = int(seq[len(prefix)]) - 1
                if next_move in valid:
                    next_col_counts[next_move] = next_col_counts.get(next_move, 0) + 1

        if not next_col_counts:
            print(f"MEDIUM: séquence trouvée mais pas de coup valide, random")
            return random.choice(valid)

        best = max(next_col_counts, key=next_col_counts.get)
        print(f"MEDIUM: BDD utilisée, {len(rows)} parties, coup={best}")
        return best

    except Exception as e:
        print(f"MEDIUM: erreur BDD ({e}), random")
        return random.choice(valid)

def ai_hard(board, player, depth=4):
    col, _ = minimax(board, depth, -math.inf, math.inf, True, player)
    return col