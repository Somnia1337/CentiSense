"""
CentiSense — Chess Position Evaluation Trainer

A web application that presents random chess positions from a database,
lets users judge the evaluation, and reveals Stockfish 19 analysis.
"""

import logging
import random
import re
import select
import subprocess
import threading
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import chess
import chess.pgn
import chess.svg
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

PGN_FILE = Path(__file__).parent / "database.pgn"
POSITIONS_FILE = Path(__file__).parent / "positions.txt"
STOCKFISH_PATH = "stockfish"
STOCKFISH_DEPTH = 20
STOCKFISH_TIMEOUT = 12.0  # seconds per position

# Pieces must be between MIN_PIECES and MAX_PIECES (inclusive)
MIN_PIECES = 10
MAX_PIECES = 26
# Only consider positions at or after MIN_PLY (skip opening)
MIN_PLY = 12

# ---------------------------------------------------------------------------
# Evaluation categories
# ---------------------------------------------------------------------------

CATEGORIES = [
    ("white_winning", "White winning", "White is winning", 300, 10_000),
    ("white_big", "White clear advantage", "White has big advantage", 150, 299),
    ("white_slight", "White slight edge", "White has slight advantage", 50, 149),
    ("equal", "Equal", "Equal position", -49, 49),
    ("black_slight", "Black slight edge", "Black has slight advantage", -149, -50),
    ("black_big", "Black clear advantage", "Black has big advantage", -299, -150),
    ("black_winning", "Black winning", "Black is winning", -10_000, -300),
]

CATEGORY_BY_KEY = {cat[0]: cat for cat in CATEGORIES}


def classify_eval(cp: int) -> str:
    """Return the category key for a centipawn score (white-positive)."""
    for key, _, _, lo, hi in CATEGORIES:
        if lo <= cp <= hi:
            return key
    return "equal"


def evaluation_category(cp: int, mate: int | None) -> str:
    """Classify a white-perspective evaluation, treating any forced mate as decisive."""
    if mate is not None:
        return classify_eval(10_000 if mate > 0 else -10_000)
    return classify_eval(cp)


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger("CentiSense")

