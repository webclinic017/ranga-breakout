from traceback import print_exc
from typing import Any  # Importing only the required types

from toolkit.kokoo import timer, dt_to_str

from __init__ import logging
from api import Helper

from history import get_historical_data, get_low_high


def create_order_args(ohlc, side, price, trigger_price):
    return dict(
        symbol=ohlc["tsym"],
        exchange=ohlc["exchange"],
        order_type="STOPLOSS_MARKET",
        product="INTRADAY",  # Options: CARRYFORWARD, INTRADAY
        quantity=ohlc["quantity"],
        symboltoken=ohlc["token"],
        variety="STOPLOSS",
        duration="DAY",
        side=side,
        price=price,
        trigger_price=trigger_price,
    )


class Breakout:
    def last_message(self):
        print(self.message)

    def __init__(self, param: dict[str, dict[str, Any]]):
        self.dct = dict(
            tsym=param["tsym"],
            exchange=param["exchange"],
            h=param["h"],
            l=param["l"],
            last_price=param["c"],
            quantity=param["quantity"],
            token=param["token"],
        )

        defaults = {
            "fn": self.make_order_params,
            "buy_args": {},
            "sell_args": {},
            "buy_id": None,
            "sell_id": None,
            "entry": 0,
            "can_trail": None,
            "stop_price": None,
        }
        self.candle_count = 2
        self.dct.update(defaults)
        self.dct_of_orders = {}
        self.message = None
        logging.info(self.dct)

    def make_order_params(self):
        try:
            self.dct["buy_args"] = create_order_args(
                self.dct,
                "BUY",
                float(self.dct["h"]) + 0.10,
                float(self.dct["h"]) + 0.05,
            )
            self.dct["sell_args"] = create_order_args(
                self.dct,
                "SELL",
                float(self.dct["l"]) - 0.10,
                float(self.dct["l"]) - 0.05,
            )
            self.dct["fn"] = self.place_both_orders
        except Exception as e:
            fn = self.dct.pop("fn")
            self.message = f"{self.dct['tsym']} encountered {e} while {fn}"
            logging.error(self.message)
            print_exc()
            self.dct["fn"] = self.last_message

    def place_both_orders(self):
        try:
            args = self.dct

            # Place buy order
            resp = Helper.api.order_place(**args["buy_args"])
            logging.info(
                f"{args['buy_args']['symbol']} {args['buy_args']['side']} got {resp=}"
            )
            self.dct["buy_id"] = resp

            # Place sell order
            resp = Helper.api.order_place(**args["sell_args"])
            logging.info(
                f"{args['sell_args']['symbol']} {args['sell_args']['side']} got {resp=}"
            )
            self.dct["sell_id"] = resp

            self.dct["fn"] = self.is_buy_or_sell
        except Exception as e:
            fn = self.dct.pop("fn")
            self.message = f"{self.dct['tsym']} encountered {e} while {fn}"
            logging.error(self.message)
            print_exc()
            self.dct["fn"] = self.last_message

    def is_buy_or_sell(self):
        """
        determine if buy or sell order is completed
        """
        try:
            buy = self.dct["buy_id"]
            sell = self.dct["sell_id"]
            if self.dct_of_orders[buy]["status"] == "complete":
                self.dct["entry"] = 1
                self.dct["can_trail"] = lambda c: c["last_price"] > c["h"]
                self.dct["stop_price"] = self.dct["l"]
            elif self.dct_of_orders[sell]["status"] == "complete":
                self.dct["entry"] = -1
                self.dct["can_trail"] = lambda c: c["last_price"] < c["l"]
                self.dct["stop_price"] = self.dct["h"]

            if self.dct["entry"] != 0:
                logging.info(f"no buy/sell complete for {self.dct['tsym']}")
                self.dct["fn"] = self.trail_stoploss
            else:
                logging.debug(f"order not complete for {self.dct['tsym']}")
        except Exception as e:
            # fn = self.dct.pop("fn")
            message = f"{self.dct['tsym']} encountered {e} while is_buy_or_sell"
            logging.error(message)
            print_exc()
            # self.dct["fn"] = self.last_message

    def get_history(self):
        params = {
            "exchange": self.dct["exchange"],
            "symboltoken": self.dct["token"],
            "interval": "FIFTEEN_MINUTE",
            "fromdate": dt_to_str("9:15"),
            "todate": dt_to_str(""),
        }
        return get_historical_data(params)

    def _is_modify_order(self, candles_now):
        try:
            is_flag = False
            # buy trade
            if self.dct["entry"] == 1:
                stop_now = min(candles_now[-3][3], candles_now[-2][3])
                is_flag = stop_now > self.dct["stop_price"]
                args = dict(
                    orderid=self.dct["sell_id"],
                    price=stop_now - 0.10,
                    triggerprice=stop_now - 0.05,
                )
                self.dct["sell_args"].update(args)
                args = self.dct["sell_args"]
            else:
                stop_now = max(candles_now[-3][2], candles_now[-2][2])
                is_flag = stop_now < self.dct["stop_price"]
                args = dict(
                    orderid=self.dct["buy_id"],
                    price=stop_now + 0.10,
                    triggerprice=stop_now + 0.05,
                )
                self.dct["buy_args"].update(args)
                args = self.dct["buy_args"]
            if is_flag:
                print(
                    f'new stop {stop_now} is going to replace {self.dct["stop_price"]}'
                )
                return args, stop_now
            else:
                return {}, None
        except Exception as e:
            fn = self.dct.pop("fn")
            self.message = f"{self.dct['tsym']} encountered {e} while {fn}"
            logging.error(self.message)
            print_exc()
            self.dct["fn"] = self.last_message

    def trail_stoploss(self):
        """
        if candles  count is changed and then check ltp
        """
        try:
            print(
                f' low:{self.dct["l"]} high:{self.dct["h"]} candle: {self.candle_count} stop_loss:  {self.dct["stop_price"]} '
            )
            if self.dct["can_trail"](self.dct):

                print(f'{self.dct["last_price"]} is a breakout for {self.dct["tsym"]}')

                candles_now = self.get_history()
                if len(candles_now) > self.candle_count:
                    print(f"{candles_now} > {self.candle_count}")

                    args, stop_now = self._is_modify_order(candles_now)
                    # modify order
                    """
                    "variety":"NORMAL",
                    "orderid":"201020000000080",
                    "ordertype":"LIMIT",
                    "producttype":"INTRADAY",
                    "duration":"DAY",
                    "price":"194.00",
                    "quantity":"1",
                    "tradingsymbol":"SBIN-EQ",
                    "symboltoken":"3045",
                    "exchange":"NSE"
                    """
                    if any(args) and stop_now:
                        logging.debug(f"order modify {args}")
                        resp = Helper.api.order_modify(**args)
                        logging.debug(f"order modify {resp}")
                        self.dct["stop_price"] = stop_now
                        # update high and low except for the last
                        self.dct["l"], self.dct["h"] = get_low_high(candles_now[:-1])
                        # update candle count if order is placed
                        self.candle_count = len(candles_now)
            timer(1)

        except Exception as e:
            fn = self.dct.pop("fn")
            self.message = f"{self.dct['tsym']} encountered {e} while {fn}"
            logging.error(self.message)
            print_exc()
            self.dct["fn"] = self.last_message

    def run(self, lst_of_orders, dct_of_ltp):
        try:
            if isinstance(lst_of_orders, list):
                self.dct_of_orders = {
                    dct["orderid"]: dct for dct in lst_of_orders if "orderid" in dct
                }
            self.dct["last_price"] = dct_of_ltp.get(
                self.dct["token"], self.dct["last_price"]
            )
            timer(1)
            if self.dct["fn"] is not None:
                print(
                    f"{self.dct['tsym']} run {self.dct['fn']} {self.dct['last_price']}"
                )
                self.dct["fn"]()
        except Exception as e:
            self.message = f"{self.dct['tsym']} encountered {e} while run"
            logging.error(self.message)
            print_exc()


if __name__ == "__main__":
    from main import get_ltp
    from universe import stocks_in_play
    from history import get_candles

    try:
        df = stocks_in_play()
        params = get_candles(df)

        # create strategy object
        for _, param in params.items():
            obj = Breakout(param)
            break

        lst_of_orders = Helper.api.orders
        dct_of_ltp = get_ltp(params)
        obj.dct["fn"] = obj.is_buy_or_sell
        obj.run(lst_of_orders, dct_of_ltp)
    except Exception as e:
        print_exc()
        print(e)
