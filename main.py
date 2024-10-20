from fastapi import FastAPI, Request, Depends, BackgroundTasks
from sqlalchemy.orm import Session
import models
from database import SessionLocal, engine
import uvicorn
from xmr_rate import XMRRate
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse
import jwt
import asyncio
from hashlib import sha256
import json
from pgplogin import PGPLogin
from deposit import Deposit
from withdraw import Withdraw
import base64
from hotwallet_status import HotWalletStatus

NORMALIZER = 1000 * 1000 * 1000 * 1000


with open('config.json', 'r') as file:
    config = json.load(file)

with open('secrets.json', 'r') as file:
    server_secrets = json.load(file)

JWT_SECRET = server_secrets["JWT_SECRET"]

models.Base.metadata.create_all(bind=engine)

xmr_rate = XMRRate(config)
pgp_login = PGPLogin(server_secrets["CONF_PEPPER"])
deposit = Deposit()
withdraw = Withdraw(config)
hotwallet_status = HotWalletStatus(config)


app = FastAPI(docs_url=None,redoc_url=None,openapi_url=None)#for security all = None

app.mount("/static", StaticFiles(directory="static"), name="static")

template = Jinja2Templates(directory="templates").TemplateResponse


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def get_jwt_token(player_id):
    encoded_jwt = jwt.encode({"player_id": player_id}, JWT_SECRET, algorithm="HS256")
    return encoded_jwt

def get_player(db, request):
    encoded_jwt = request.cookies.get("auth")
    if not encoded_jwt:
        return None
    try:
        player_id = jwt.decode(encoded_jwt, JWT_SECRET, algorithms=["HS256"])["player_id"]
    except:
        return None

    player = models.Player.get(db, player_id)
    return player

class BackgroundRunner:
    def __init__(self):
        self.db = next(get_db())

    async def run_game(self):
        current_games = models.Game.get_current_games(self.db)
        if not current_games:
            models.Game.start_first_games(self.db)
        while True:
            active_games = models.Game.get_active_games(self.db)
            for game in active_games:
                print(game.state)
                game.next_state(self.db)
            await asyncio.sleep(1)

    async def run_delete_old_login_codes(self):
        while True:
            models.LoginCode.delete_expired(self.db, config["LOGIN_CODE_EXPIRE_TIME"])
            await asyncio.sleep(config["LOGIN_CODE_SWEEP_TIME"])

    async def run_check_deposits(self):
        while True:
            try:
                deposit.check_deposits(self.db)
            except Exception as e:
                print(str(e))
            await asyncio.sleep(config["DEPOSIT_SWEEP_TIME"])


runner = BackgroundRunner()

@app.on_event('startup')
async def app_startup():
    asyncio.create_task(runner.run_game())
    asyncio.create_task(runner.run_delete_old_login_codes())
    asyncio.create_task(runner.run_check_deposits())

@app.get("/")
async def path_root(request: Request, db: Session = Depends(get_db)):
    return RedirectResponse("/arcade")

@app.get("/arcade")
async def path_arcade(request: Request, db: Session = Depends(get_db)):
    player = get_player(db, request)
    return template(request=request, name="arcade.html", context={"page":"arcade","player":player})

@app.get("/arcade/iframe")
async def path_arcade_iframe(request: Request, db: Session = Depends(get_db)):
    player = get_player(db, request)
    current_games = models.Game.get_current_games(db)
    bal_display = request.cookies.get("bal_display", "XMR")
    return template(request=request, name="arcade-iframe.html", context={"page":"arcade-i","player":player,"current_games":current_games,"curr_xmr_rate":xmr_rate.check(),"db":db,"bal_display":bal_display,"config":config})

@app.get("/deposit")
async def path_deposit(request: Request, db: Session = Depends(get_db)):
    player = get_player(db, request)
    if not player:
        return RedirectResponse("/player/login")
    player.create_deposit_if_none(db)
    bal_display = request.cookies.get("bal_display", "XMR")
    return template(request=request, name="deposit.html", context={"page":"deposit","player":player, "get_qr_svg":deposit.get_qr_svg,"curr_xmr_rate":xmr_rate.check(),"bal_display":bal_display})

@app.get("/withdraw")
async def path_withdraw(request: Request, result: str = "", db: Session = Depends(get_db)):
    player = get_player(db, request)
    if not player:
        return RedirectResponse("/player/login", status_code=302)
    bal_display = request.cookies.get("bal_display", "XMR")
    return template(request=request, name="withdraw.html", context={"player":player,"curr_xmr_rate":xmr_rate.check(),"page":"withdraw","bal_display":bal_display,"result":base64.b64decode(result.encode()).decode()})

@app.post("/withdraw")
async def path_withdraw_post(request: Request, background_tasks: BackgroundTasks):
    db = next(get_db()) #fixes issues with background tasks
    player = get_player(db, request)
    if not player:
        return RedirectResponse("/user/login", status_code=302)

    form = await request.form()
    address = form.get("address")
    amount = form.get("amount")
    if float(amount) < 0.0001:
        transfer_final = 'amount has to be greater than 0.0001'
    else:
        original_amount = int(float(amount) * NORMALIZER)
        db_withdraw_request = models.WithdrawRequest.create(db, player, original_amount)
        if db_withdraw_request:
            background_tasks.add_task(withdraw.request_withdraw, db, db_withdraw_request, address)
            transfer_final = 'transfer requested'
        else:
            transfer_final = 'not enough balance'

    return RedirectResponse(f"/withdraw?result={base64.b64encode(transfer_final.encode()).decode()}", status_code=302)