# ---------------------------------------------------------------------------
# Lifespan (must be defined before FastAPI app creation)
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown lifecycle."""

    # Startup
    def init_db():
        build_position_database()

    db_thread = threading.Thread(target=init_db, daemon=True)
    db_thread.start()
    engine.start()

    yield

    # Shutdown
    engine.stop()


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(title="CentiSense", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)

# Serve static files from /static
static_dir = Path(__file__).parent / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


@app.get("/", response_class=HTMLResponse)
async def index():
    """Serve the main page."""
    index_path = static_dir / "index.html"
    if index_path.exists():
        return HTMLResponse(index_path.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>CentiSense</h1><p>index.html not found.</p>")


# ---------------------------------------------------------------------------
# Stockfish engine management
# ---------------------------------------------------------------------------


def parse_stockfish_line(line: str, state: dict) -> bool:
    """Parse one Stockfish UCI output line into ``state`` in place.

    ``state`` is updated with any of ``cp``, ``mate``, ``bestmove`` or ``pv``
    present on the line. Returns True when the line is the terminal
    ``bestmove`` line that ends a search.
    """
    line = line.strip()

    match = re.match(r"^bestmove\s+(\S+)", line)
    if match:
        state["bestmove"] = match.group(1)
        return True

    if "score cp" in line:
        match = re.search(r"score cp\s+(-?\d+)", line)
        if match:
            state["cp"] = int(match.group(1))
    elif "score mate" in line:
        match = re.search(r"score mate\s+(-?\d+)", line)
        if match:
            state["mate"] = int(match.group(1))

    pv_match = re.search(r"\bpv\s+(.*)", line)
    if pv_match:
        state["pv"] = pv_match.group(1).split()

    return False


class StockfishEngine:
    """Manages a persistent Stockfish 19 process."""

    def __init__(self, path: str = STOCKFISH_PATH):
        self.path = path
        self.process: subprocess.Popen | None = None
        self.lock = threading.Lock()

    def start(self):
        log.info("Starting Stockfish engine...")
        try:
            self.process = subprocess.Popen(
                [self.path],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
            # Init UCI mode
            self._send("uci")
            self._wait_for("uciok", timeout=5.0)
            # Set threads and hash
            self._send("setoption name Threads value 2")
            self._send("setoption name Hash value 64")
            self._send("isready")
            self._wait_for("readyok", timeout=5.0)
            log.info("Stockfish engine ready.")
        except Exception as e:
            log.error(f"Failed to start Stockfish: {e}")
            self.process = None

    def stop(self):
        if self.process:
            log.info("Stopping Stockfish engine...")
            try:
                self._send("quit")
                self.process.wait(timeout=3)
            except Exception:
                self.process.kill()
            self.process = None

    def _send(self, cmd: str):
        if self.process and self.process.stdin:
            self.process.stdin.write(cmd + "\n")
            self.process.stdin.flush()

    def _wait_for(self, keyword: str, timeout: float) -> bool:
        """Read lines until keyword found or timeout."""
        if not self.process or not self.process.stdout:
            return False
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            line = self._read_line(deadline - time.monotonic())
            if line is None:
                break
            if keyword in line:
                return True
        return False

    def _read_line(self, timeout: float) -> str | None:
        """Read one line from the engine, returning None on timeout or EOF."""
        if not self.process or not self.process.stdout:
            return None
        stdout = self.process.stdout
        ready, _, _ = select.select([stdout], [], [], timeout)
        if not ready:
            return None
        line = stdout.readline()
        return line if line else None

    def _drain(self, timeout: float) -> None:
        """Discard pending engine output, waiting at most ``timeout`` seconds."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._read_line(deadline - time.monotonic()) is None:
                break

    def _error_result(self, error: str) -> dict:
        """Return the standard dict shape for a failed evaluation."""
        return {"cp": 0, "mate": None, "bestmove": None, "pv": [], "error": error}

    def evaluate(
        self, fen: str, depth: int = STOCKFISH_DEPTH, timeout: float = STOCKFISH_TIMEOUT
    ) -> dict:
        """
        Evaluate a position. Returns dict with:
          - cp: centipawn score (positive = white better)
          - mate: mate in N moves (None if no forced mate found within depth)
          - bestmove: UCI best move
          - pv: principal variation (list of UCI moves)

        On failure, the dict also contains an ``error`` key describing what
        went wrong (engine unavailable, timeout, or unexpected termination).
        """
        with self.lock:
            if not self.process or self.process.poll() is not None:
                log.warning("Stockfish not running, attempting restart...")
                self.start()
                if not self.process or self.process.poll() is not None:
                    return self._error_result("engine unavailable")

            # Process is guaranteed alive here; stdout must be connected.
            assert self.process.stdout is not None

            # Cancel any in-progress search (no-op if idle) and drain stale
            # output from a previous timeout. Without this, leftover
            # "bestmove" / "info" lines from a prior search would pollute the
            # pipe and cause the next evaluation to return wrong scores.
            self._send("stop")
            self._drain(0.2)

            # Send position and go command
            self._send(f"position fen {fen}")
            self._send(f"go depth {depth}")

            state: dict = {"cp": 0, "mate": None, "bestmove": None, "pv": []}
            deadline = time.monotonic() + timeout

            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._send("stop")
                    self._drain(0.5)
                    return self._error_result(f"timed out after {timeout:.0f}s")

                line = self._read_line(remaining)
                if line is None:
                    # EOF means the process died; otherwise we simply did not
                    # receive a complete line before the deadline (a timeout).
                    if self.process.poll() is not None:
                        return self._error_result("engine terminated unexpectedly")
                    self._send("stop")
                    self._drain(0.5)
                    return self._error_result(f"timed out after {timeout:.0f}s")

                if parse_stockfish_line(line, state):
                    break  # bestmove line terminates the search

            return {
                "cp": state["cp"],
                "mate": state["mate"],
                "bestmove": state["bestmove"],
                "pv": state["pv"],
            }


# Global engine instance
engine = StockfishEngine()


# ---------------------------------------------------------------------------
# Position database
# ---------------------------------------------------------------------------

# In-memory list of FENs to serve
positions: list[str] = []


