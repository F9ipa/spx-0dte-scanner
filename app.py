import os
import json
import time
import threading
from collections import deque

from flask import Flask, jsonify, render_template
import websocket


app = Flask(__name__)


# ============================================================
# CONFIG
# ============================================================

TOKEN = os.getenv("ODTE_TOKEN", "").strip()

WS_URL = os.getenv(
    "ODTE_WS_URL",
    "wss://api.0dtespx.com/__ws"
)

ORIGIN = os.getenv(
    "ODTE_ORIGIN",
    "https://www.0dtespx.com"
)


# ============================================================
# GLOBAL STATE
# ============================================================

state = {
    "connected": False,
    "authenticated": False,

    "status": "STARTING",
    "error": None,

    "last_update": None,

    "spx": None,
    "vix": None,
    "expected_move": None,

    "calls": [],
    "puts": [],

    "best_call": None,
    "best_put": None,

    "raw_last_message": None
}

lock = threading.Lock()

spx_history = deque(maxlen=300)


# ============================================================
# HELPERS
# ============================================================

def to_float(value):

    try:
        return float(value)

    except Exception:

        return None


def clamp(value, minimum=0, maximum=100):

    return max(
        minimum,
        min(maximum, value)
    )


def spread_percent(bid, ask):

    if bid is None or ask is None:
        return None

    if bid <= 0 or ask <= 0:
        return None

    mid = (bid + ask) / 2

    if mid <= 0:
        return None

    return ((ask - bid) / mid) * 100


# ============================================================
# OPTION CHAIN NORMALIZER
# ============================================================

def normalize_chain(message):

    payload = message.get("payload")

    if payload is None:
        return []


    # --------------------------------------------------------
    # Normal format
    # --------------------------------------------------------

    if isinstance(payload, list):

        rows = payload

        result = []

        for row in rows:

            if not isinstance(row, dict):
                continue

            strike = to_float(
                row.get("strike")
            )

            if strike is None:
                continue


            call = row.get("call") or {}
            put = row.get("put") or {}


            result.append({

                "strike": strike,

                "call": {

                    "bid": to_float(
                        call.get("bid")
                    ),

                    "ask": to_float(
                        call.get("ask")
                    ),

                    "delta": to_float(
                        call.get("delta")
                    )
                },

                "put": {

                    "bid": to_float(
                        put.get("bid")
                    ),

                    "ask": to_float(
                        put.get("ask")
                    ),

                    "delta": to_float(
                        put.get("delta")
                    )
                }
            })


        return result


    # --------------------------------------------------------
    # Columnar format
    # --------------------------------------------------------

    if isinstance(payload, dict):

        if "k" not in payload:
            return []


        strikes = payload.get("k", [])

        call_bid = payload.get("cb", [])
        call_ask = payload.get("ca", [])
        call_delta = payload.get("cd", [])

        put_bid = payload.get("pb", [])
        put_ask = payload.get("pa", [])
        put_delta = payload.get("pd", [])


        result = []


        for i, strike in enumerate(strikes):

            try:

                result.append({

                    "strike": float(strike),

                    "call": {

                        "bid": (
                            float(call_bid[i]) / 100
                            if i < len(call_bid)
                            else None
                        ),

                        "ask": (
                            float(call_ask[i]) / 100
                            if i < len(call_ask)
                            else None
                        ),

                        "delta": (
                            float(call_delta[i]) / 10000
                            if i < len(call_delta)
                            else None
                        )
                    },

                    "put": {

                        "bid": (
                            float(put_bid[i]) / 100
                            if i < len(put_bid)
                            else None
                        ),

                        "ask": (
                            float(put_ask[i]) / 100
                            if i < len(put_ask)
                            else None
                        ),

                        "delta": (
                            float(put_delta[i]) / 10000
                            if i < len(put_delta)
                            else None
                        )
                    }
                })

            except Exception:

                continue


        return result


    return []


# ============================================================
# AGGREGATE DATA
# ============================================================

