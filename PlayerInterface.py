import os
from collections.abc import Callable
import socket
import json
import sys


class PlayerInterface:
    def __init__(self, decision_callback: Callable):
        if not callable(decision_callback):
            sys.exit("Value Error: decision_callback not callable, expected callable callback")
        self.callback = decision_callback

        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.socket.connect('/tmp/game.sock')

        print(f"[Player {os.environ.get('player_id')}] sending id message...")
        self.__send_message({"player_id": os.environ.get("player_id")})
        # perhaps add ack somewhere here if needed

    def start(self):
        while True:
            game_state = self.__receive_msg()
            decision: bool = self.callback(game_state)
            self.__send_message({"decision": decision})

    def __receive_msg(self) -> dict:
        bytes_buffer = bytearray()
        bytes_read = 0
        while bytes_read < 4:
            data = self.socket.recv(1024)
            bytes_read += len(data)
            bytes_buffer.extend(data)
        message_length = int.from_bytes(bytes_buffer[:4], "big")

        while bytes_read < message_length:
            data = self.socket.recv(message_length - bytes_read)
            bytes_read += len(data)
            bytes_buffer.extend(data)

        return json.loads(bytes_buffer[4:].decode('utf-8'))

    def __send_message(self, message: dict):
        message = json.dumps(message)
        encoded_message = bytearray(message.encode('utf-8'))
        message_length = len(encoded_message)
        encoded_message[0:0] = message_length.to_bytes(4, byteorder='big')
        self.socket.sendall(encoded_message)