@app.get("/player")
async def path_player(request: Request, db: Session = Depends(get_db)):
    player = get_player(db, request)
    if not player:
        return RedirectResponse("/player/login")
    bal_display = request.cookies.get("bal_display", "XMR")    
    return template(request=request, name="player.html", context={"page":"player","player":player,"curr_xmr_rate":xmr_rate.check(),"bal_display":bal_display})

@app.get("/player/login")
async def path_player_login(request: Request, db: Session = Depends(get_db)):
    player = get_player(db, request)
    if player:
        return RedirectResponse("/player")
    return template(request=request, name="player/login.html", context={"page":"player_login"})

@app.post("/player/login")
async def path_player_login_post(request: Request, db: Session = Depends(get_db)):
    player = get_player(db, request)
    if player:
        return RedirectResponse("/player", status_code=302)
    form = await request.form()
    public_pgp_key = form.get("public_pgp")
    if not public_pgp_key:
        return "No valid public pgp key provided"
    fingerprint, confirmation_code, encrypted_data = pgp_login.generate_encrypted_confirmation_code(public_pgp_key)
    if not fingerprint:
        return RedirectResponse("/player/login", status_code=302)
    login_code = pgp_login.create_login_code_in_db(db, fingerprint, confirmation_code)
    return template(request=request, name="player/code-display.html", context={"message":encrypted_data.data,"public_pgp_key":public_pgp_key})

@app.post("/player/login/verify")
async def path_player_login_verify(request: Request, db: Session = Depends(get_db)):
    player = get_player(db, request)
    if player:
        return RedirectResponse("/player", status_code=302)
    form = await request.form()
    code = form.get("code")
    public_pgp_key = form.get("public_pgp")
    login_code, display_name, fingerprint = pgp_login.verify_login_code(db, public_pgp_key, code)

    if not login_code:
        return RedirectResponse("/player/login", status_code=302)

    if not login_code.player:
        db_player = models.Player.create(db, display_name, fingerprint)
    else:
        db_player = login_code.player

    response = RedirectResponse("/", status_code=302)
    response.set_cookie("auth", get_jwt_token(db_player.id), max_age=86400 * 365, expires=86400 * 365)
    return response

@app.get("/player/logout")
async def path_player_logout(request: Request, db: Session = Depends(get_db)):
    response = RedirectResponse("/")
    response.delete_cookie("auth")
    return response


@app.get("/arcade/game/{game_num}")
async def path_arcade_game(request: Request, game_num: int, db: Session = Depends(get_db)):
    game = models.Game.get_by_num(db, game_num)
    player = get_player(db, request)
    taken_spots = game.get_taken_spots(db)
    bal_display = request.cookies.get("bal_display", "XMR")
    return template(request=request, name="arcade/game.html", context={"page":"game","game":game,"player":player,"taken_spots":taken_spots,"curr_xmr_rate":xmr_rate.check(),"db":db,"bal_display":bal_display, "sha256":sha256,"config":config})

@app.post("/arcade/game/{game_id}/spot")
async def path_arcade_game_spot(request: Request, game_id: str, db: Session = Depends(get_db)):
    player = get_player(db, request)
    if not player:
        return None
    game = models.Game.get(db, game_id)
    if not game:
        return None

    form = await request.form()
    spot_num = form.get("spot")

    game.add_spot(db, spot_num, player)
    return RedirectResponse(f"/arcade/game/{game.num}", status_code=302)

@app.get("/rate/xmr")
async def path_rate_xmr(request: Request, db: Session = Depends(get_db)):
    return xmr_rate.check()


@app.get("/balance/display/{currency_type}")
async def path_balance_display(request: Request, currency_type: str, from_pg: str, db: Session = Depends(get_db)):
    if currency_type not in {"XMR","USD"}:
        return "fail"
    if from_pg not in {"arcade","deposit","withdraw","player"} and "game" not in from_pg:
        return "fail"

    redirect_url = ""

    if from_pg == "arcade":
        redirect_url = "/arcade/iframe"
    elif from_pg == "deposit":
        redirect_url = "/deposit"
    elif from_pg == "withdraw":
        redirect_url = "/withdraw"
    elif from_pg == "player":
        redirect_url = "/player"
    elif "game" in from_pg:
        redirect_url = f"/arcade/game/{from_pg[5]}" #fails over 9 displayed games, better against injection, 6 displayed games is good enough.

    response = RedirectResponse(redirect_url, status_code=302)
    response.set_cookie("bal_display", currency_type, max_age=86400 * 365)
    return response

@app.get("/hotwallet/status")
async def path_hotwallet_status(request: Request, db: Session = Depends(get_db)):
    balance = hotwallet_status.check()
    return {"total_balance":balance[0]/NORMALIZER,"unlocked_balance":balance[1]/NORMALIZER,"blocks_to_unlock":balance[2]}


if __name__ == "__main__":
    uvicorn.run("main:app", host=config["HOST"], port=config["PORT"], reload=config["LIVE_RELOAD"])
