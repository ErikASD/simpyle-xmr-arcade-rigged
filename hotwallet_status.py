import time
import models

class HotWalletStatus:
	def __init__(self, config):
		self.LEEWAY = config["HOTWALLET_STAUTS_LEEWAY"]
		self.balance = 0
		self.unlocked_balance = 0
		self.last_updated_time = 0
		self.blocks_to_unlock = 0
		self.check()

	def check(self):
		current_time = int(time.time())

		if current_time > self.last_updated_time + self.LEEWAY:
			self.update_balance()

		return self.balance, self.unlocked_balance, self.blocks_to_unlock

	def update_balance(self):
		try:
			balance = models.xmr_wallet_rpc.get_balance()
			self.balance = balance["balance"]
			self.unlocked_balance = balance["unlocked_balance"]
			self.blocks_to_unlock = balance["blocks_to_unlock"]
			self.last_updated_time = int(time.time())
		except:
			print("failed to get hotwallet balance")