def build_position_database():
    """Parse the PGN file and extract qualifying positions."""
    global positions

    # If we already have a pre-built positions file, load it
    if POSITIONS_FILE.exists():
        log.info(f"Loading positions from {POSITIONS_FILE}...")
        positions = [
            line.strip()
            for line in POSITIONS_FILE.read_text().splitlines()
            if line.strip()
        ]
        log.info(f"Loaded {len(positions)} positions from cache.")
        return

    if not PGN_FILE.exists():
        log.warning(
            f"No PGN database found at {PGN_FILE}. Please download a PGN file first."
        )
        return

    log.info(f"Parsing PGN file: {PGN_FILE}...")
    extracted = []
    game_count = 0

    with open(PGN_FILE, encoding="utf-8", errors="replace") as f:
        while True:
            try:
                game = chess.pgn.read_game(f)
            except Exception as e:
                log.warning(f"Error parsing game: {e}")
                continue

            if game is None:
                break

            game_count += 1
            if game_count % 2000 == 0:
                log.info(
                    f"  Parsed {game_count} games, collected "
                    f"{len(extracted)} positions..."
                )

            # Skip non-standard variants (Chess960, etc.)
            variant = game.headers.get("Variant", "Standard")
            if variant != "Standard":
                continue

            board = game.board()
            node = game

            move_num = 0
            while node := node.variations:
                node = node[0]  # follow main line
                move_num += 1
                try:
                    board.push(node.move)
                except ValueError:
                    break

                if move_num < MIN_PLY:
                    continue

                # Count pieces
                piece_count = bin(board.occupied).count("1")
                if piece_count < MIN_PIECES or piece_count > MAX_PIECES:
                    continue

                # Skip positions with very few legal moves (fast count)
                if board.legal_moves.count() < 5:
                    continue

                extracted.append(board.fen())

            # Downsample periodically to keep memory usage reasonable
            if len(extracted) > 80_000:
                extracted = random.sample(extracted, 50_000)

    if extracted:
        # Save to cache file for faster subsequent loads
        POSITIONS_FILE.write_text("\n".join(extracted), encoding="utf-8")
        positions = extracted
        log.info(
            f"Parsed {game_count} games, built database with "
            f"{len(positions)} positions."
        )
    else:
        log.warning("No qualifying positions extracted from PGN.")
        positions = []


# ---------------------------------------------------------------------------
# Session management (in-memory)
# ---------------------------------------------------------------------------

# session_id -> {"fen": str, "cp": int, "mate": int|None, "bestmove": str, "pv": list}
sessions: dict[str, dict] = {}


def render_board(fen: str) -> tuple[chess.Board, str]:
    """Render an SVG board for the given FEN, flipped for the side to move."""
    board = chess.Board(fen)
    flipped = not board.turn
    svg_data = chess.svg.board(board=board, size=400, flipped=flipped)
    return board, svg_data


def apply_move(fen: str, move_uci: str) -> dict:
    """Validate and apply a UCI move to a position, returning the new state.

    Raises HTTPException(400) for missing/invalid input or illegal moves.
    The returned dict contains the new FEN, SVG, side to move and legal moves.
    """
    if not isinstance(fen, str) or not fen:
        raise HTTPException(status_code=400, detail="Missing or invalid FEN.")
    if not isinstance(move_uci, str) or not move_uci:
        raise HTTPException(status_code=400, detail="Missing or invalid move.")

    try:
        board = chess.Board(fen)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid FEN.") from None

    try:
        move = chess.Move.from_uci(move_uci)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid move notation.") from None

    if move not in board.legal_moves:
        raise HTTPException(status_code=400, detail="Illegal move.")

    san = board.san(move)
    board.push(move)
    _, svg_data = render_board(board.fen())

    return {
        "fen": board.fen(),
        "svg": svg_data,
        "side_to_move": "white" if board.turn else "black",
        "legal_moves": [m.uci() for m in board.legal_moves],
        "san": san,
    }


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------


@app.get("/api/health")
async def health():
    """Health check."""
    return {
        "status": "ok",
        "positions_loaded": len(positions),
        "stockfish_running": engine.process is not None
        and engine.process.poll() is None,
    }


