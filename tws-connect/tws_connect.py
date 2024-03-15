from decimal import Decimal
import threading
from ibapi.common import TickAttrib, TickerId
from ibapi.ticktype import TickType, TickTypeEnum
from ibapi.wrapper import *
from ibapi.client import *
import asyncio
from zmq_server import ZmqServer
from order_book import OrderBook
import time

# tickType enum
# 0: "BID_SIZE",
# 1: "BID",
# 2: "ASK",
# 3: "ASK_SIZE",
# 4: "LAST",
# 5: "LAST_SIZE",

# TODO
# Threading for sub/unsub
# Request-reply pattern for Python exchange_connectors
btc = Contract()
btc.conId = 589141846
btc.secType = 'FUT'
btc.exchange = 'CME'
btc.currency = 'USD'


class TWSClient(EClient, EWrapper):
    secType = 'FUT'
    exchange = 'CME'
    currency = 'USD'
    book_type_mapping = {
        "top_of_book": "0",
        "orderbook": "1",
        "tob_snapshot": "2"
    }
    tob = {}

    def __init__(self, zmq_server: ZmqServer):
        EClient.__init__(self, self)
        self.zmq_server = zmq_server
        self.subscribed_tob_req_ids = set()
        self.subscribed_ob_req_ids = set()
        self.ob = OrderBook(max_depth=30)

        self.loop = asyncio.new_event_loop()
        self.async_thread = threading.Thread(
            target=self.start_loop, daemon=True)
        self.async_thread.start()

        self.subscription_queue = asyncio.Queue()
        self.snapshot_queue = asyncio.Queue()

    def start_loop(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def stop_loop(self):
        self.loop.call_soon_threadsafe(self.loop.stop)

    def nextValidId(self, orderId: TickerId):
        # asyncio.run_coroutine_threadsafe(
        #     self.subscription_handler(), self.loop)
        asyncio.run_coroutine_threadsafe(self.snapshot_handler(), self.loop)
        print("I'm returned from nextValidId")

    # Callback on market data price tick
    def tickPrice(self, reqId: int, tickType: int, price: float, attrib: TickAttrib):
        print("TickPrice. TickerId:", reqId, "tickType:", tickType, "Price:", floatMaxString(
            price), "CanAutoExecute:", attrib.canAutoExecute, "PastLimit:", attrib.pastLimit)

        # If inst_id not in tob, add it
        if reqId not in self.tob:
            self.tob[reqId] = {}

        self.tob[reqId][tickType] = price

    # Callback on market data size tick, updates corresponding price tick
    def tickSize(self, reqId: TickerId, tickType: TickerId, size: Decimal):
        (inst_id, book_type) = divmod(reqId, 10)
        # Note: tickSize is guaranteed to happen after a corresponding tickPrice
        # top_of_book does not store tickSize updates, it only stores tickPrice updates, and publishes whenever tickSize updates are received
        if book_type == self.book_type_mapping["top_of_book"]:
            # 0 -> BID_SIZE
            if tickType == 0:
                side = "bid"
                price = self.tob[1]
            # 3 -> ASK_SIZE
            elif tickType == 3:
                side = "ask"
                price = self.tob[2]
            else:
                print("Ignoring unknown tickType in tickSize:", tickType)
                return
            print("TickSize. TickerId:", reqId, "tickType: ",
                  tickType, "Size:", decimalMaxString(size))

            data = f"{side} {price} {decimalMaxString(size)}"
            fut = asyncio.run_coroutine_threadsafe(
                self.zmq_server.publish(inst_id, data), self.loop)
            try:
                fut.result()
            except:
                raise RuntimeError(
                    "Error in publishing tob: ", fut.exception())

        # tob_snapshot stores both tickPrice and tickSize tickTypes - to be send at one go when snapshot ends
        elif book_type == self.book_type_mapping["tob_snapshot"]:
            print("Snapshot tickSize. ReqId:", reqId, "tickType: ",)
            self.tob[reqId][tickType] = size

    # On snapshot end, send the full snapshot info that has been buffered
    def tickSnapshotEnd(self, reqId: TickerId):
        print("Snapshot end. ReqId:", reqId)
        bid = self.tob[reqId][1]
        ask = self.tob[reqId][2]

        fut = asyncio.run_coroutine_threadsafe(
            self.zmq_server.reply(f"{bid} {ask}"), self.loop)
        try:
            fut.result()
        except:
            raise RuntimeError("Error in replying snapshot: ", fut.exception())


    def updateMktDepth(self, reqId: TickerId, position: int, operation: int, side: int, price: float, size: Decimal):
        print("UpdateMarketDepth. ReqId:", reqId, "Position:", position, "Operation:", operation,
              "Side:", side, "Price:", floatMaxString(price), "Size:", decimalMaxString(size))

    def updateMktDepthL2(self, reqId: TickerId, position: int, marketMaker: str, operation: int, side: int, price: float, size: Decimal, isSmartDepth: bool):
        print("UpdateMarketDepthL2. ReqId:", reqId, "Position:", position, "MarketMaker:", marketMaker, "Operation:", operation,
              "Side:", side, "Price:", floatMaxString(price), "Size:", decimalMaxString(size), "isSmartDepth:", isSmartDepth)

    async def subscription_handler(self):
        while True:
            alive_req_ids = set()
            keep_alive_msgs = await self.zmq_server.recv_all_pulls()
            print("Keep alive msgs:", keep_alive_msgs)
            for msg in keep_alive_msgs:
                # Expected ZMQ message format:
                # topic = `<con_id>`
                # data = `<inst_id> <book_type>` Refer to book_type_mapping
                (con_id, inst_id, book_type) = msg.split(" ")
                print("Inst_id: ", int(inst_id))

                # Appends respective postfix, to generate unique req_id for each subscription/snapshot request
                # inst_id is used instead of con_id, as con_id exceeds some hidden upper bound for reqId
                req_id = int(f"{inst_id}{self.book_type_mapping[book_type]}")
                contract = make_contract(conId=int(con_id))

                print("Contract conId:", contract.conId)
                alive_req_ids.add(req_id)

                if book_type == "top_of_book":
                    # Check if subscription already exists
                    if req_id in self.subscribed_tob_req_ids:
                        continue
                    else:
                        self.subscribed_tob_req_ids.add(req_id)
                        print("Subscribing to req_id:", req_id,
                              "for contract:", contract)
                        self.reqMktData(req_id, contract, "", False, False, [])
                elif book_type == "orderbook":
                    if req_id in self.subscribed_ob_req_ids:
                        continue
                    else:
                        self.subscribed_ob_req_ids.add(req_id)
                        self.reqMktDepth(req_id, contract, 30, False, [])
                else:
                    raise ValueError(
                        f"Unknown book_type: {book_type}. Check ZMQ subscription format.")

                print("Alive req_ids:", alive_req_ids)
                print("Subscribed tob req_ids:", self.subscribed_tob_req_ids)
                # Cancel subscriptions for req_ids not in alive_req_ids
                # for req_id in (self.subscribed_tob_req_ids - alive_req_ids):
                #     print("Unsub req_id:", req_id, "for contract:", contract)
                #     self.cancelMktData(req_id)
                #     self.subscribed_tob_req_ids.remove(req_id)

                # for req_id in (self.subscribed_ob_req_ids - alive_req_ids):
                #     self.cancelMktDepth(req_id)
                #     self.subscribed_ob_req_ids.remove(req_id)

    async def snapshot_handler(self):
        while True:
            await self.zmq_server.poll_rep_socket()
            msg = await self.zmq_server.receive_req()
            (inst_id, con_id) = msg.split(" ")

            # Parse message
            req_id = int(f"{inst_id}{self.book_type_mapping['tob_snapshot']}")

            contract = make_contract(conId=int(con_id))

            self.reqMktData(req_id, contract, "", True, False, [])


def make_contract(conId: int, secType: str = 'FUT', exchange: str = 'CME', currency: str = 'USD'):
    print("Making contract with conid:", conId)
    contract = Contract()
    contract.conId = conId
    contract.secType = secType
    contract.exchange = exchange
    contract.currency = currency
    return contract
