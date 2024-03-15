
import threading
from tws_connect import TWSClient
from zmq_server import ZmqServer

def main():
    zmq_server = ZmqServer("tcp://127.0.0.1:5555",
                           "tcp://127.0.0.1:5556", "tcp://127.0.0.1:5557")
    app = TWSClient(zmq_server=zmq_server)
    app.connect("127.0.0.1", 4001, 1000)

    # Start the message loop in the main thread
    app_thread = threading.Thread(target=app.run, daemon=True)
    app_thread.start()

    # subscription_thread = threading.Thread(
    #     target=manage_subscriptions, args=(app, zmq_server))
    # subscription_thread.start()


if __name__ == "__main__":
    main()