def parse_aggregate(message):

    payload = message.get("payload")

    if not isinstance(payload, dict):
        return


    spx = None
    vix = None
    expected_move = None


    # Direct fields

    for key in [
        "spx",
        "SPX",
        "price",
        "index_price"
    ]:

        if key in payload:

            value = to_float(
                payload[key]
            )

            if value is not None:

                spx = value
                break


    for key in [
        "vix",
        "VIX"
    ]:

        if key in payload:

            value = to_float(
                payload[key]
            )

            if value is not None:

                vix = value
                break


    for key in [
        "spxExpectedMove",
        "expected_move",
        "expectedMove"
    ]:

        if key in payload:

            value = to_float(
                payload[key]
            )

            if value is not None:

                expected_move = value
                break


    with lock:

        if spx is not None:

            state["spx"] = spx

            spx_history.append(
                (time.time(), spx)
            )


        if vix is not None:

            state["vix"] = vix


        if expected_move is not None:

            state["expected_move"] = expected_move


# ============================================================
# MOMENTUM
# ============================================================

def momentum_score(side):

    if len(spx_history) < 10:

        return 50


    old_price = spx_history[-10][1]
    new_price = spx_history[-1][1]


    movement = new_price - old_price


    with lock:

        spx = state["spx"]


    if spx is None:
        return 50


    threshold = max(
        spx * 0.00025,
        0.5
    )


    if side == "CALL":

        score = 50 + (
            movement / threshold
        ) * 25

    else:

        score = 50 - (
            movement / threshold
        ) * 25


    return clamp(score)


# ============================================================
# CONTRACT SCORE
# ============================================================

def calculate_score(
    side,
    strike,
    delta,
    bid,
    ask
):

    with lock:

        spx = state["spx"]
        vix = state["vix"]
        expected_move = state["expected_move"]


    if (
        spx is None
        or delta is None
        or bid is None
        or ask is None
    ):

        return None


    if bid <= 0 or ask <= 0:

        return None


    # --------------------------------------------------------
    # Delta
    # Target ~0.50 - 0.65 for directional 0DTE
    # --------------------------------------------------------

    abs_delta = abs(delta)


    delta_score = clamp(
        100 - abs(abs_delta - 0.55) * 250
    )


    # --------------------------------------------------------
    # Distance
    # --------------------------------------------------------

    distance = abs(
        strike - spx
    )


    if expected_move is None or expected_move <= 0:

        expected_move = spx * 0.005


    distance_ratio = (
        distance / expected_move
    )


    distance_score = clamp(
        100 - distance_ratio * 55
    )


    # --------------------------------------------------------
    # Spread
    # --------------------------------------------------------

    spread = spread_percent(
        bid,
        ask
    )


    if spread is None:

        spread_score = 0

    else:

        spread_score = clamp(
            100 - spread * 18
        )


    # --------------------------------------------------------
    # Premium
    # --------------------------------------------------------

    mid = (
        bid + ask
    ) / 2


    premium_score = clamp(
        35 + min(mid, 25) * 2.6
    )


    # --------------------------------------------------------
    # Momentum
    # --------------------------------------------------------

    momentum = momentum_score(
        side
    )


    # --------------------------------------------------------
    # VIX
    # --------------------------------------------------------

    if vix is None:

        vix_score = 60

    else:

        vix_score = clamp(
            95 - max(
                0,
                vix - 13
            ) * 4
        )


    # ========================================================
    # FINAL SCORE
    # ========================================================

    score = (

        delta_score * 0.28

        +

        momentum * 0.22

        +

        distance_score * 0.15

        +

        spread_score * 0.15

        +

        premium_score * 0.10

        +

        vix_score * 0.10

    )


    return round(
        score,
        1
    )


# ============================================================
# BUILD CONTRACTS
# ============================================================

