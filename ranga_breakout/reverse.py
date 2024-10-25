from traceback import print_exc
from typing import Any  # Importing only the required types

from toolkit.kokoo import timer, dt_to_str

from __init__ import logging
from api import Helper

from history import find_buy_stop, get_historical_data, find_sell_stop, find_extremes

from pprint import pprint


def create_order_args(ohlc, side, price, trigger_price):
    return dict(
        symbol=ohlc["tsym"],
        exchange=ohlc["exchange"],
        order_type="STOPLOSS_LIMIT",
        product="INTRADAY",  # Options: CARRYFORWARD, INTRADAY
        quantity=ohlc["quantity"],
        symboltoken=ohlc["token"],
        variety="STOPLOSS",
        duration="DAY",
        side=side,
        price=price,
        trigger_price=trigger_price,
    )


class Reverse:

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
            "entry": None,
            "stop_price": None,
            "can_trail": None,
            "candle_two": None,
        }
        self.dct.update(defaults)
        self.candle_count = 2
        self.candle_other = 2
        self.dct_of_orders = {}
        self.message = "message not set"
        logging.info(self.dct)
        self.make_order_params()

    def make_order_params(self):
        try:
            high = float(self.dct["h"])
            low = float(self.dct["l"])
            half = (high - low) / 2
            self.dct["buy_args"] = create_order_args(
                ohlc=self.dct, side="BUY", price=low - half, trigger_price=low - half
            )
            self.dct["sell_args"] = create_order_args(
                ohlc=self.dct, side="SELL", price=high + half, trigger_price=high + half
            )
            self.dct["fn"] = self.place_both_orders
        except Exception as e:
            fn = self.dct.pop("fn")
            self.message = f"{self.dct['tsym']} encountered {e} while {fn}"
            logging.error(self.message)
            print_exc()
            self.dct["fn"] = None

    def place_both_orders(self):
        try:
            args = self.dct

            # Place buy order
            resp = Helper.api.order_place(**args["buy_args"])
            logging.debug(
                f"{args['buy_args']['symbol']} {args['buy_args']['side']} got {resp=}"
            )
            self.dct["buy_id"] = resp

            # Place sell order
            resp = Helper.api.order_place(**args["sell_args"])
            logging.debug(
                f"{args['sell_args']['symbol']} {args['sell_args']['side']} got {resp=}"
            )
            self.dct["sell_id"] = resp

            self.dct["fn"] = self.move_initial_stop
            self.message = "buy and sell orders placed"
        except Exception as e:
            fn = self.dct.pop("fn")
            self.message = f"{self.dct['tsym']} encountered {e} while {fn}"
            logging.error(self.message)
            print_exc()
            self.dct["fn"] = None

    def get_history(self):
        params = {
            "exchange": self.dct["exchange"],
            "symboltoken": self.dct["token"],
            "interval": "FIFTEEN_MINUTE",
            "fromdate": dt_to_str("9:15"),
            "todate": dt_to_str(""),
        }
        print(params)
        return get_historical_data(params)

    def _is_buy_or_sell(self, operation):
        buy_or_sell = self.dct[f"{operation}_id"]
        return self.dct_of_orders[buy_or_sell]["status"] == "complete"

    def is_order_complete(self, operation):
        try:
            FLAG = False
            if self._is_buy_or_sell(operation):
                self.dct["fn"] = None
                self.message = (
                    f"trail complete for {self.dct['tsym']} by {operation} stop order"
                )
                FLAG = True
        except Exception as e:
            self.message = f"{self.dct['tsym']} encountered {e} while is_order_complete"
            logging.error(self.message)
            print_exc()
        finally:
            return FLAG

    def move_initial_stop(self):
        """
        determine if buy or sell order is completed
        """
        try:
            high = float(self.dct["h"])
            low = float(self.dct["l"])
            half = (high - low) / 2
            if self._is_buy_or_sell("buy"):
                stop_now = low - half - (high - low)
                self.dct["stop_price"] = stop_now
                args = dict(
                    orderid=self.dct["sell_id"],
                    price=stop_now,
                    triggerprice=stop_now,
                )
                self.dct["sell_args"].update(args)
                args = self.dct["sell_args"]
                self.message = (
                    f'buy stop {stop_now} is going to replace {self.dct["stop_price"]}'
                )
                self.dct["stop_price"] = stop_now
                logging.debug(f"order modify {args}")
                resp = Helper.api.order_modify(**args)
                logging.debug(f"order modify {resp}")
                candles_now = self.get_history()
                if candles_now is not None and any(candles_now):
                    self.dct["candle_two"] = max(candles_now[-3][2], candles_now[-2][2])
                    self.dct["can_trail"] = lambda c: c["last_price"] > c["candle_two"]
                    self.dct["l"], self.dct["h"] = find_extremes(candles_now)
                    self.dct["entry"] = "buy"
            elif self._is_buy_or_sell("sell"):
                stop_now = high + half + (high - low)
                self.dct["stop_price"] = stop_now
                args = dict(
                    orderid=self.dct["buy_id"],
                    price=stop_now,
                    triggerprice=stop_now,
                )
                self.dct["buy_args"].update(args)
                args = self.dct["buy_args"]
                logging.debug(f"order modify {args}")
                resp = Helper.api.order_modify(**args)
                logging.debug(f"order modify {resp}")
                candles_now = self.get_history()
                if candles_now is not None and any(candles_now):
                    self.dct["candle_two"] = min(candles_now[-3][3], candles_now[-2][3])
                    self.dct["can_trail"] = lambda c: c["last_price"] < c["candle_two"]
                    self.dct["l"], self.dct["h"] = find_extremes(candles_now)
                    self.dct["entry"] = "sell"

            if self.dct["entry"] is None:
                self.message = f"no entry order is completed for {self.dct['tsym']}"
            else:
                self.message = (
                    f"{self.dct['entry']} order completed for {self.dct['tsym']}"
                )
                self.dct["fn"] = self.move_breakeven
        except Exception as e:
            self.message = f"{self.dct['tsym']} encountered {e} while is_buy_or_sell"
            logging.error(self.message)
            print_exc()

    def move_breakeven(self):
        try:
            # check if stop loss is already hit
            operation = "sell" if self.dct["entry"] == "buy" else "buy"
            if self.is_order_complete(operation):
                return

            if self.dct["can_trail"](self.dct):
                # assign next funtion
                self.dct["fn"] = self.trail_stoploss
                # save for last action
                self.message = f"moved to breakeven {self.dct['candle_two']} for {self.dct['tsym']}"
                # assign condtion for next function
                if self.dct["entry"] == "buy":
                    self.dct["can_trail"] = lambda c: c["last_price"] > c["h"]
                else:
                    self.dct["can_trail"] = lambda c: c["last_price"] < c["l"]
                return

            # means opposite here
            if operation == "buy":
                print(
                    f'{self.dct["last_price"]} is not < {self.dct["candle_two"]} for {self.dct["tsym"]}'
                )
            else:
                print(
                    f'{self.dct["last_price"]} is not > {self.dct["candle_two"]} for {self.dct["tsym"]}'
                )

        except Exception as e:
            self.message = f'{self.dct["tsym"]} encountered {e} while move_breakeven'
            logging.error(self.message)
            print_exc()

    def _is_modify_order(self, candles_now):
        try:
            # buy trade
            if self.dct["entry"] == "buy":
                # stop_now = min(candles_now[-3][3], candles_now[-2][3])
                stop_now, highest = find_buy_stop(candles_now)
                if stop_now and stop_now > self.dct["stop_price"]:
                    args = dict(
                        orderid=self.dct["sell_id"],
                        price=stop_now - 0.10,
                        triggerprice=stop_now - 0.05,
                    )
                    self.dct["sell_args"].update(args)
                    args = self.dct["sell_args"]
                    self.dct["h"] = highest
                    self.message = f'buy stop {stop_now} is going to replace {self.dct["stop_price"]}'
                    self.dct["stop_price"] = stop_now
                    return args

            elif self.dct["entry"] == "sell":
                # stop_now = max(candles_now[-3][2], candles_now[-2][2])
                stop_now, lowest = find_sell_stop(candles_now)
                if stop_now and stop_now < self.dct["stop_price"]:
                    args = dict(
                        orderid=self.dct["buy_id"],
                        price=stop_now + 0.10,
                        triggerprice=stop_now + 0.05,
                    )
                    self.dct["buy_args"].update(args)
                    args = self.dct["buy_args"]
                    self.dct["l"] = lowest
                    self.message = f'sell stop {stop_now} is going to replace {self.dct["stop_price"]}'
                    self.dct["stop_price"] = stop_now
                    return args
            return {}
        except Exception as e:
            fn = self.dct.pop("fn")
            self.message = f"{self.dct['tsym']} encountered {e} {fn}"
            logging.error(self.message)
            print_exc()
            self.dct["fn"] = None

    def trail_stoploss(self):
        """
        if candles  count is changed and then check ltp
        """
        try:
            # check if stop loss is already hit
            operation = "sell" if self.dct["entry"] == "buy" else "buy"
            if self.is_order_complete(operation):
                return

            FLAG = False
            if self.candle_other > self.candle_count:
                print(
                    f"other candles {self.candle_other} > this symbol candle {self.candle_count}"
                )
                FLAG = True
            elif self.dct["can_trail"](self.dct):
                print(f'{self.dct["last_price"]} is a breakout for {self.dct["tsym"]}')
                FLAG = True

            if FLAG:
                candles_now = self.get_history()
                if len(candles_now) > self.candle_count:
                    pprint(candles_now)
                    print(
                        f"curr candle:{len(candles_now)} > prev candle:{self.candle_count}"
                    )
                    args = self._is_modify_order(candles_now)
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
                    if any(args):
                        logging.debug(f"order modify {args}")
                        resp = Helper.api.order_modify(**args)
                        logging.debug(f"order modify {resp}")
                        self.candle_count = len(candles_now)
                        self.candle_other = len(candles_now)
                        # timer(0.5)

        except Exception as e:
            fn = self.dct.pop("fn")
            self.message = f"{self.dct['tsym']} encountered {e} while {fn}"
            logging.error(self.message)
            print_exc()
            self.dct["fn"] = None

    def run(self, lst_of_orders, dct_of_ltp, CANDLE_OTHER):
        try:
            if isinstance(lst_of_orders, list):
                self.dct_of_orders = {
                    dct["orderid"]: dct for dct in lst_of_orders if "orderid" in dct
                }
            self.dct["last_price"] = dct_of_ltp.get(
                self.dct["token"], self.dct["last_price"]
            )
            if CANDLE_OTHER > self.candle_other:
                print(
                    f'{self.dct["tsym"]} other candle {self.candle_other}  > other symbol candle {CANDLE_OTHER}'
                )
                self.candle_other = CANDLE_OTHER

            if self.dct["fn"] is not None:
                message = dict(
                    symbol=self.dct["tsym"],
                    low=self.dct["l"],
                    high=self.dct["h"],
                    last_price=self.dct["last_price"],
                    prev_candle=self.candle_count,
                    other_candle=self.candle_other,
                    stop_loss=self.dct["stop_price"],
                    next_fn=self.dct["fn"],
                    candle_two=self.dct["candle_two"],
                )
                pprint(message)
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