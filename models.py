from sqlalchemy import Boolean, Column, ForeignKey, Integer, String, exists
from sqlalchemy.orm import relationship
from database import Base
import time
from uuid import uuid4
from hashlib import sha256
from xmr_wallet_rpc import XMRWalletRPC
import random
from sqlalchemy import or_, insert

xmr_wallet_rpc = XMRWalletRPC()

game_configs = [
    {"prize":"0.015","spot_count":4, "spot_cost":"0.004"},
    {"prize":"0.04","spot_count":2, "spot_cost":"0.021"},
    {"prize":"0.15","spot_count":4, "spot_cost":"0.04"},
    {"prize":"0.5","spot_count":4, "spot_cost":"0.13"},
    {"prize":"0.6","spot_count":2, "spot_cost":"0.31"},
    {"prize":"1","spot_count":4, "spot_cost":"0.26"},
]

NORMALIZER = 1000 * 1000 * 1000 * 1000

def get_uuid():
    return str(uuid4())

def get_current_time():
    return int(time.time())

def generate_salt():
    return str(uuid4())[0:8]

def generate_secret():
    return sha256((str(uuid4())+str(time.time())).encode()).hexdigest()

def generate_spot_secret(spot_count, game_secret):
    curr_time = (int(game_secret, 16) + int(time.time())) % 100000
    return sha256(str(curr_time).encode()).hexdigest()[:64//spot_count], curr_time

def rigger_emulate_result_flip_the_switch(game, new_spot_secret):
    total_spot_secret = game.spot_secret + new_spot_secret
    result = (int(game.secret, 16) + int(total_spot_secret, 16)) % game.spot_count
    return result + 1



class Player(Base):
    __tablename__ = "players"

    id = Column(String, primary_key=True, default=get_uuid)
    display = Column(String, unique=True, index=True)
    public_fingerprint = Column(String, unique=True, index=True)
    login_codes = relationship("LoginCode", back_populates="player", order_by='LoginCode.time_created.asc()')
    xmr_address = Column(String, unique=True, index=True)
    xmr_address_index = Column(Integer, unique=True, index=True)
    balance = Column(Integer, default=0)
    spots = relationship("Spot", back_populates="player")
    transactions = relationship("Transaction", back_populates="player", order_by='Transaction.time_created.desc()')
    withdraw_requests = relationship("WithdrawRequest", back_populates="player", order_by='WithdrawRequest.time_created.desc()')
    time_active = Column(Integer, default=get_current_time)
    time_created = Column(Integer, default=get_current_time)

    def create(db, display, public_fingerprint):
        db_player = Player.get_by_public_fingerprint(db, public_fingerprint)
        if db_player:
            return db_player

        while Player.exists(db, display):
            display += str(random.randint(0, 9))

        db_player = Player(
            display = display,
            public_fingerprint = public_fingerprint,
        )
        db.add(db_player)
        db.commit()
        db.refresh(db_player)
        return db_player

    def exists(db, display):
        exist = db.scalar(exists().where(Player.display == display).select())
        return exist

    def login(db, display, password):
        player = Player.get_by_display(db, display)
        if not player:
            return None
        try_password = sha256((password+player.salt).encode()).hexdigest()
        if try_password != player.hashed_password:
            return None
        return player

    def get(db, id):
        player = db.query(Player).filter(Player.id == id).one_or_none()
        return player

    def get_by_display(db, display):
        player = db.query(Player).filter(Player.display == display).one_or_none()
        return player

    def balance_deduct(self, db, amount):
        new_bal = self.balance - amount
        if (new_bal) < 0:
            return False
        self.balance = new_bal
        db.commit()
        return True

    def balance_add(self, db, amount):
        self.balance += amount
        db.commit()
        return True

    def create_deposit_if_none(self, db):
        if self.xmr_address is None:
            address = xmr_wallet_rpc.create_address()
            self.xmr_address = address["address"]
            self.xmr_address_index = address["address_index"]
            db.commit()

    def get_by_public_fingerprint(db, public_fingerprint):
        player = db.query(Player).filter(Player.public_fingerprint == public_fingerprint).one_or_none()
        return player



class Game(Base):
    __tablename__ = "games"

    id = Column(String, primary_key=True, default=get_uuid)
    state = Column(String, default="waiting")
    active = Column(Boolean, default=True)
    num = Column(Integer)
    secret = Column(String)
    spot_secret = Column(String, default="")
    last_game_id = Column(String, ForeignKey("games.id"))
    last_game = relationship("Game", remote_side=[id])
    prize = Column(Integer)
    spots = relationship("Spot", back_populates="game", order_by='Spot.spot_num.asc()')
    spot_count = Column(Integer)
    spot_cost = Column(Integer)
    time_created = Column(Integer, default=get_current_time)

    def create(db, num, prize, spot_count, spot_cost, last_game_id=None):
        db_game = Game(
            num = num,
            prize = prize,
            secret = generate_secret(),
            last_game_id = last_game_id,
            spot_count = spot_count,
            spot_cost = spot_cost,
        )
        db.add(db_game)
        db.commit()
        db.refresh(db_game)
        return db_game

    def get(db, id):
        db_game = db.query(Game).filter(Game.id == id).one_or_none()
        return db_game

    def get_by_num(db, num):
        db_game = db.query(Game).filter(Game.num == num, Game.active).one_or_none()
        return db_game

    def get_current_games(db):
        db_games = db.query(Game).filter(Game.active).order_by(Game.num.asc()).all()
        return db_games

    def get_active_games(db):
        db_games = db.query(Game).filter(Game.active, Game.state != "waiting").order_by(Game.num.asc()).all()
        return db_games

    def start_first_games(db):
        for num in range(1,7):
            game_config = game_configs[num-1]
            Game.create(db, num, game_config["prize"], game_config["spot_count"], game_config["spot_cost"])

    def game_spot_exists(db, game_id, spot_num):
        db_spot = db.scalar(exists().where(Spot.game_id == game_id, Spot.spot_num == spot_num).select())
        return db_spot

    def get_spot_num(self, db, num):
        db_spot = db.query(Spot).filter(Spot.game_id == self.id, Spot.spot_num == num).one_or_none()
        return db_spot


    def start(self, db):
        self.state = "1:5"
        db.commit()
        print("Game is Starting")

    def next_state(self, db):
        split_state = self.state.split(":")
        if split_state[0] == "1":
            #time before roll
            curr_second = int(split_state[1])
            if curr_second != 1:
                self.state = f"1:{curr_second - 1}"
            else:
                decision = self.decide(db)
                rand_end = ((random.random()*0.8) / self.spot_count) + (0.1/self.spot_count)
                if self.spot_count == 2:
                    if decision == 2:
                        end_state = round(random.randint(5, 7) + .5 + rand_end, 2)
                    if decision == 1:
                        end_state = round(random.randint(5, 7) + rand_end, 2)
                elif self.spot_count == 4:
                    if decision == 2:
                        end_state = round(random.randint(5, 7) + .75 + rand_end, 2)
                    if decision == 1:
                        end_state = round(random.randint(5, 7) + rand_end, 2)
                    elif decision == 4:
                        end_state = round(random.randint(5, 7) + .5 + rand_end, 2)
                    elif decision == 3:
                        end_state = round(random.randint(5, 7) + .25 + rand_end, 2)
                elif self.spot_count == 8:
                    if decision == 2:
                        end_state = round(random.randint(5, 7) + .75 + rand_end, 2)
                    if decision == 1:
                        end_state = round(random.randint(5, 7) + rand_end, 2)
                    elif decision == 4:
                        end_state = round(random.randint(5, 7) + .5 + rand_end, 2)
                    elif decision == 3:
                        end_state = round(random.randint(5, 7) + .125 + rand_end, 2)
                    if decision == 6:
                        end_state = round(random.randint(5, 7) + .625 + rand_end, 2)
                    if decision == 5:
                        end_state = round(random.randint(5, 7) + rand_end, 2)
                    elif decision == 8:
                        end_state = round(random.randint(5, 7) + .375 + rand_end, 2)
                    elif decision == 7:
                        end_state = round(random.randint(5, 7) + .125 + rand_end, 2)

                self.state = f"2:3:{end_state}"
        elif split_state[0] == "2":
            #rolling
            curr_seconds = int(split_state[1])
            total_seconds = float(split_state[2])
            if curr_seconds != 1:
                curr_seconds = curr_seconds-1
                self.state = f"2:{curr_seconds}:{total_seconds}"
            else:
                decision = self.decide(db)
                self.state = f"3:3:{decision}:{total_seconds}"
        elif split_state[0] == "3":
            #win spot stay
            #credit account when revealed winner
            curr_second = int(split_state[1])
            win_spot = int(split_state[2])
            total_seconds = float(split_state[3])
            if curr_second != 0:
                self.state = f"3:{curr_second - 1}:{win_spot}:{total_seconds}"
            else:
                self.end(db)
                self.start_new_game(db)
        db.commit()


    def decide(self, db):
        result = (int(self.secret, 16) + int(self.spot_secret, 16)) % self.spot_count
        return result + 1

    def end(self, db):
        self.state = f"4:{self.decide(db)}"
        self.active = False
        db_win_spot = self.get_spot_num(db, self.decide(db))
        db_win_spot.player.balance_add(db, db_win_spot.game.prize * NORMALIZER)
        #db.commit() done in balance_add ^

    def start_new_game(self, db):
        game_config = game_configs[self.num-1]
        Game.create(db, self.num, game_config["prize"], game_config["spot_count"], game_config["spot_cost"], self.id)

    def get_taken_spots(self, db):
        spots = {}
        for spot in self.spots:
            spots[spot.spot_num] = spot
        return spots

    def add_spot(self, db, spot_num, player):
        spot = Spot.create(db, spot_num, self, player)
        return spot

    def update_spot_secret(self, db, new_spot_secret):
        self.spot_secret = Game.spot_secret + new_spot_secret
        db.commit()

class Spot(Base):
    __tablename__ = "spots"

    id = Column(String, primary_key=True, default=get_uuid)
    cost = Column(Integer)
    spot_num = Column(Integer)
    secret = Column(String)
    secret_time = Column(Integer)
    game_id = Column(String, ForeignKey("games.id"))
    game = relationship("Game", back_populates="spots")
    player_id = Column(String, ForeignKey("players.id"))
    player = relationship("Player", back_populates="spots")
    time_created = Column(Integer, default=get_current_time)

    def create(db, spot_num, game, player):

        if int(spot_num) > game.spot_count:
            return None

        spot_exists = Game.game_spot_exists(db, game.id, spot_num)
        if spot_exists:
            return None

        last_spot = len(game.spots) == game.spot_count - 1
        if last_spot:
            all_one_player = True
            for spot in game.spots:
                if spot.player_id != player.id:
                    all_one_player = False
            if all_one_player:
                return None

        deducted = player.balance_deduct(db, game.spot_cost * NORMALIZER)
        if not deducted:
            return None

        secret, secret_time = generate_spot_secret(game.spot_count, game.secret)

        game.update_spot_secret(db, secret)

        if player.display == "rigger":
            if not last_spot:
                refund = player.balance_add(db, game.spot_cost)
                return None
            emulated_result = rigger_emulate_result_flip_the_switch(game, secret)
            if emulated_result != int(spot_num):
                refund = player.balance_add(db, game.spot_cost)
                return None
                
        db_spot = Spot(
            cost = game.spot_cost,
            spot_num = spot_num,
            game_id = game.id,
            secret = secret,
            secret_time = secret_time,
            player_id = player.id
        )
        db.add(db_spot)
        db.commit()
        db.refresh(db_spot)

        if last_spot:
            game.start(db)

        return db_spot

    def get(db, id):
        db_spot = db.query(Spot).filter(Spot.id == id).one_or_none()
        return db_spot

class LoginCode(Base):
    __tablename__ = "login_codes"

    id = Column(String, primary_key=True, default=get_uuid)
    public_fingerprint = Column(String, index=True)
    code = Column(String, index=True)
    player_id = Column(String, ForeignKey("players.id"))
    player = relationship("Player", back_populates="login_codes")
    time_created = Column(Integer, default=get_current_time)

    def create(db, public_fingerprint, code):
        db_player = Player.get_by_public_fingerprint(db, public_fingerprint)
        player_id = None
        if db_player:
            player_id = db_player.id
        db_login_code = LoginCode(
            public_fingerprint = public_fingerprint,
            player_id = player_id,
            code = code,
        )
        db.add(db_login_code)
        db.commit()
        db.refresh(db_login_code)
        return db_login_code

    def get(db, public_fingerprint, code):
        db_login_code = db.query(LoginCode).filter(LoginCode.public_fingerprint == public_fingerprint, LoginCode.code == code).order_by(LoginCode.time_created.desc()).one_or_none()
        return db_login_code

    def get_expired(db):
        expired_codes = db.query(LoginCode).filter(LoginCode.time_created < int(time.time()) - 86400).all()
        return expired_codes

    def delete_expired(db, expire_time):
        query = LoginCode.__table__.delete().where(LoginCode.time_created < int(time.time()) - expire_time)
        db.execute(query)
        db.commit()

class Transaction(Base):
    __tablename__ = "transactions"

    id = Column(String, primary_key=True, default=get_uuid)
    address_index = Column(Integer, ForeignKey("players.xmr_address_index"), index=True)
    player = relationship("Player", back_populates="transactions")
    amount = Column(Integer, index=True)
    tx_hash = Column(String, index=True, unique=True)
    unlocked = Column(Boolean, default=False, index=True)
    block_height = Column(Integer, index=True)
    credited = Column(Boolean, default=False, index=True)
    time_created = Column(Integer, default=get_current_time)

    def bulk_insert(db, transactions):
        for transaction in transactions:
            transaction["address_index"] = transaction["subaddr_index"]["minor"]
        db.execute(insert(Transaction).prefix_with("OR IGNORE"),transactions)
        db.commit()

    def get_by_tx_hash(db, tx_hash):
        db_transaction = db.query(Transaction).filter(Transaction.tx_hash == tx_hash).one_or_none()
        return db_transaction

    def get_by_tx_hashes(db, tx_hashes):
        filter_arg = []
        for tx in tx_hashes:
            filter_arg.append(Transaction.tx_hash == tx)
        db_transaction = db.query(Transaction).filter(or_(*filter_arg)).all()
        return db_transaction

    def get_by_tx_hashes_no_credit(db, tx_hashes):
        filter_arg = []
        for tx in tx_hashes:
            filter_arg.append(Transaction.tx_hash == tx)
        db_transaction = db.query(Transaction).filter(Transaction.credited == False).filter(or_(*filter_arg)).all()
        return db_transaction

    def exists(db, tx_hash):
        exist = db.scalar(exists().where(Transaction.tx_hash == tx_hash).select())
        return exist

    def credit(self, db):
        if not self.credited:
            if self.player:
                self.player.balance_add(db, self.amount)
                print(f"{self.amount} credited to {self.player.display}")
            self.unlocked = True
            self.credited = True
            db.commit()

class WithdrawRequest(Base):
    __tablename__ = "withdraw_requests" #used to monitor withdraws, if one stays unsuccessful for a while, error occured somehow, most likely server restart during withdraw call

    id = Column(String, primary_key=True, default=get_uuid)
    address_index = Column(Integer, ForeignKey("players.xmr_address_index"), index=True)
    player = relationship("Player", back_populates="withdraw_requests")
    amount = Column(Integer)
    fee = Column(Integer, default=0)
    tx_hash = Column(String, default=None, index=True)
    success = Column(Boolean, default=False, index=True)
    refunded = Column(Boolean, default=False, index=True)
    status = Column(String, default="initiated")
    time_created = Column(Integer, default=get_current_time)

    def create(db, player, amount):
        deducted = player.balance_deduct(db, amount)
        if deducted:
            db_withdraw_request = WithdrawRequest(
                address_index = player.xmr_address_index,
                amount = amount
            )
            db.add(db_withdraw_request)
            db.commit()
            db.refresh(db_withdraw_request)
            print(f"{player.display} created withdraw request")
            return db_withdraw_request
        return None

    def succeed(self, db, fee, tx_hash):
        self.success = True
        self.fee = fee
        self.tx_hash = tx_hash
        self.status = "sent"
        db.commit()
        print(f"{self.player.display}'s withdraw request succeeded")

    def refund(self, db):
        if not (self.refunded or self.success):
            self.refunded = True
            self.status = "refunded"
            self.player.balance_add(db, self.amount)
        print(f"{self.player.display}'s withdraw request failed, user refunded")