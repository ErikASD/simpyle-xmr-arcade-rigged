import time
import requests

class XMRRate:
	def __init__(self, config):
		self.LEEWAY = config["XMR_RATE_LEEWAY"]
		self.price = 0
		self.last_updated_time = 0
		self.check()

	def check(self):
		current_time = int(time.time())

		if current_time > self.last_updated_time + self.LEEWAY:
			self.update_price()

		return self.price

	def update_price(self):
		url = "https://whitebit.com/api/v1/public/ticker?market=XMR_USDT" #most recent price from top 3 exchange, good as estimate, not advised if using to convert.
		response = requests.get(url)
		body = response.json()
		self.price = float(body["result"]["last"])
		self.last_updated_time = int(time.time())