def build_contracts(chain):

    calls = []
    puts = []


    for row in chain:

        strike = row["strike"]


        # ====================================================
        # CALL
        # ====================================================

        call = row["call"]


        call_score = calculate_score(

            "CALL",

            strike,

            call["delta"],

            call["bid"],

            call["ask"]

        )


        if call_score is not None:

            calls.append({

                "side": "CALL",

                "strike": strike,

                "bid": call["bid"],

                "ask": call["ask"],

                "delta": call["delta"],

                "spread": spread_percent(
                    call["bid"],
                    call["ask"]
                ),

                "score": call_score
            })


        # ====================================================
        # PUT
        # ====================================================

        put = row["put"]


        put_score = calculate_score(

            "PUT",

            strike,

            put["delta"],

            put["bid"],

            put["ask"]

        )


        if put_score is not None:

            puts.append({

                "side": "PUT",

                "strike": strike,

                "bid": put["bid"],

                "ask": put["ask"],

                "delta": put["delta"],

                "spread": spread_percent(
                    put["bid"],
                    put["ask"]
                ),

                "score": put_score
            })


    calls.sort(
        key=lambda x: x["score"],
        reverse=True
    )


    puts.sort(
        key=lambda x: x["score"],
        reverse=True
    )


    return calls[:3], puts[:3]


# ============================================================
# WEBSOCKET
# ============================================================

def on_open(ws):

    with lock:

        state["connected"] = True

        state["status"] = "AUTHENTICATING"


    # --------------------------------------------------------
    # Authentication
    # --------------------------------------------------------

    if TOKEN:

        ws.send(
            json.dumps({
                "token": TOKEN
            })
        )

    else:

        ws.send(
            json.dumps({
                "auth": "public"
            })
        )


    time.sleep(0.5)


    # --------------------------------------------------------
    # Subscribe
    # --------------------------------------------------------

    ws.send(
        json.dumps({

            "action": "subscribe",

            "channels": [

                "live_aggregate_data",

                "live_option_chain"

            ]

        })
    )


def on_message(ws, message):

    try:

        data = json.loads(
            message
        )


        channel = data.get(
            "channel"
        )


        with lock:

            state["last_update"] = time.time()

            state["raw_last_message"] = data


        # ====================================================
        # Aggregate
        # ====================================================

        if channel == "live_aggregate_data":

            parse_aggregate(
                data
            )


        # ====================================================
        # Option Chain
        # ====================================================

        elif channel == "live_option_chain":

            chain = normalize_chain(
                data
            )


            calls, puts = build_contracts(
                chain
            )


            with lock:

                state["calls"] = calls

                state["puts"] = puts

                state["best_call"] = (
                    calls[0]
                    if calls
                    else None
                )

                state["best_put"] = (
                    puts[0]
                    if puts
                    else None
                )


    except Exception as error:

        with lock:

            state["error"] = str(
                error
            )


def on_error(ws, error):

    with lock:

        state["connected"] = False

        state["error"] = str(
            error
        )

        state["status"] = "WS ERROR"


def on_close(ws, code, message):

    with lock:

        state["connected"] = False

        state["status"] = "RECONNECTING"


# ============================================================
# WS THREAD
# ============================================================

def websocket_loop():

    while True:

        try:

            ws = websocket.WebSocketApp(

                WS_URL,

                on_open=on_open,

                on_message=on_message,

                on_error=on_error,

                on_close=on_close

            )


            ws.run_forever(

                origin=ORIGIN,

                ping_interval=20,

                ping_timeout=10

            )


        except Exception as error:

            with lock:

                state["connected"] = False

                state["error"] = str(
                    error
                )


        time.sleep(3)


# ============================================================
# API
# ============================================================

@app.route("/")
def home():

    return render_template(
        "index.html"
    )


@app.route("/api/state")
def api_state():

    with lock:

        result = dict(
            state
        )


    if result["last_update"]:

        result["age"] = round(

            time.time()
            - result["last_update"],

            1

        )

    else:

        result["age"] = None


    return jsonify(
        result
    )


# ============================================================
# START
# ============================================================

threading.Thread(
    target=websocket_loop,
    daemon=True
).start()


if __name__ == "__main__":

    app.run(

        host="0.0.0.0",

        port=int(
            os.getenv(
                "PORT",
                "10000"
            )
        )

    )
