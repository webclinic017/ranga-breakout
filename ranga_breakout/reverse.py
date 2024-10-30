from traceback import print_exc
from typing import Any  # Importing only the required types

from toolkit.kokoo import timer, dt_to_str

from __init__ import logging
from api import Helper

from history import find_buy_stop, get_historical_data, find_sell_stop, find_extremes

from pprint import pprint


def float_2_curr(value: float):
    try:
        return round(value / 0.05) * 0.05
    except Exception as e:
        print(e)


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

    """ 
        common methods
    """

    def get_history(self):
        params = {
            "exchange": self.dct["exchange"],
            "symboltoken": self.dct["token"],
            "interval": "FIFTEEN_MINUTE",
            "fromdate": dt_to_str("9:15"),
            "todate": dt_to_str(""),
        }
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

    """
       1. make order params 
    """

    def make_order_params(self):
        """
        make order params for placing orders
        """
        try:
            high, low = float(self.dct["h"]), float(self.dct["l"])
            half_spread = (high - low) / 2

            # Precompute prices for buy and sell orders
            buy_price = float_2_curr(low - half_spread)
            sell_price = float_2_curr(high + half_spread)

            self.dct["buy_args"] = create_order_args(
                ohlc=self.dct, side="BUY", price=buy_price, trigger_price=buy_price
            )
            self.dct["sell_args"] = create_order_args(
                ohlc=self.dct, side="SELL", price=sell_price, trigger_price=sell_price
            )
            self.dct["fn"] = self.place_both_orders
        except Exception as e:
            fn = self.dct.pop("fn")
            self.message = f"{self.dct['tsym']} encountered {e} while {fn}"
            logging.error(self.message)
            print_exc()
            self.dct["fn"] = None

    """
     1. place both  orders
    """

    def _place_order(self, order_args_key, order_type):
        """Helper to place an order and log the response."""
        args = self.dct[order_args_key]
        resp = Helper.api.order_place(**args)
        logging.debug(f"{args['symbol']} {order_type} order response: {resp}")
        return resp

    def place_both_orders(self):
        try:
            # Place buy and sell orders with helper function
            self.dct["buy_id"] = self._place_order("buy_args", "BUY")
            self.dct["sell_id"] = self._place_order("sell_args", "SELL")

            # Set the next function
            self.dct["fn"] = self.move_initial_stop
            self.message = f"buy and sell orders placed for {self.dct['tsym']}"

        except Exception as e:
            fn_name = self.dct.pop("fn", None)  # Remove fn on error
            self.message = (
                f"{self.dct['tsym']} encountered error '{e}' in function {fn_name}"
            )
            logging.error(self.message)
            print_exc()
            self.dct["fn"] = None  # Reset fn pointer on failure

    """ 
      2.  move initial stop 
    """

    def move_initial_stop(self):
        try:
            high = float(self.dct["h"])
            low = float(self.dct["l"])
            half = float_2_curr((high - low) / 2)
            if self._is_buy_or_sell("buy"):
                self.dct["entry"] = "buy"
                stop_now = float_2_curr(low - half - (high - low))
                args = dict(
                    orderid=self.dct["sell_id"],
                    price=stop_now,
                    triggerprice=stop_now,
                )
                self.dct["sell_args"].update(args)
                args = self.dct["sell_args"]
                self.dct["stop_price"] = stop_now
                logging.debug(f"order modify {args}")
                resp = Helper.api.order_modify(**args)
                logging.debug(f"order modify {resp}")
                candles_now = self.get_history()
                if candles_now is not None and any(candles_now):
                    self.dct["candle_two"] = max(candles_now[-3][2], candles_now[-2][2])
                    self.dct["can_trail"] = lambda c: c["last_price"] > c["candle_two"]
                    self.dct["l"], self.dct["h"] = find_extremes(candles_now)
            elif self._is_buy_or_sell("sell"):
                self.dct["entry"] = "sell"
                stop_now = float_2_curr(high + half + (high - low))
                args = dict(
                    orderid=self.dct["buy_id"],
                    price=stop_now,
                    triggerprice=stop_now,
                )
                self.dct["buy_args"].update(args)
                args = self.dct["buy_args"]
                self.dct["stop_price"] = stop_now
                logging.debug(f"order modify {args}")
                resp = Helper.api.order_modify(**args)
                logging.debug(f"order modify {resp}")
                # get candles
                candles_now = self.get_history()
                if candles_now is not None and any(candles_now):
                    self.dct["candle_two"] = min(candles_now[-3][3], candles_now[-2][3])
                    self.dct["can_trail"] = lambda c: c["last_price"] < c["candle_two"]
                    self.dct["l"], self.dct["h"] = find_extremes(candles_now)

            if self.dct["entry"] is not None:
                self.message = f'{self.dct["entry"]} NEW stop {stop_now} '
                f'is going to replace INITIAL stop {self.dct["stop_price"]}'
                self.dct["fn"] = self.move_breakeven
        except Exception as e:
            self.message = f"{self.dct['tsym']} encountered {e} while is_buy_or_sell"
            logging.error(self.message)
            print_exc()

    """
        3. Move to Breakeven
    """

    def _update_order_args(self, order_id, args_dict, stop_now):
        """Helper to update order args and modify order."""
        self.dct[args_dict].update(
            {
                "orderid": self.dct[order_id],
                "price": stop_now,
                "triggerprice": stop_now,
            }
        )
        args = self.dct[args_dict]
        self.message = f'{self.dct["entry"]} BREAKEVEN {stop_now} will replace {self.dct["stop_price"]}'
        self.dct["stop_price"] = stop_now
        logging.debug(f"Order modify: {args}")
        resp = Helper.api.order_modify(**args)
        logging.debug(f"Order modify response: {resp}")

    def _set_trailing_stoploss(self):
        """Helper function to set trailing stop loss."""
        try:
            # Determine trailing conditions based on entry type
            if self.dct["entry"] == "buy":
                self.dct["can_trail"] = lambda c: c["last_price"] > c["h"]
                stop_now, order_id, args_dict = self.dct["l"], "sell_id", "sell_args"
            else:
                self.dct["can_trail"] = lambda c: c["last_price"] < c["l"]
                stop_now, order_id, args_dict = self.dct["h"], "buy_id", "buy_args"

            # Update arguments and log the change
            self._update_order_args(order_id, args_dict, stop_now)
        except Exception as e:
            logging.error(f"Error while setting trailing stop: {e}")

    def move_breakeven(self):
        try:
            # Determine operation type based on entry
            operation = "sell" if self.dct["entry"] == "buy" else "buy"

            # check if stop loss is already hit
            if self.is_order_complete(operation):
                return

            if self.dct["can_trail"](self.dct):
                # assign next function
                self.dct["fn"] = self.trail_stoploss
                # assign condtion for next function
                self._set_trailing_stoploss()
                return

            # means opposite here
            condition = "<" if operation == "buy" else ">"
            logging.info(
                f'{self.dct["last_price"]} is not {condition} {self.dct["candle_two"]} for {self.dct["tsym"]}'
            )
        except Exception as e:
            self.message = f'{self.dct["tsym"]} encountered {e} while move_breakeven'
            logging.error(self.message)
            print_exc()

    """
        4. trail stoploss
    """

    def _update_buy_stop(self, candles_now):
        """Helper to update the buy stop loss."""
        stop_now, highest = find_buy_stop(candles_now)
        if stop_now and stop_now > self.dct["stop_price"]:
            stop_now = float_2_curr(stop_now)
            args = {
                "orderid": self.dct["sell_id"],
                "price": stop_now - 0.10,
                "triggerprice": stop_now - 0.05,
            }
            self.dct["sell_args"].update(args)
            self.dct["h"] = highest
            self.dct["stop_price"] = stop_now
            self.message = f"TRAILING {stop_now} will replace {self.dct['stop_price']}"
            return args
        return {}

    def _update_sell_stop(self, candles_now):
        """Helper to update the sell stop loss."""
        stop_now, lowest = find_sell_stop(candles_now)
        if stop_now and stop_now < self.dct["stop_price"]:
            stop_now = float_2_curr(stop_now)
            args = {
                "orderid": self.dct["buy_id"],
                "price": stop_now + 0.10,
                "triggerprice": stop_now + 0.05,
            }
            self.dct["buy_args"].update(args)
            self.dct["l"] = lowest
            self.dct["stop_price"] = stop_now
            self.message = f"TRAILING {stop_now} will replace {self.dct['stop_price']}"
            return args
        return {}

    def _is_modify_order(self, candles_now):
        try:
            if self.dct["entry"] == "buy":
                return self._update_buy_stop(candles_now)
            elif self.dct["entry"] == "sell":
                return self._update_sell_stop(candles_now)

            return {}

        except Exception as e:
            self.message = (
                f"{self.dct['tsym']} encountered error '{e}' in  is_modify_order"
            )
            logging.error(self.message)
            print_exc()

    def _should_modify_order(self):
        """Determine if conditions meet for modifying the trailing stop loss."""
        if self.candle_other > self.candle_count:
            print(
                f"Other candles ({self.candle_other}) exceed current symbol candle ({self.candle_count})"
            )
            return True
        elif self.dct["can_trail"](self.dct):
            print(f"{self.dct['last_price']} is a breakout for {self.dct['tsym']}")
            return True
        return False

    def _modify_order(self, candles_now):
        """Handles the order modification if conditions are met."""
        args = self._is_modify_order(candles_now)
        if any(args):
            logging.debug(f"Order modification parameters: {args}")
            resp = Helper.api.order_modify(**args)
            logging.debug(f"Order modification response: {resp}")
            self.candle_count = len(candles_now)
            self.candle_other = len(candles_now)

    def trail_stoploss(self):
        """
        if candles  count is changed and then check ltp
        """
        try:
            # check if stop loss is already hit
            operation = "sell" if self.dct["entry"] == "buy" else "buy"
            if self.is_order_complete(operation):
                return

            if self._should_modify_order():
                candles_now = self.get_history()
                if len(candles_now) > self.candle_count:
                    pprint(candles_now)
                    print(
                        f"curr candle:{len(candles_now)} > prev candle:{self.candle_count}"
                    )
                    self._modify_order(candles_now)

        except Exception as e:
            self.message = f"{self.dct['tsym']} encountered {e} while trailing stop"
            logging.error(self.message)
            print_exc()

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