@app.get("/api/position")
async def get_position():
    """Return a random position for evaluation."""
    if not positions:
        # Fallback: generate a built-in position
        fen = chess.STARTING_FEN
    else:
        # Pick a random position and verify it's valid
        for _ in range(20):
            fen = random.choice(positions)
            try:
                board = chess.Board(fen)
                if board.is_game_over():
                    continue
                # Ensure there are enough pieces
                piece_count = bin(board.occupied).count("1")
                if piece_count < MIN_PIECES:
                    continue
                break
            except ValueError:
                continue
        else:
            fen = random.choice(positions)

    # Generate SVG board
    try:
        board, svg_data = render_board(fen)
    except ValueError:
        board = chess.Board()
        svg_data = chess.svg.board(board=board, size=400)

    # Evaluate with Stockfish
    eval_result = engine.evaluate(fen)
    if eval_result.get("error"):
        log.error(f"Stockfish evaluation failed: {eval_result['error']}")
        raise HTTPException(
            status_code=503,
            detail="Analysis engine is busy or unavailable. Please try again.",
        )

    cp = eval_result.get("cp", 0)
    mate = eval_result.get("mate")
    bestmove = eval_result.get("bestmove")
    pv = eval_result.get("pv", [])

    # Stockfish UCI reports cp/mate from the side-to-move perspective.
    # Convert to white's perspective for consistent classification.
    if not board.turn:  # black to move
        cp = -cp
        if mate is not None:
            mate = -mate

    # Convert bestmove to SAN
    bestmove_san = None
    if bestmove:
        try:
            bestmove_san = board.san(chess.Move.from_uci(bestmove))
        except Exception:
            bestmove_san = bestmove

    # Convert PV to SAN
    pv_san = []
    temp_board = board.copy()
    for uci_move in pv:
        try:
            move = chess.Move.from_uci(uci_move)
            pv_san.append(temp_board.san(move))
            temp_board.push(move)
        except Exception:
            pv_san.append(uci_move)
            break

    # Create session
    session_id = uuid.uuid4().hex
    sessions[session_id] = {
        "fen": fen,
        "cp": cp,
        "mate": mate,
        "bestmove": bestmove_san,
        "pv": pv_san,
        "category": evaluation_category(cp, mate),
    }

    # Clean old sessions (keep max 1000)
    if len(sessions) > 1000:
        keys = list(sessions.keys())
        for old_key in keys[: len(keys) - 1000]:
            del sessions[old_key]

    # Determine side to move
    side_to_move = "white" if board.turn else "black"

    return {
        "session_id": session_id,
        "fen": fen,
        "svg": svg_data,
        "side_to_move": side_to_move,
        "legal_moves": [move.uci() for move in board.legal_moves],
    }


@app.post("/api/move")
async def make_move(request: Request):
    """Apply a move to a position, validating legality server-side."""
    body = await request.json()
    return apply_move(body.get("fen"), body.get("move"))


@app.post("/api/guess")
async def submit_guess(request: Request):
    """Submit user's evaluation guess and get the correct answer."""
    body = await request.json()
    session_id = body.get("session_id")
    guess_category = body.get("category")

    if not session_id or session_id not in sessions:
        raise HTTPException(
            status_code=400,
            detail="Invalid or expired session. Please request a new position.",
        )

    if guess_category not in CATEGORY_BY_KEY:
        raise HTTPException(
            status_code=400, detail=f"Invalid category: {guess_category}"
        )

    session = sessions[session_id]
    correct_category = session["category"]
    is_correct = guess_category == correct_category

    # Format the evaluation text
    cp = session["cp"]
    mate = session["mate"]
    if mate is not None:
        if mate > 0:
            eval_text = f"White M{mate} (White mates in {mate})"
        else:
            eval_text = f"Black M{abs(mate)} (Black mates in {abs(mate)})"
    else:
        abs_cp = abs(cp)
        if cp > 0:
            eval_text = f"+{cp / 100:.2f} (white +{abs_cp / 100:.2f})"
        elif cp < 0:
            eval_text = f"{cp / 100:.2f} (black +{abs_cp / 100:.2f})"
        else:
            eval_text = "0.00 (dead equal)"

    # Determine the correct category info
    cat_info = CATEGORY_BY_KEY[correct_category]
    correct_label = cat_info[1]

    # Best moves text
    bestmove = session.get("bestmove") or "N/A"
    pv = session.get("pv", [])

    result = {
        "correct": is_correct,
        "guess_category": guess_category,
        "guess_label": CATEGORY_BY_KEY.get(guess_category, ("", "未知", ""))[1],
        "correct_category": correct_category,
        "correct_label": correct_label,
        "eval_cp": cp,
        "eval_mate": mate,
        "eval_text": eval_text,
        "bestmove": bestmove,
        "pv": pv[:5],  # Show up to 5 PV moves
    }

    # Clean up session
    del sessions[session_id]

    return result


# ---------------------------------------------------------------------------
# Main entry point (for `uv run` or direct execution)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8080, reload=False, log_level="info")
